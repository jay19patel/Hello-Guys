"""Meeting domain services: transcript parsing (VTT/SRT/speaker-diarized), storage, and proposed-time workflows."""

import re
import uuid
from datetime import datetime

from django.utils import timezone

from crm import google, services
from crm.models import Activity, Meeting, MeetingTranscript

_VTT_TIME_RE = re.compile(
    r"^(?P<start>\d{1,2}:?\d{2}:\d{2}(?:[.,]\d{1,3})?)\s*-->\s*(?P<end>\d{1,2}:?\d{2}:\d{2}(?:[.,]\d{1,3})?)"
)
_VTT_VOICE_TAG_RE = re.compile(r"^<v\s+([^>]+)>(.+?)(?:</v>)?$")
_SPEAKER_LINE_RE = re.compile(r"^(?:\[(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]\s*)?(?P<speaker>[A-Za-z0-9_.@ \-]{1,50}):\s+(?P<text>.+)$")


def _clean_timestamp(raw: str) -> str:
    return raw.split(".")[0].split(",")[0].strip()


def parse_transcript_text(raw_text: str) -> list[dict[str, str]]:
    """Parse VTT, SRT, or `Speaker: text` transcripts into structured [{speaker, timestamp, text}] segments."""
    lines = [line.rstrip() for line in (raw_text or "").splitlines()]
    segments: list[dict[str, str]] = []
    current_ts = ""

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line == "WEBVTT" or line.isdigit():
            continue
        if match := _VTT_TIME_RE.match(line):
            current_ts = _clean_timestamp(match.group("start"))
            continue
        if vmatch := _VTT_VOICE_TAG_RE.match(line):
            speaker, text = vmatch.group(1).strip(), vmatch.group(2).strip()
            segments.append({"speaker": speaker, "timestamp": current_ts, "text": text})
            continue
        if smatch := _SPEAKER_LINE_RE.match(line):
            speaker = smatch.group("speaker").strip()
            ts = smatch.group("ts") or current_ts
            text = smatch.group("text").strip()
            if speaker.lower() not in ("http", "https"):
                segments.append({"speaker": speaker, "timestamp": ts, "text": text})
                continue
        if segments and not current_ts:
            segments[-1]["text"] = f"{segments[-1]['text']} {line}".strip()
        else:
            segments.append({"speaker": "Speaker", "timestamp": current_ts, "text": line})
    return segments


def create_transcript(
    meeting: Meeting,
    raw_text: str,
    *,
    title: str = "",
    source: str = MeetingTranscript.Source.MANUAL,
    segments: list[dict[str, str]] | None = None,
    summary: str = "",
    action_items: list[dict] | None = None,
    key_decisions: list[str] | None = None,
    language: str = "en",
    duration_seconds: int | None = None,
    user=None,
) -> MeetingTranscript:
    """Store a meeting transcript in the MeetingTranscript table and log it on the customer timeline."""
    cleaned_text = (raw_text or "").strip()
    if not cleaned_text:
        raise ValueError("Transcript text cannot be empty.")

    parsed_segments = segments if segments is not None else parse_transcript_text(cleaned_text)
    transcript = MeetingTranscript.objects.create(
        meeting=meeting,
        title=(title or f"{meeting.title} — Transcript")[:255],
        source=source,
        raw_text=cleaned_text,
        segments=parsed_segments,
        summary=(summary or "").strip(),
        action_items=action_items or [],
        key_decisions=key_decisions or [],
        language=(language or "en")[:20],
        duration_seconds=duration_seconds,
        created_by=user,
    )
    if meeting.customer_id:
        services.add_note(
            user=user,
            customer=meeting.customer,
            opportunity=meeting.opportunity,
            kind=Activity.Kind.MEETING,
            body=f"Transcript saved for \"{meeting.title}\" ({transcript.word_count} words).",
        )
    return transcript


def propose_meeting_time(
    meeting: Meeting,
    *,
    email: str,
    name: str = "",
    proposed_start: datetime | None = None,
    proposed_end: datetime | None = None,
    note: str = "",
    user=None,
) -> dict:
    """Record a guest's proposed new meeting time on the CRM meeting."""
    proposal = {
        "id": uuid.uuid4().hex[:10],
        "email": email.strip().lower(),
        "name": name.strip(),
        "proposed_start": proposed_start.isoformat() if proposed_start else "",
        "proposed_end": proposed_end.isoformat() if proposed_end else "",
        "note": note.strip()[:500],
        "status": "pending",
        "created_at": timezone.now().isoformat(timespec="seconds"),
    }
    proposals = list(meeting.proposed_times or [])
    proposals.insert(0, proposal)
    meeting.proposed_times = proposals
    meeting.save(update_fields=["proposed_times", "updated_at"])

    if meeting.customer_id:
        when = timezone.localtime(proposed_start).strftime("%d %b %Y, %I:%M %p") if proposed_start else "new time"
        services.add_note(
            user=user,
            customer=meeting.customer,
            opportunity=meeting.opportunity,
            kind=Activity.Kind.MEETING,
            body=f"{email} proposed {when} for \"{meeting.title}\"{f': {note}' if note else ''}",
        )
    return proposal


def resolve_proposed_time(meeting: Meeting, proposal_id: str, *, decision: str, user=None) -> Meeting:
    """Accept (reschedule + sync to Google) or decline a guest's proposed meeting time."""
    if decision not in ("accepted", "declined"):
        raise ValueError("Decision must be 'accepted' or 'declined'.")

    proposals = list(meeting.proposed_times or [])
    target = next((p for p in proposals if p.get("id") == proposal_id), None)
    if target is None:
        raise ValueError("Proposed time not found.")

    target["status"] = decision
    target["resolved_at"] = timezone.now().isoformat(timespec="seconds")

    if decision == "accepted" and target.get("proposed_start") and target.get("proposed_end"):
        new_start = datetime.fromisoformat(target["proposed_start"])
        new_end = datetime.fromisoformat(target["proposed_end"])
        if timezone.is_naive(new_start):
            new_start = timezone.make_aware(new_start, timezone.get_current_timezone())
        if timezone.is_naive(new_end):
            new_end = timezone.make_aware(new_end, timezone.get_current_timezone())
        if new_end <= new_start:
            raise ValueError("Proposed end time must be after start time.")
        meeting.start_at = new_start
        meeting.end_at = new_end
        meeting.status = Meeting.Status.SCHEDULED
        for other in proposals:
            if other.get("id") != proposal_id and other.get("status", "pending") == "pending":
                other["status"] = "declined"

    meeting.proposed_times = proposals
    meeting.save()

    if decision == "accepted" and meeting.google_event_id:
        google.sync_meeting(meeting)

    if meeting.customer_id:
        services.add_note(
            user=user,
            customer=meeting.customer,
            opportunity=meeting.opportunity,
            kind=Activity.Kind.MEETING,
            body=f"Proposed time from {target.get('email', 'guest')} {decision} for \"{meeting.title}\".",
        )
    return meeting
