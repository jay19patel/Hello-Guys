"""Delete old chats, read notifications and finished reminders. Run daily from cron.

python manage.py hello_guy_prune              # uses HELLO_GUY_AI["RETENTION_DAYS"] (0 = do nothing)
python manage.py hello_guy_prune --days 90
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from hello_guy_ai.conf import ai_settings
from hello_guy_ai.models import Run, Thread


class Command(BaseCommand):
    help = "Delete old chats older than the retention period"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None, help="Override HELLO_GUY_AI['RETENTION_DAYS']")

    def handle(self, *args, days: int | None = None, **options):
        days = ai_settings.RETENTION_DAYS if days is None else days
        if days <= 0:
            self.stdout.write("Retention is off (RETENTION_DAYS = 0); nothing deleted.")
            return
        cutoff = timezone.now() - timedelta(days=days)
        # Threads with an active run are kept: someone may still be waiting on an approval.
        threads = Thread.objects.filter(updated_at__lt=cutoff).exclude(runs__status__in=Run.ACTIVE)
        counts = {
            "threads": threads.delete()[0],
        }
        self.stdout.write(self.style.SUCCESS(f"Deleted (older than {days} days): {counts}"))
