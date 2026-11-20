from django.core.management.base import BaseCommand
from meta_connector.services.maintenance import retry_webhooks


class Command(BaseCommand):
    help = "Re-process webhooks that failed or got stuck (same as the dashboard's Retry button). Safe to run from cron."

    def handle(self, *args, **options):
        fixed, failing = retry_webhooks()
        self.stdout.write(self.style.SUCCESS(f"Processed {fixed} webhooks; {failing} still failing (see Django Admin → Webhook logs)."))
