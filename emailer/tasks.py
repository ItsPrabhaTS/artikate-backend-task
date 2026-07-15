import logging
import random
import time
import uuid

from celery import shared_task
from django.conf import settings
from django.utils.module_loading import import_string

from . import dead_letter, keys
from .providers import EmailDeliveryError
from .rate_limiter import RateLimiterUnavailable, SlidingWindowRateLimiter
from .redis_client import get_redis

logger = logging.getLogger(__name__)


def _limiter():
    return SlidingWindowRateLimiter(
        get_redis(),
        key=keys.RATE_LIMITER,
        limit=settings.EMAIL_RATE_LIMIT,
        window_seconds=settings.EMAIL_RATE_WINDOW_SECONDS,
        # every granted permit is one provider call; the audit trail is
        # written by the same Lua script that grants, so it is exact
        audit_key=keys.ATTEMPT_LOG,
    )


def _provider():
    return import_string(settings.EMAIL_PROVIDER)()


def _backoff_seconds(attempt):
    """Exponential backoff with jitter: base * 2^(attempt-1), capped, then
    scaled by a random factor in [0.5, 1.0] so a burst of failures doesn't
    come back as a synchronised burst of retries."""
    delay = min(
        settings.EMAIL_RETRY_BACKOFF_MAX_SECONDS,
        settings.EMAIL_RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
    )
    return delay * (0.5 + random.random() / 2)


@shared_task(bind=True, name="emailer.send_email", max_retries=None)
def send_email(self, message_id, recipient, subject, body, attempt=1):
    """Send one transactional email, respecting the provider rate limit.

    Two retry paths, deliberately kept apart:

    * throttled - not a failure. The task reschedules itself for when the
      limiter says a slot frees, and the failure counter does NOT move.
    * provider failure - exponential backoff with jitter; after
      EMAIL_MAX_SEND_ATTEMPTS the job is dead-lettered, never dropped.

    ``max_retries=None`` because Celery's built-in retry counter would
    conflate those two paths; the explicit ``attempt`` kwarg counts only
    real provider failures.

    Crash safety comes from settings: with task_acks_late +
    task_reject_on_worker_lost the broker message survives a SIGKILL'd
    worker and is redelivered (see ANSWERS.md section 2). The trade-off is
    at-least-once delivery: a worker killed between the provider call and
    the ack means the email can be sent twice.
    """
    try:
        granted, retry_after = _limiter().try_acquire(
            f"{message_id}:{attempt}:{uuid.uuid4().hex[:8]}"
        )
    except RateLimiterUnavailable as exc:
        # Fail closed: if Redis can't vouch for the window, we must not call
        # the provider. Park the task and let it try again shortly.
        logger.warning("rate limiter unavailable, holding %s: %s", message_id, exc)
        raise self.retry(countdown=settings.EMAIL_RETRY_BACKOFF_BASE_SECONDS)

    if not granted:
        # Small jitter on top of the limiter's hint - every throttled task
        # is told about the *same* freed slot, only one of them can win it.
        jitter = random.random() * min(0.1 * settings.EMAIL_RATE_WINDOW_SECONDS, 1.0)
        raise self.retry(countdown=retry_after + jitter)

    try:
        _provider().send(message_id, recipient, subject, body)
    except EmailDeliveryError as exc:
        if attempt >= settings.EMAIL_MAX_SEND_ATTEMPTS:
            logger.error("dead-lettering %s after %d attempts: %s", message_id, attempt, exc)
            dead_letter.push(
                message={
                    "message_id": message_id,
                    "recipient": recipient,
                    "subject": subject,
                    "body": body,
                },
                error=str(exc),
                attempts=attempt,
            )
            return {"status": "dead_lettered", "message_id": message_id, "attempts": attempt}

        delay = _backoff_seconds(attempt)
        logger.warning(
            "send failed for %s (attempt %d/%d), retrying in %.2fs: %s",
            message_id, attempt, settings.EMAIL_MAX_SEND_ATTEMPTS, delay, exc,
        )
        raise self.retry(
            countdown=delay,
            kwargs={
                "message_id": message_id,
                "recipient": recipient,
                "subject": subject,
                "body": body,
                "attempt": attempt + 1,
            },
        )

    get_redis().zadd(keys.SENT, {message_id: int(time.time() * 1000)})
    return {"status": "sent", "message_id": message_id, "attempts": attempt}
