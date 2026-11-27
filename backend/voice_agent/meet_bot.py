"""Self-Hosted Playwright Google Meet Bot Worker.

Joins a Google Meet URL in Chromium, injects a WebSocket audio bridge to the Pipecat worker,
and exits cleanly when the session is stopped from the CRM UI.

Usage:
    python -m voice_agent.meet_bot --session-id <uuid>
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from asgiref.sync import sync_to_async

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_ENV", "local")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.utils import timezone  # noqa: E402
from crm.models import GoogleAccount  # noqa: E402
from voice_agent.models import VoiceBotSession  # noqa: E402
from voice_agent.services import build_ws_url, finalize_session_to_crm  # noqa: E402

DEFAULT_PROFILE_DIR = BACKEND_DIR / "media" / "playwright_google_profile"

logger = logging.getLogger(__name__)

# Injected into the Google Meet page to bridge <audio> elements <-> Pipecat WebSocket.
AUDIO_BRIDGE_JS = """
(wsUrl) => {
    if (window.__hgAudioBridge) return;
    const ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';
    window.__hgAudioBridge = ws;

    ws.onopen = () => {
        const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        const processor = ctx.createScriptProcessor(4096, 1, 1);
        const attached = new WeakSet();

        const attachStreams = () => {
            document.querySelectorAll('audio, video').forEach(el => {
                if (attached.has(el) || !el.srcObject) return;
                try {
                    const src = ctx.createMediaStreamSource(el.srcObject);
                    src.connect(processor);
                    attached.add(el);
                } catch (e) {}
            });
        };

        processor.onaudioprocess = (ev) => {
            if (ws.readyState !== WebSocket.OPEN) return;
            const input = ev.inputBuffer.getChannelData(0);
            const pcm = new Int16Array(input.length);
            for (let i = 0; i < input.length; i++) {
                const s = Math.max(-1, Math.min(1, input[i]));
                pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
            }
            ws.send(pcm.buffer);
        };
        processor.connect(ctx.destination);
        attachStreams();
        setInterval(attachStreams, 3000);
    };
}
"""


async def run_meet_bot(session_id: str) -> None:
    """Launch Chromium, join Google Meet, and bridge audio until session completes."""
    session = await sync_to_async(VoiceBotSession.objects.filter(pk=session_id).first)()
    if session is None:
        logger.error("VoiceBotSession %s not found.", session_id)
        return

    def _log(msg: str, status: str | None = None, error: str | None = None) -> None:
        session.append_log(msg)
        fields = ["connection_logs", "updated_at"]
        if status:
            session.status = status
            fields.append("status")
        if error is not None:
            session.error_message = error[:500]
            fields.append("error_message")
        session.save(update_fields=fields)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        await sync_to_async(_log)(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium",
            status=VoiceBotSession.Status.FAILED,
            error="Playwright not installed.",
        )
        return

    ws_url = build_ws_url(session)
    default_headless = "false" if os.environ.get("DJANGO_ENV", "local") == "local" else "true"
    headless = os.environ.get("PLAYWRIGHT_HEADLESS", default_headless).lower() != "false"
    user_data_dir = os.environ.get("PLAYWRIGHT_USER_DATA_DIR", "").strip() or str(DEFAULT_PROFILE_DIR)
    Path(user_data_dir).mkdir(parents=True, exist_ok=True)
    google_acc = await sync_to_async(GoogleAccount.get_solo)()
    if google_acc.is_connected and google_acc.email:
        await sync_to_async(_log)(f"Using connected Google account ({google_acc.email}) browser profile.")

    try:
        async with async_playwright() as p:
            launch_args = [
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ]
            channel = os.environ.get("PLAYWRIGHT_CHANNEL", "chrome").strip() or None
            try:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir,
                    channel=channel,
                    headless=headless,
                    args=launch_args,
                    permissions=["microphone"],
                )
            except Exception:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir,
                    headless=headless,
                    args=launch_args,
                    permissions=["microphone"],
                )
            page = context.pages[0] if context.pages else await context.new_page()

            await sync_to_async(_log)(f"Opening Google Meet: {session.meeting_url}")
            await page.goto(session.meeting_url, wait_until="domcontentloaded", timeout=30000)

            # Wait up to 120s for either auto-join or manual sign-in/join in the visible browser window
            leave_btn = page.locator('button[aria-label*="Leave call" i]').first
            joined_clicked = False
            for _ in range(60):
                if await leave_btn.count() > 0 and await leave_btn.is_visible():
                    break
                for dismiss_sel in (
                    'button:has-text("Continue without")',
                    'button:has-text("Got it")',
                    'button:has-text("Dismiss")',
                ):
                    d_btn = page.locator(dismiss_sel).first
                    if await d_btn.count() > 0 and await d_btn.is_visible():
                        try:
                            await d_btn.click(timeout=1500)
                        except Exception as exc:
                            logger.debug("Dismiss button click skipped: %s", exc)

                name_input = page.locator('input[placeholder*="name" i], input[aria-label*="name" i]').first
                if await name_input.count() > 0 and await name_input.is_visible():
                    try:
                        if not (await name_input.input_value()).strip():
                            await name_input.fill(session.bot_name)
                    except Exception as exc:
                        logger.debug("Name input fill skipped: %s", exc)

                if not joined_clicked:
                    for join_sel in (
                        'button:has-text("Join now"):not([disabled])',
                        'button:has-text("Ask to join"):not([disabled])',
                        'button:has-text("Switch here"):not([disabled])',
                    ):
                        j_btn = page.locator(join_sel).first
                        if await j_btn.count() > 0 and await j_btn.is_visible():
                            await j_btn.click()
                            joined_clicked = True
                            await sync_to_async(_log)("Clicked Join. Waiting to enter the meeting...")
                            break

                await asyncio.sleep(2)

            if not (await leave_btn.count() > 0 and await leave_btn.is_visible()):
                shot_path = BACKEND_DIR / "media" / f"meet_bot_{session_id}.png"
                await page.screenshot(path=str(shot_path))
                title = await page.title()
                raise RuntimeError(
                    f"Timed out waiting to enter Meet on '{title}' ({page.url}). "
                    f"Please sign in or click Join in the browser window."
                )

            await sync_to_async(_log)(
                "Bot is LIVE inside the Google Meet!",
                status=VoiceBotSession.Status.IN_MEETING,
            )

            await page.evaluate(AUDIO_BRIDGE_JS, ws_url)
            await sync_to_async(_log)(f"Audio bridge active -> {ws_url}")

            # Stay in meeting until stopped from CRM UI or meeting ends
            while True:
                await asyncio.sleep(2)
                latest_status = await sync_to_async(
                    lambda: VoiceBotSession.objects.filter(pk=session_id).values_list("status", flat=True).first()
                )()
                if latest_status in (VoiceBotSession.Status.COMPLETED, VoiceBotSession.Status.FAILED, None):
                    break

            await context.close()
            await sync_to_async(finalize_session_to_crm)(session)
    except Exception as exc:
        logger.exception("Self-hosted Playwright Meet bot failed for %s", session_id)
        await sync_to_async(_log)(
            f"Playwright bot error: {exc}",
            status=VoiceBotSession.Status.FAILED,
            error=str(exc),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Self-Hosted Playwright Google Meet Bot")
    parser.add_argument("--session-id", required=True, help="VoiceBotSession UUID")
    args = parser.parse_args()
    asyncio.run(run_meet_bot(args.session_id))


if __name__ == "__main__":
    main()
