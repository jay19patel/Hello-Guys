"""Pull changes made in Google into the CRM: meetings (reschedule, cancel, rename, guests' RSVPs) and Drive
files (renamed, trashed).

python manage.py crm_google_sync          # loop forever, every 5 minutes (run ONE)
python manage.py crm_google_sync --once   # single pass (cron / tests)
"""

import logging
import time

from django.core.management.base import BaseCommand

from crm import google
from crm.models import GoogleAccount

logger = logging.getLogger(__name__)

EVERY_SECONDS = 300


class Command(BaseCommand):
    help = "Pull Google Calendar and Drive changes into the CRM"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one pass and exit")

    def handle(self, *args, once: bool = False, **options):
        while True:
            try:
                if GoogleAccount.get_solo().is_connected:
                    meetings, files = google.pull_meetings(), google.pull_files()
                    if meetings or files:
                        logger.info("From Google: %s meeting(s), %s Drive item(s) updated", meetings, files)
            except Exception:  # Google/DB blip: log and try again next pass, never exit
                logger.exception("Google pull failed")
            if once:
                return
            time.sleep(EVERY_SECONDS)
