"""Independent business logic for Hello Guy Meet: room creation and database-backed MoM storage."""

import logging
import os
from typing import Any

from django.db import transaction
from django.utils import timezone

from hello_guy_meet.models import MeetMinutes, MeetRoom, MeetSettings

logger = logging.getLogger(__name__)


def build_meet_prompt(*, custom_prompt: str = "") -> str:
    """Build the AI Assistant system prompt from room override or global MeetSettings."""
    cfg = MeetSettings.get_solo()
    return custom_prompt.strip() or cfg.default_system_prompt.strip()


@transaction.atomic
def create_meet_room(
    *,
    title: str,
    host=None,
    scheduled_start=None,
    scheduled_end=None,
    ai_enabled: bool | None = None,
    bot_name: str = "",
    custom_prompt: str = "",
) -> MeetRoom:
    """Create a standalone Hello Guy Meet room."""
    cfg = MeetSettings.get_solo()
    return MeetRoom.objects.create(
        title=title.strip()[:200] or "Hello Guy Meet",
        host=host if getattr(host, "is_authenticated", False) else None,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_end,
        ai_enabled=cfg.ai_auto_join_default if ai_enabled is None else ai_enabled,
        bot_name=(bot_name or cfg.default_bot_name).strip()[:120],
        system_prompt=build_meet_prompt(custom_prompt=custom_prompt),
    )


def resolve_llm_model_and_key(cfg: MeetSettings) -> tuple[str, str]:
    """Select the configured LLM model and matching API key (Mistral, Groq, or OpenAI)."""
    model = (cfg.llm_model or "").strip() or "groq/llama-3.3-70b-versatile"
    mistral_key = cfg.get_api_key("MISTRAL_API_KEY")
    groq_key = cfg.get_api_key("GROQ_API_KEY")
    openai_key = cfg.get_api_key("OPENAI_API_KEY")

    if model.startswith("mistral/") and mistral_key:
        return model, mistral_key
    if model.startswith("groq/") and groq_key:
        return model, groq_key
    if mistral_key and not groq_key and not openai_key:
        return (model if model.startswith("mistral/") else "mistral/mistral-small-latest"), mistral_key
    if groq_key and not openai_key:
        return (model if model.startswith("groq/") else "groq/llama-3.3-70b-versatile"), groq_key
    return model, openai_key


def generate_mom_summary(transcript: list[dict[str, Any]], cfg: MeetSettings | None = None) -> str:
    """Generate a concise Minutes of Meeting summary from transcript turns."""
    if not transcript:
        return "Meeting ended (no speech transcript recorded)."
    cfg = cfg or MeetSettings.get_solo()
    lines = [f"{t.get('speaker') or t.get('role', 'Speaker')}: {t.get('text', '')}" for t in transcript if t.get("text")]
    joined = "\n".join(lines)
    model, api_key = resolve_llm_model_and_key(cfg)
    if api_key:
        try:
            import litellm
            resp = litellm.completion(
                model=model,
                api_key=api_key,
                messages=[
                    {"role": "system", "content": "Create concise Minutes of Meeting (MoM) with 3-5 bullet points: key discussion points, decisions, and action items."},
                    {"role": "user", "content": joined[:6000]},
                ],
                max_tokens=260,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.warning("MoM LLM summary failed, falling back to excerpt: %s", exc)
    return "Minutes of Meeting (auto):\n" + "\n".join(f"• {line}" for line in lines[:6])


def extract_action_items(summary: str) -> list[str]:
    """Extract bullet lines as structured action items for MeetMinutes."""
    items: list[str] = []
    for raw_line in (summary or "").splitlines():
        line = raw_line.strip()
        if line.startswith(("•", "-", "*")):
            cleaned = line.lstrip("•-* ").strip()
            if cleaned:
                items.append(cleaned)
    return items[:20]


@transaction.atomic
def finalize_room(
    room: MeetRoom,
    *,
    transcript: list[dict[str, Any]] | None = None,
    user=None,
) -> MeetMinutes:
    """End a MeetRoom and store its transcript & Minutes of Meeting in the MeetMinutes table."""
    cfg = MeetSettings.get_solo()
    if transcript is not None:
        room.transcript = transcript
    if not room.call_summary:
        room.call_summary = generate_mom_summary(room.transcript or [], cfg=cfg)
    room.status = MeetRoom.Status.ENDED
    room.ended_at = room.ended_at or timezone.now()
    room.save(update_fields=["transcript", "call_summary", "status", "ended_at", "updated_at"])

    participants = list(
        dict.fromkeys(
            str(t.get("speaker")).strip()
            for t in (room.transcript or [])
            if t.get("speaker")
        )
    )
    minutes, _ = MeetMinutes.objects.update_or_create(
        room=room,
        defaults={
            "title": room.title,
            "summary": room.call_summary,
            "action_items": extract_action_items(room.call_summary),
            "participants": participants,
            "transcript": room.transcript or [],
            "created_by": user if getattr(user, "is_authenticated", False) else room.host,
        },
    )
    return minutes
