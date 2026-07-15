"""Email provider abstraction.

A real deployment would wrap SES/Mailgun/Postmark here. For the assessment
the provider is simulated, with hooks to make it fail on demand so the
retry and dead-letter paths can be exercised end to end:

  * message ids starting with ``fail-twice``  fail on attempts 1 and 2,
    then succeed - drives the retry/backoff path
  * message ids starting with ``fail-always`` never succeed - drives the
    dead-letter path
  * anything else succeeds immediately

Attempt counters live in Redis (not process memory) so the behaviour is
identical whether the worker is the in-process test worker or a separate
`celery worker` process being SIGKILL'd during the live demo.
"""
import logging

from . import keys
from .redis_client import get_redis

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Transient provider failure - the caller should retry with backoff."""


class ConsoleEmailProvider:
    """Always succeeds; 'delivery' is a log line."""

    def send(self, message_id, recipient, subject, body):
        logger.info("delivered message_id=%s to=%s subject=%r", message_id, recipient, subject)


class FlakyEmailProvider(ConsoleEmailProvider):
    def send(self, message_id, recipient, subject, body):
        attempt = get_redis().hincrby(keys.FLAKY_ATTEMPTS, message_id, 1)

        if message_id.startswith("fail-always"):
            raise EmailDeliveryError(f"{message_id}: provider rejected (permanent test failure)")
        if message_id.startswith("fail-twice") and attempt <= 2:
            raise EmailDeliveryError(f"{message_id}: provider timeout (attempt {attempt})")

        super().send(message_id, recipient, subject, body)
