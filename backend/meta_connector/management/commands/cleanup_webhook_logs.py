from django.core.management.base import BaseCommand
from meta_connector.services.maintenance import cleanup_webhook_logs


class Command(BaseCommand):
    help = "Delete webhook logs older than the retention set in Dashboard → Settings (also runs automatically once a day)."

    def handle(self, *args, **options):
        deleted = cleanup_webhook_logs(force=True)
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} old webhook logs."))
