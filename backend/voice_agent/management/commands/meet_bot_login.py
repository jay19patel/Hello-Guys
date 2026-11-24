"""Open Chromium with the persistent Playwright profile so you can sign into your connected Google account once."""

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from crm.models import GoogleAccount
from voice_agent.meet_bot import DEFAULT_PROFILE_DIR


class Command(BaseCommand):
    help = "Sign into your connected Google account in Playwright's persistent browser profile"

    def handle(self, *args, **options):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.stderr.write(self.style.ERROR("Playwright is not installed. Run: pip install playwright && playwright install chromium"))
            return

        profile_dir = os.environ.get("PLAYWRIGHT_USER_DATA_DIR", "").strip() or str(DEFAULT_PROFILE_DIR)
        Path(profile_dir).mkdir(parents=True, exist_ok=True)
        account = GoogleAccount.get_solo()
        email_hint = f" ({account.email})" if account.is_connected and account.email else ""

        self.stdout.write(f"Opening Chromium with persistent profile: {profile_dir}")
        self.stdout.write(f"Please sign into your connected Google account{email_hint}, then close the browser window.")

        with sync_playwright() as p:
            channel = os.environ.get("PLAYWRIGHT_CHANNEL", "chrome").strip() or None
            try:
                ctx = p.chromium.launch_persistent_context(
                    profile_dir,
                    channel=channel,
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
            except Exception:
                ctx = p.chromium.launch_persistent_context(
                    profile_dir,
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://accounts.google.com/", wait_until="domcontentloaded")
            try:
                page.wait_for_event("close", timeout=0)
            except Exception:
                pass
            ctx.close()
        self.stdout.write(self.style.SUCCESS("Google browser profile saved! Headless Meet bots will now join as this account."))
