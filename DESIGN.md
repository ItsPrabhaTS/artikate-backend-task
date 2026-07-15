# DESIGN — Section 2: Rate-Limited Async Job Queue

Requirements driving every decision below: bursts of 2,000 requests in ~10s,
a hard provider ceiling of 200 emails/minute, retries on failure, and no job
loss on worker crash.

## 1. Architecture choice

Three options considered:

**Celery + Redis (chosen).**
Pros: mature ack semantics (`task_acks_late`, `task_reject_on_worker_lost`)
give crash-safety without me re-implementing message recovery; countdown/ETA
scheduling gives retries and throttling "park until later" for free; the
worker model scales horizontally. Cons: real operational surface (visibility
timeouts, prefetch tuning are things you must actually understand — see
ANSWERS.md §2), and the Redis broker is not a durable log — a Redis crash
between fsyncs can lose queued messages (mitigable with AOF `appendfsync
always`, or RabbitMQ if that guarantee becomes contractual).

**Django-Q2.**
Simpler to operate and can use the ORM as broker, which removes Redis as a
dependency. But retry semantics are coarser (no per-task countdown-on-retry
pattern as natural as Celery's), the ecosystem is far smaller, and an ORM
broker turns every enqueue into a database write — at 2,000 jobs/10s that
puts burst load exactly where I don't want it.

**Custom worker (BRPOPLPUSH loop).**
Full control and great for understanding, but I would be re-implementing
acknowledgement, heartbeat/visibility, ETA scheduling, and graceful shutdown
— precisely the bugs Celery has already fixed. Not defensible for
production email delivery.

The assessment also mandates Celery+Redis for the implementation, but the
trade-offs above are why I would land there anyway. One deliberate boundary:
Celery may retry and schedule, but **the rate limit itself lives in Redis,
not in Celery** — `celery_app`'s built-in `rate_limit` option is per-worker,
so two workers at `100/m` each would happily do 200/m against a provider
that counts globally.

## 2. The rate limiter (`emailer/rate_limiter.py`)

**Choice: sliding window over a Redis sorted set — Option B.**

* *Fixed window (INCR + EXPIRE)* is the cheapest, but admits 2x the limit
  across a boundary: 200 sends at 11:59:59 and 200 more at 12:00:01 are both
  "within their window" while the provider sees 400 in two seconds. With a
  provider that enforces its own limit, boundary bursts turn into hard
  rejections, so this is disqualified.
* *Token bucket (DECR + TTL refill)* enforces a smooth rate and allows
  controlled bursts, but needs refill bookkeeping (last-refill timestamp,
  fractional tokens) and its burst allowance is exactly what I don't want —
  the provider's contract is "no more than 200 in any rolling minute".
* *Sliding window (ZSET + ZREMRANGEBYSCORE)* models that contract literally:
  one sorted-set member per granted send, scored by timestamp; evict what
  aged out, count, admit if under the limit. Memory cost is one member per
  send in the window (≤200) — trivial.

**Atomicity: a single Lua script.** The grant decision is
check-then-act (`ZCARD` then conditional `ZADD`), and the decision depends on
the intermediate read. `MULTI`/`EXEC` cannot branch on a value read inside
the transaction, and a pipeline is just batching — neither closes the race
where two workers both read 199 and both add. Redis executes a Lua script
atomically (the server is single-threaded), so the read and the conditional
write are one indivisible unit. The same script also returns *when the
oldest permit expires*, so a throttled task reschedules itself for the
moment a slot actually frees instead of polling.

**Clocks are part of correctness.** Workers read `time.time()` *before*
entering the script, so timestamps can arrive out of order (thread
scheduling now, host clock drift later). A stale `now` running the eviction
after a fresher one would undercount the window and over-admit. The script
therefore clamps `now` to a per-key high-water mark: time, as the limiter
sees it, never goes backwards. There is a regression test that replays the
exact out-of-order sequence
(`test_out_of_order_caller_clocks_cannot_breach_the_limit`), because this
started as a real intermittent failure of the 500-job test, not a
theoretical concern.

**Redis failure: fail closed.** If Redis cannot vouch for the window, the
task does not call the provider; it parks itself and retries
(`RateLimiterUnavailable` → retry with countdown). A minute of delayed email
is recoverable; blowing the provider's limit risks hard rejections or
account suspension, and "Redis is down" usually also means "the broker is
down", so throughput is already gone. The cost of failing closed is that
email delivery now has Redis as a hard availability dependency — accepted.

## 3. Retries and dead-lettering (`emailer/tasks.py`)

Two retry paths are deliberately kept separate:

* **Throttled** — not a failure. Retry when the limiter says a slot frees
  (plus jitter, since every throttled task is told about the same slot and
  only one can win it). Does not consume a failure attempt.
* **Provider failure** — exponential backoff with jitter
  (`base * 2^(attempt-1)`, capped, scaled by random [0.5, 1.0] so a burst of
  failures doesn't return as a synchronized burst of retries). After
  `EMAIL_MAX_SEND_ATTEMPTS` the job goes to the dead-letter list with its
  payload, error and attempt count — inspectable and replayable via
  `manage.py dead_letter [--requeue]`. Nothing is ever silently dropped.

The task runs with `max_retries=None` and carries an explicit `attempt`
kwarg, because Celery's built-in retry counter would conflate the two paths
(a job throttled 30 times during a flash sale must not be dead-lettered for
it).

## 4. Crash safety

`task_acks_late = True` + `task_reject_on_worker_lost = True` +
`worker_prefetch_multiplier = 1`. The message is acknowledged only *after*
the task finishes, so a SIGKILL'd worker leaves it unacked and Redis
redelivers it after the visibility timeout. Full mechanics — including why
this means at-least-once delivery and where a duplicate send can sneak in —
are in ANSWERS.md §2 (the SIGKILL question).

## 5. Known limitations

* **At-least-once, not exactly-once.** A worker killed between the provider
  call and the ack causes a duplicate send on redelivery. The fix is an
  idempotency key checked on the provider side (most transactional email
  APIs accept one); dedupe inside our own system can only shrink the window,
  not close it.
* **The audit log (`emailer:attempts`) grows unbounded.** It exists so the
  tests can prove the limit was never exceeded; production would trim it
  (or not write it at all).
* **Single Redis.** Broker, limiter and dead letters share one instance —
  fine at this scale; at real scale I would at least split limiter state
  from the broker so a broker flush cannot reset rate accounting.
* **Throttled tasks re-enter the queue** rather than waiting in a separate
  delayed queue; under sustained overload the same messages cycle through
  the worker. Acceptable at 2k bursts; a dedicated "scheduled" ZSET would be
  the next step.
