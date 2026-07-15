import time

from django.conf import settings
from django.core.management.base import BaseCommand

from emailer import keys
from emailer.redis_client import get_redis


class Command(BaseCommand):
    help = "Show live queue/rate-limiter/dead-letter state from Redis."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true",
                            help="Refresh every second until Ctrl+C.")

    def handle(self, *args, **options):
        try:
            while True:
                self.stdout.write(self._snapshot())
                if not options["watch"]:
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    def _snapshot(self):
        r = get_redis()
        window_ms = int(settings.EMAIL_RATE_WINDOW_SECONDS * 1000)
        now_ms = int(time.time() * 1000)

        queued = r.llen("celery")
        sent = r.zcard(keys.SENT)
        dead = r.llen(keys.DEAD_LETTER)
        attempts = r.zcard(keys.ATTEMPT_LOG)
        in_window = r.zcount(keys.RATE_LIMITER, now_ms - window_ms, now_ms)

        return (
            f"{time.strftime('%H:%M:%S')}  "
            f"queued={queued:<5} sent={sent:<5} provider_calls={attempts:<5} "
            f"dead_letter={dead:<3} "
            f"window={in_window}/{settings.EMAIL_RATE_LIMIT} "
            f"(last {settings.EMAIL_RATE_WINDOW_SECONDS:.0f}s)"
        )
