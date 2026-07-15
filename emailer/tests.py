import time

import fakeredis
import pytest
import redis as redis_lib
from celery.contrib.testing.worker import start_worker
from django.conf import settings
from django.test import override_settings

from config import celery_app

from . import dead_letter, keys
from .rate_limiter import RateLimiterUnavailable, SlidingWindowRateLimiter
from .redis_client import get_redis
from .tasks import send_email

# ---------------------------------------------------------------------------
# Rate limiter unit tests (fakeredis + injected clock: fast and deterministic)
# ---------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def limiter(clock):
    client = fakeredis.FakeRedis(decode_responses=True)
    return SlidingWindowRateLimiter(
        client, key="test:rate", limit=3, window_seconds=10, clock=clock
    )


def test_grants_up_to_the_limit_then_denies(limiter):
    grants = [limiter.try_acquire(f"m{i}")[0] for i in range(4)]
    assert grants == [True, True, True, False]


def test_denial_reports_when_a_slot_actually_frees(limiter, clock):
    for i in range(3):
        limiter.try_acquire(f"m{i}")
        clock.now += 1  # permits at t, t+1, t+2

    granted, retry_after = limiter.try_acquire("m3")  # asked at t+3
    assert not granted
    # the oldest permit (t) leaves the 10s window at t+10, i.e. in 7s
    assert retry_after == pytest.approx(7.0, abs=0.01)


def test_window_slides_rather_than_resetting(clock):
    limiter = SlidingWindowRateLimiter(
        fakeredis.FakeRedis(decode_responses=True),
        key="test:rate", limit=2, window_seconds=10, clock=clock,
    )
    assert limiter.try_acquire("a")[0]
    clock.now += 5
    assert limiter.try_acquire("b")[0]
    clock.now += 4  # t+9: both permits still inside the 10s window
    assert not limiter.try_acquire("c")[0]

    clock.now += 1.5  # t+10.5: "a" has aged out, "b" (t+5) has not
    assert limiter.try_acquire("d")[0]
    assert not limiter.try_acquire("e")[0]


def test_limit_is_never_briefly_double_at_a_boundary(limiter, clock):
    """The failure mode of fixed windows: N sends late in one window plus N
    early in the next. A sliding window must refuse the second burst."""
    for i in range(3):
        assert limiter.try_acquire(f"first-{i}")[0]
    clock.now += 9.9  # just over a fixed-window boundary, inside the sliding one
    assert not limiter.try_acquire("second-0")[0]


def test_out_of_order_caller_clocks_cannot_breach_the_limit(clock):
    """Two workers read time.time() before entering the Lua script, so the
    script can see timestamps arrive out of order. Replay the sequence that
    would over-admit without the high-water-mark clamp: a fresh call trims
    permits that a stale caller's window still needs to count."""
    client = fakeredis.FakeRedis(decode_responses=True)
    limiter = SlidingWindowRateLimiter(
        client, key="test:rate", limit=2, window_seconds=10,
        clock=clock, audit_key="test:audit",
    )

    clock.now = 95.2
    assert limiter.try_acquire("m1")[0]
    clock.now = 95.5
    assert limiter.try_acquire("m2")[0]
    clock.now = 105.6  # trims m1 and m2 out of the limiter's zset
    assert limiter.try_acquire("m3")[0]
    clock.now = 105.0  # stale thread: its (95.0, 105.0] window still holds m1+m2

    limiter.try_acquire("m4")  # granted, but clamped to 105.6 - never to 105.0

    # invariant over every provider call ever made: no rolling 10s window
    # may contain more than `limit` calls
    calls = sorted(score for _, score in client.zrange("test:audit", 0, -1, withscores=True))
    for i in range(len(calls) - limiter.limit):
        assert calls[i + limiter.limit] - calls[i] >= limiter.window_ms


def test_fails_closed_when_redis_is_unreachable(clock):
    dead_client = redis_lib.Redis(
        host="localhost", port=1, socket_connect_timeout=0.1, socket_timeout=0.1
    )
    limiter = SlidingWindowRateLimiter(
        dead_client, key="test:rate", limit=3, window_seconds=10, clock=clock
    )
    with pytest.raises(RateLimiterUnavailable):
        limiter.try_acquire("m0")


# ---------------------------------------------------------------------------
# End-to-end queue tests (real Redis broker + a real in-process Celery worker)
# ---------------------------------------------------------------------------


def _redis_available():
    try:
        redis_lib.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1).ping()
        return True
    except Exception:
        return False


requires_redis = pytest.mark.skipif(
    not _redis_available(),
    reason=f"Redis not reachable at {settings.REDIS_URL} - run `docker compose up -d redis`",
)


def _reset_queue_state():
    r = get_redis()
    r.delete(*keys.ALL, "celery", "unacked", "unacked_index")
    stale_results = list(r.scan_iter("celery-task-meta-*"))
    if stale_results:
        r.delete(*stale_results)


def _wait_until(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.25)
    return predicate()


# Shrunk knobs so 500 jobs drain in seconds instead of minutes; the mechanism
# under test (sliding window, backoff, dead-letter) is identical.
FAST_QUEUE = dict(
    EMAIL_RATE_LIMIT=50,
    EMAIL_RATE_WINDOW_SECONDS=1.0,
    EMAIL_MAX_SEND_ATTEMPTS=4,
    EMAIL_RETRY_BACKOFF_BASE_SECONDS=0.2,
    EMAIL_RETRY_BACKOFF_MAX_SECONDS=1.0,
)


@pytest.mark.redis
@requires_redis
@override_settings(**FAST_QUEUE)
def test_500_jobs_none_lost_rate_respected_and_failure_retried():
    _reset_queue_state()
    r = get_redis()

    message_ids = [f"msg-{i:04d}" for i in range(499)] + ["fail-twice-poison"]

    with start_worker(
        celery_app, pool="threads", concurrency=8,
        perform_ping_check=False, loglevel="error",
    ):
        for message_id in message_ids:
            send_email.delay(
                message_id=message_id,
                recipient="customer@example.com",
                subject=f"Order confirmation {message_id}",
                body="Thanks for your order!",
            )
        _wait_until(lambda: r.zcard(keys.SENT) >= len(message_ids), timeout=120)

    # 1. No job lost: every id delivered, exactly once (ZADD dedupes members,
    #    so 500 members == 500 distinct delivered ids).
    sent_ids = set(r.zrange(keys.SENT, 0, -1))
    assert sent_ids == set(message_ids)

    # 2. The rate limit was never exceeded. The audit log is written by the
    #    same atomic Lua script that grants permits, so this checks every
    #    provider call ever made: no rolling window may hold more than
    #    `limit` calls, i.e. calls i and i+limit must be >= window apart.
    calls = sorted(score for _, score in r.zrange(keys.ATTEMPT_LOG, 0, -1, withscores=True))
    limit = FAST_QUEUE["EMAIL_RATE_LIMIT"]
    window_ms = FAST_QUEUE["EMAIL_RATE_WINDOW_SECONDS"] * 1000
    violations = [
        (i, calls[i + limit] - calls[i])
        for i in range(len(calls) - limit)
        if calls[i + limit] - calls[i] < window_ms
    ]
    assert violations == []
    # sanity: the retried job means more provider calls than jobs
    assert len(calls) == len(message_ids) + 2

    # 3. The poisoned job failed twice, was retried with growing backoff and
    #    was finally delivered.
    assert int(r.hget(keys.FLAKY_ATTEMPTS, "fail-twice-poison")) == 3
    poison_calls = sorted(
        score
        for member, score in r.zrange(keys.ATTEMPT_LOG, 0, -1, withscores=True)
        if member.startswith("fail-twice-poison:")
    )
    assert len(poison_calls) == 3
    first_gap = poison_calls[1] - poison_calls[0]
    second_gap = poison_calls[2] - poison_calls[1]
    # jittered exponential backoff: attempt 1 waits >= 0.5*base, attempt 2 >= base
    assert first_gap >= 0.5 * FAST_QUEUE["EMAIL_RETRY_BACKOFF_BASE_SECONDS"] * 1000 * 0.9
    assert second_gap >= FAST_QUEUE["EMAIL_RETRY_BACKOFF_BASE_SECONDS"] * 1000 * 0.9


@pytest.mark.redis
@requires_redis
@override_settings(**{**FAST_QUEUE, "EMAIL_RETRY_BACKOFF_BASE_SECONDS": 0.05})
def test_permanently_failing_job_is_dead_lettered_not_dropped():
    _reset_queue_state()
    r = get_redis()

    with start_worker(
        celery_app, pool="threads", concurrency=2,
        perform_ping_check=False, loglevel="error",
    ):
        send_email.delay(
            message_id="fail-always-doomed",
            recipient="customer@example.com",
            subject="This will never send",
            body="...",
        )
        assert _wait_until(lambda: r.llen(keys.DEAD_LETTER) == 1, timeout=30)

    entries = dead_letter.entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["message"]["message_id"] == "fail-always-doomed"
    assert entry["attempts"] == FAST_QUEUE["EMAIL_MAX_SEND_ATTEMPTS"]
    assert "provider rejected" in entry["error"]

    # dead-lettered, not delivered - and not silently dropped either
    assert r.zscore(keys.SENT, "fail-always-doomed") is None
