import time

from django.core.management.base import BaseCommand

from emailer.tasks import send_email


class Command(BaseCommand):
    help = (
        "Enqueue a burst of demo emails (with a few deliberate failures) to "
        "watch the queue, rate limiter and retries live. Run a worker first:\n"
        "  celery -A config worker -l info --pool=threads --concurrency=8"
    )

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=120)
        parser.add_argument("--fail-twice", type=int, default=2,
                            help="How many messages should fail twice then succeed.")
        parser.add_argument("--fail-always", type=int, default=1,
                            help="How many messages should fail permanently (dead-letter).")

    def handle(self, *args, **options):
        batch = int(time.time())
        submitted = 0

        def enqueue(message_id):
            nonlocal submitted
            send_email.delay(
                message_id=message_id,
                recipient="customer@example.com",
                subject=f"Order confirmation {message_id}",
                body="Thanks for your order!",
            )
            submitted += 1

        for i in range(options["fail_twice"]):
            enqueue(f"fail-twice-{batch}-{i}")
        for i in range(options["fail_always"]):
            enqueue(f"fail-always-{batch}-{i}")
        for i in range(options["count"] - submitted):
            enqueue(f"demo-{batch}-{i:04d}")

        self.stdout.write(self.style.SUCCESS(f"Enqueued {submitted} jobs (batch {batch})."))
        self.stdout.write(
            "\nWatch it drain:\n"
            "  python manage.py queue_stats --watch\n"
            "\nOr poke Redis directly:\n"
            "  docker compose exec redis redis-cli LLEN celery          # queued\n"
            "  docker compose exec redis redis-cli ZCARD emailer:sent   # delivered\n"
            "  docker compose exec redis redis-cli LRANGE emailer:dead_letter 0 -1"
        )
