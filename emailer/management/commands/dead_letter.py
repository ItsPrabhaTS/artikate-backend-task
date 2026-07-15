import json

from django.core.management.base import BaseCommand

from emailer import dead_letter
from emailer.tasks import send_email


class Command(BaseCommand):
    help = "Inspect the dead-letter queue, or requeue everything in it."

    def add_arguments(self, parser):
        parser.add_argument("--requeue", action="store_true",
                            help="Resubmit every dead-lettered job as a fresh task.")

    def handle(self, *args, **options):
        if not options["requeue"]:
            items = dead_letter.entries()
            if not items:
                self.stdout.write("Dead-letter queue is empty.")
                return
            for entry in items:
                self.stdout.write(json.dumps(entry, indent=2))
            self.stdout.write(f"\n{len(items)} entries.")
            return

        requeued = 0
        while (entry := dead_letter.pop()) is not None:
            message = entry["message"]
            send_email.delay(
                message_id=message["message_id"],
                recipient=message["recipient"],
                subject=message["subject"],
                body=message["body"],
            )
            requeued += 1
        self.stdout.write(self.style.SUCCESS(f"Requeued {requeued} jobs."))
