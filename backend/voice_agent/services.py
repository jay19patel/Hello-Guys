"""Services for AI Voice Bot sessions, MeetingBaas/Recall/Telephony bridges, and CRM logging."""

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from crm import google as crm_google
from crm.models import Activity, Customer, GoogleAccount, Meeting, Opportunity
from voice_agent.models import VoiceBotSession, normalize_meeting_url

logger = logging.getLogger(__name__)


class VoiceBotError(Exception):
    """Raised when starting or stopping a voice bot session fails."""


def build_ws_url(session: VoiceBotSession) -> str:
    """Build the wss:// Pipecat WebSocket endpoint URL for a session."""
    base = os.environ.get("PIPECAT_WS_BASE_URL", "ws://localhost:8001").rstrip("/")
    return f"{base}/ws/voice-bot/{session.id}"


def build_crm_system_prompt(
    *,
    customer: Customer | None = None,
    opportunity: Opportunity | None = None,
    custom_prompt: str = "",
) -> str:
    """Auto-enrich the voice bot system prompt with CRM lead/customer context."""
    parts = [
        "You are a helpful, concise, and professional AI voice assistant representing our company.",
        "Speak naturally in short conversational sentences suitable for a live voice call.",
    ]
    if customer is not None:
        ctx = [f"Client Name: {customer.name}"]
        if customer.company:
            ctx.append(f"Company: {customer.company}")
        if customer.email:
            ctx.append(f"Email: {customer.email}")
        if customer.phone:
            ctx.append(f"Phone: {customer.phone}")
        if customer.notes:
            ctx.append(f"CRM Notes: {customer.notes[:400]}")
        recent_notes = list(
            customer.activities.order_by("-created_at").values_list("kind", "body")[:5]
        )
        if recent_notes:
            formatted = "; ".join(f"[{k}] {b[:140]}" for k, b in recent_notes)
            ctx.append(f"Recent CRM Activity: {formatted}")
        parts.append("CRM Client Profile:\n" + "\n".join(ctx))

    if opportunity is not None:
        services = ", ".join(s.name for s in opportunity.service_list) or "General"
        opp_ctx = (
            f"Active Opportunity: {opportunity.title} (Stage: {opportunity.get_status_display()}, "
            f"Services: {services})"
        )
        if opportunity.details:
            opp_ctx += f"\nDetails: {opportunity.details[:400]}"
        parts.append(opp_ctx)

    if custom_prompt.strip():
        parts.append(f"Call Instructions:\n{custom_prompt.strip()}")

    return "\n\n".join(parts)


def _trigger_provider_start(session: VoiceBotSession, ws_url: str) -> tuple[str, str]:
    """Call the external meeting/telephony provider or launch the self-hosted Playwright bot.

    Returns (external_bot_id, log_message).
    """
    provider = session.provider
    if provider == VoiceBotSession.Provider.SELF_HOSTED:
        backend_dir = Path(__file__).resolve().parent.parent
        log_dir = backend_dir / "media"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / f"meet_bot_{session.id}.log", "ab")
        env = os.environ.copy()
        env["DJANGO_ENV"] = "local" if settings.DEBUG else "production"
        env["PYTHONPATH"] = f"{backend_dir}{os.pathsep}{env.get('PYTHONPATH', '')}".strip(os.pathsep)
        proc = subprocess.Popen(
            [sys.executable, "-m", "voice_agent.meet_bot", "--session-id", str(session.id)],
            cwd=str(backend_dir),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return f"pid:{proc.pid}", f"Self-hosted Playwright Meet bot launched (PID {proc.pid})."

    if provider == VoiceBotSession.Provider.MEETINGBAAS:
        api_key = os.environ.get("MEETINGBAAS_API_KEY", "").strip()
        if not api_key:
            return "", "MEETINGBAAS_API_KEY not set; session queued for local WebSocket testing."
        resp = requests.post(
            os.environ.get("MEETINGBAAS_API_URL", "https://api.meetingbaas.com/bots"),
            headers={"x-meeting-baas-api-key": api_key, "Content-Type": "application/json"},
            json={
                "meeting_url": session.meeting_url,
                "bot_name": session.bot_name,
                "streaming": {"output": ws_url, "input": ws_url},
                "reserved": False,
            },
            timeout=15,
        )
        if not resp.ok:
            raise VoiceBotError(f"MeetingBaas error ({resp.status_code}): {resp.text[:250]}")
        data = resp.json()
        return str(data.get("bot_id") or data.get("id") or ""), "MeetingBaas bot dispatched."

    if provider == VoiceBotSession.Provider.RECALL:
        api_key = os.environ.get("RECALL_API_KEY", "").strip()
        if not api_key:
            return "", "RECALL_API_KEY not set; session queued for local WebSocket testing."
        resp = requests.post(
            os.environ.get("RECALL_API_URL", "https://us-west-2.recall.ai/api/v1/bot/"),
            headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
            json={
                "meeting_url": session.meeting_url,
                "bot_name": session.bot_name,
                "real_time_media": {"websocket_audio_destination_url": ws_url},
            },
            timeout=15,
        )
        if not resp.ok:
            raise VoiceBotError(f"Recall.ai error ({resp.status_code}): {resp.text[:250]}")
        data = resp.json()
        return str(data.get("id") or ""), "Recall.ai bot dispatched."

    return "", f"Telephony stream ({provider}) ready at {ws_url}."


def _trigger_provider_stop(session: VoiceBotSession) -> str:
    """Ask the external provider or self-hosted Playwright worker to leave the meeting."""
    if not session.external_bot_id:
        return "Session stopped locally."
    if session.provider == VoiceBotSession.Provider.SELF_HOSTED and session.external_bot_id.startswith("pid:"):
        try:
            pid = int(session.external_bot_id.split(":", 1)[1])
            os.kill(pid, signal.SIGTERM)
            return f"Stopped self-hosted Playwright bot (PID {pid})."
        except (ValueError, ProcessLookupError, PermissionError):
            return "Self-hosted Playwright bot already exited."
    if session.provider == VoiceBotSession.Provider.MEETINGBAAS:
        api_key = os.environ.get("MEETINGBAAS_API_KEY", "").strip()
        if api_key:
            base = os.environ.get("MEETINGBAAS_API_URL", "https://api.meetingbaas.com/bots").rstrip("/")
            requests.delete(f"{base}/{session.external_bot_id}", headers={"x-meeting-baas-api-key": api_key}, timeout=10)
            return "Requested MeetingBaas bot to leave meeting."
    elif session.provider == VoiceBotSession.Provider.RECALL:
        api_key = os.environ.get("RECALL_API_KEY", "").strip()
        if api_key:
            base = os.environ.get("RECALL_API_URL", "https://us-west-2.recall.ai/api/v1/bot/").rstrip("/")
            requests.post(f"{base}/{session.external_bot_id}/leave_call/", headers={"Authorization": f"Token {api_key}"}, timeout=10)
            return "Requested Recall.ai bot to leave call."
    return "Stopped active session."


@transaction.atomic
def start_bot_session(
    *,
    meet_code_or_url: str = "",
    phone_number: str = "",
    bot_name: str = "Hello Guys AI Assistant",
    custom_prompt: str = "",
    mode: str = VoiceBotSession.Mode.GOOGLE_MEET,
    provider: str = VoiceBotSession.Provider.SELF_HOSTED,
    customer_id: int | None = None,
    opportunity_id: int | None = None,
    meeting_id: int | None = None,
    user=None,
) -> VoiceBotSession:
    """Create a VoiceBotSession, enrich prompt with CRM context, and dispatch the bot."""
    customer = Customer.objects.filter(pk=customer_id).first() if customer_id else None
    opportunity = Opportunity.objects.select_related("customer").filter(pk=opportunity_id).first() if opportunity_id else None
    meeting = Meeting.objects.select_related("customer", "opportunity").filter(pk=meeting_id).first() if meeting_id else None

    if meeting and not customer:
        customer = meeting.customer
    if meeting and not opportunity:
        opportunity = meeting.opportunity
    if opportunity and not customer:
        customer = opportunity.customer

    normalized_url = normalize_meeting_url(meet_code_or_url or (meeting.meet_url if meeting else ""))
    if mode == VoiceBotSession.Mode.GOOGLE_MEET and not normalized_url:
        raise VoiceBotError("Provide a Google Meet code (e.g. abc-defg-hij) or full Meet URL.")
    if mode == VoiceBotSession.Mode.PHONE_CALL and not phone_number.strip():
        raise VoiceBotError("Provide a phone number for phone_call mode.")

    system_prompt = build_crm_system_prompt(customer=customer, opportunity=opportunity, custom_prompt=custom_prompt)
    session = VoiceBotSession.objects.create(
        customer=customer,
        opportunity=opportunity,
        meeting=meeting,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        mode=mode,
        provider=provider,
        meeting_url=normalized_url,
        phone_number=phone_number.strip(),
        bot_name=(bot_name or "Hello Guys AI Assistant").strip()[:120],
        system_prompt=system_prompt,
        status=VoiceBotSession.Status.QUEUED,
        started_at=timezone.now(),
    )
    ws_url = build_ws_url(session)
    session.append_log(f"Session created ({mode} via {provider}). WebSocket: {ws_url}")

    try:
        ext_id, msg = _trigger_provider_start(session, ws_url)
        session.external_bot_id = ext_id
        session.append_log(msg)
        if ext_id and session.provider != VoiceBotSession.Provider.SELF_HOSTED:
            session.status = VoiceBotSession.Status.IN_MEETING
        session.save(update_fields=["external_bot_id", "status", "connection_logs", "updated_at"])
    except VoiceBotError as exc:
        session.status = VoiceBotSession.Status.FAILED
        session.error_message = str(exc)
        session.append_log(f"Failed: {exc}")
        session.save(update_fields=["status", "error_message", "connection_logs", "updated_at"])
        raise

    return session


@transaction.atomic
def stop_bot_session(session: VoiceBotSession, user=None) -> VoiceBotSession:
    """Stop an active voice bot session and log the summary to CRM."""
    msg = _trigger_provider_stop(session)
    session.append_log(msg)
    session.status = VoiceBotSession.Status.COMPLETED
    session.ended_at = session.ended_at or timezone.now()
    session.save(update_fields=["status", "ended_at", "connection_logs", "updated_at"])
    return finalize_session_to_crm(session, user=user)


def generate_call_summary(transcript: list[dict[str, Any]]) -> str:
    """Generate a concise post-call summary from transcript turns."""
    if not transcript:
        return "Voice bot session completed (no speech transcript captured)."
    lines = [f"{t.get('role', 'speaker').title()}: {t.get('text', '')}" for t in transcript if t.get("text")]
    joined = "\n".join(lines)
    if os.environ.get("OPENAI_API_KEY"):
        try:
            import litellm
            resp = litellm.completion(
                model=os.environ.get("VOICE_SUMMARY_MODEL", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": "Summarize this sales/support call in 3-4 concise bullet points with action items."},
                    {"role": "user", "content": joined[:6000]},
                ],
                max_tokens=220,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.warning("LLM summary failed, falling back to transcript excerpt: %s", exc)
    return "Call Summary (auto):\n" + "\n".join(lines[:5])


def _format_transcript_doc(session: VoiceBotSession, meeting_title: str) -> str:
    """Format a clean Minutes of Meeting + Transcript document for Google Drive."""
    dt = timezone.localtime(session.ended_at or session.created_at).strftime("%d %b %Y, %I:%M %p")
    lines = [
        f"MINUTES OF MEETING & TRANSCRIPT: {meeting_title}",
        f"Date: {dt}",
        f"Mode: {session.get_mode_display()} | Assistant: {session.bot_name}",
    ]
    if session.customer:
        lines.append(f"Customer: {session.customer}")
    if session.meeting and session.meeting.attendees:
        lines.append(f"Attendees: {', '.join(session.meeting.attendees)}")
    lines.extend(["", "=== MINUTES OF MEETING (SUMMARY) ===", session.call_summary or "No summary."])
    lines.extend(["", "=== FULL TRANSCRIPT ==="])
    for turn in session.transcript or []:
        role = str(turn.get("role", "speaker")).title()
        text = str(turn.get("text", "")).strip()
        if text:
            lines.append(f"{role}: {text}")
    if not (session.transcript or []):
        lines.append("(No speech turns captured)")
    return "\n".join(lines)


def _save_transcript_to_drive(session: VoiceBotSession, meeting_title: str, user=None) -> str:
    """Upload the meeting transcript + MoM to the customer's Google Drive folder if Google is connected."""
    if session.customer is None or not GoogleAccount.get_solo().is_connected:
        return ""
    date_tag = timezone.localtime(session.ended_at or session.created_at).strftime("%d %b %Y")
    doc_name = f"{meeting_title} — Transcript ({date_tag})"
    content = _format_transcript_doc(session, meeting_title)
    try:
        gfile = crm_google.upload_text_as_doc(session.customer, doc_name, content, user=user or session.created_by)
        session.append_log(f"Saved transcript to Google Drive: {gfile.name}")
        return gfile.url
    except Exception as exc:
        logger.warning("Could not save transcript to Google Drive for session %s: %s", session.id, exc)
        session.append_log(f"Google Drive upload skipped: {exc}")
        return ""


def _send_mom_email(session: VoiceBotSession, meeting_title: str, drive_url: str = "") -> list[str]:
    """Email a structured Minutes of Meeting (MoM) summary to attendees, customer, and host."""
    recipients: list[str] = []
    if session.meeting and session.meeting.attendees:
        recipients.extend(session.meeting.attendees)
    if session.customer and session.customer.email:
        recipients.append(session.customer.email)
    host_email = GoogleAccount.get_solo().email
    if host_email:
        recipients.append(host_email)

    seen: set[str] = set()
    unique_recipients = []
    for r in recipients:
        cleaned = (r or "").strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            unique_recipients.append(cleaned)
    if not unique_recipients:
        return []

    dt = timezone.localtime(session.ended_at or session.created_at).strftime("%d %b %Y, %I:%M %p")
    body_lines = [
        f"Minutes of Meeting: {meeting_title}",
        f"Date: {dt}",
        f"Attendees: {', '.join(unique_recipients)}",
        "",
        "Summary & Action Items:",
        session.call_summary or "No summary generated.",
    ]
    if drive_url:
        body_lines.extend(["", f"Full Transcript (Google Drive): {drive_url}"])

    try:
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or host_email or "noreply@helloguys.local"
        send_mail(
            subject=f"MoM: {meeting_title} ({dt})",
            message="\n".join(body_lines),
            from_email=from_email,
            recipient_list=unique_recipients,
            fail_silently=False,
        )
        session.append_log(f"MoM summary emailed to: {', '.join(unique_recipients)}")
        return unique_recipients
    except Exception as exc:
        logger.warning("Could not send MoM email for session %s: %s", session.id, exc)
        session.append_log(f"MoM email skipped: {exc}")
        return []


@transaction.atomic
def finalize_session_to_crm(
    session: VoiceBotSession,
    *,
    transcript: list[dict[str, Any]] | None = None,
    user=None,
) -> VoiceBotSession:
    """Persist final transcript, generate summary, upload to Google Drive, email MoM, and log CRM Activity."""
    if transcript is not None:
        session.transcript = transcript
    if not session.call_summary:
        session.call_summary = generate_call_summary(session.transcript or [])
    if session.status != VoiceBotSession.Status.FAILED:
        session.status = VoiceBotSession.Status.COMPLETED
    session.ended_at = session.ended_at or timezone.now()

    meeting_title = (
        session.meeting.title
        if session.meeting
        else (session.opportunity.title if session.opportunity else (session.customer.name if session.customer else session.bot_name))
    )

    if not session.drive_transcript_url:
        session.drive_transcript_url = _save_transcript_to_drive(session, meeting_title, user=user)
    if not session.mom_emailed_to:
        session.mom_emailed_to = _send_mom_email(session, meeting_title, drive_url=session.drive_transcript_url)

    session.save(update_fields=[
        "transcript", "call_summary", "status", "ended_at",
        "drive_transcript_url", "mom_emailed_to", "connection_logs", "updated_at",
    ])

    if session.customer is not None:
        kind = Activity.Kind.MEETING if session.mode == VoiceBotSession.Mode.GOOGLE_MEET else Activity.Kind.CALL
        body = f"AI Voice Bot ({session.bot_name}) — {meeting_title}\n\n{session.call_summary}"
        if session.drive_transcript_url:
            body += f"\n\nGoogle Drive Transcript: {session.drive_transcript_url}"
        Activity.objects.create(
            customer=session.customer,
            opportunity=session.opportunity,
            kind=kind,
            body=body[:5000],
            created_by=user or session.created_by,
        )
    return session
