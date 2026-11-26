"""Global Follow-ups service: customizable channels, scheduling, snooze/complete, and notification bridging."""

from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from core import notifications
from core.models import FollowUpChannel, GlobalFollowUp

DEFAULT_CHANNELS = [
    ("General", "fa-solid fa-bolt", "indigo"),
    ("WhatsApp", "fa-brands fa-whatsapp", "emerald"),
    ("Phone Call", "fa-solid fa-phone", "blue"),
    ("Email", "fa-solid fa-envelope", "violet"),
    ("Meeting", "fa-solid fa-calendar-days", "amber"),
    ("Payment / Invoice", "fa-solid fa-receipt", "rose"),
]


def ensure_default_channels() -> None:
    """Seed starter follow-up channels once when the table is empty."""
    if FollowUpChannel.objects.exists():
        return
    for name, icon, color in DEFAULT_CHANNELS:
        FollowUpChannel.objects.get_or_create(
            slug=slugify(name),
            defaults={"name": name, "icon": icon, "color": color},
        )


def get_or_create_channel(
    name: str,
    *,
    icon: str = "fa-solid fa-bolt",
    color: str = "indigo",
    user: Any = None,
) -> FollowUpChannel:
    """Create or retrieve a custom FollowUpChannel by name/slug."""
    cleaned = (name or "").strip()[:60]
    if not cleaned:
        raise ValueError("Channel name is required.")
    slug = slugify(cleaned)[:60] or "custom"
    channel, created = FollowUpChannel.objects.get_or_create(
        slug=slug,
        defaults={
            "name": cleaned,
            "icon": (icon or "fa-solid fa-bolt")[:60],
            "color": (color or "indigo")[:20],
            "created_by": user if getattr(user, "is_authenticated", False) else None,
        },
    )
    if not created and not channel.is_active:
        channel.is_active = True
        channel.save(update_fields=["is_active"])
    return channel


def _resolve_channel(channel: FollowUpChannel | str | int | None) -> FollowUpChannel | None:
    if channel is None or channel == "":
        return None
    if isinstance(channel, FollowUpChannel):
        return channel
    if isinstance(channel, int) or (isinstance(channel, str) and channel.isdigit()):
        return FollowUpChannel.objects.filter(pk=int(channel)).first()
    if isinstance(channel, str):
        existing = FollowUpChannel.objects.filter(Q(slug=slugify(channel)) | Q(name__iexact=channel.strip())).first()
        return existing or get_or_create_channel(channel)
    return None


@transaction.atomic
def schedule_follow_up(
    *,
    title: str,
    due_at,
    channel: FollowUpChannel | str | int | None = None,
    note: str = "",
    priority: str = GlobalFollowUp.Priority.NORMAL,
    assigned_to: Any = None,
    created_by: Any = None,
    related_label: str = "",
    related_url: str = "",
    entity_type: str = "",
    entity_id: str = "",
    notify_now: bool = True,
) -> GlobalFollowUp:
    """Schedule a global follow-up on any topic/channel and optionally push a GlobalNotification."""
    ensure_default_channels()
    resolved_channel = _resolve_channel(channel)
    if priority not in GlobalFollowUp.Priority.values:
        priority = GlobalFollowUp.Priority.NORMAL

    target_user = assigned_to or created_by
    fu = GlobalFollowUp.objects.create(
        title=(title or "Follow-up").strip()[:200],
        note=(note or "").strip()[:2000],
        channel=resolved_channel,
        due_at=due_at,
        priority=priority,
        assigned_to=target_user if getattr(target_user, "is_authenticated", False) else None,
        created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
        related_label=(related_label or "").strip()[:150],
        related_url=(related_url or "").strip()[:500],
        entity_type=(entity_type or "").strip()[:40],
        entity_id=str(entity_id or "")[:64],
    )

    if notify_now and fu.assigned_to:
        ch_name = fu.channel.name if fu.channel else "Follow-up"
        when = timezone.localtime(fu.due_at).strftime("%d %b, %H:%M")
        notifications.notify(
            user=fu.assigned_to,
            title=f"Follow-up: {fu.title}",
            body=f"{ch_name} · Due {when}" + (f" — {fu.note[:120]}" if fu.note else ""),
            url=fu.related_url or "/follow-ups/",
            category="followup",
            level="info",
            metadata={"followup_id": fu.pk},
        )
    return fu


def complete_follow_up(follow_up: GlobalFollowUp) -> GlobalFollowUp:
    follow_up.status = GlobalFollowUp.Status.DONE
    follow_up.completed_at = timezone.now()
    follow_up.save(update_fields=["status", "completed_at", "updated_at"])
    return follow_up


def snooze_follow_up(follow_up: GlobalFollowUp, hours: int = 24) -> GlobalFollowUp:
    follow_up.due_at = timezone.now() + timedelta(hours=max(1, min(hours, 24 * 30)))
    follow_up.notified_at = None
    follow_up.save(update_fields=["due_at", "notified_at", "updated_at"])
    return follow_up


def cancel_follow_up(follow_up: GlobalFollowUp) -> GlobalFollowUp:
    follow_up.status = GlobalFollowUp.Status.CANCELLED
    follow_up.save(update_fields=["status", "updated_at"])
    return follow_up


def notify_due_follow_ups(user: Any = None) -> int:
    """Push a warning notification once for any pending follow-up that has reached its due_at."""
    now = timezone.now()
    qs = GlobalFollowUp.objects.select_related("channel", "assigned_to", "created_by").filter(
        status=GlobalFollowUp.Status.PENDING,
        due_at__lte=now,
        notified_at__isnull=True,
    )
    if user is not None:
        qs = qs.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True, created_by=user))

    count = 0
    for fu in qs[:50]:
        recipient = fu.assigned_to or fu.created_by
        if not recipient:
            continue
        ch_name = fu.channel.name if fu.channel else "Follow-up"
        notifications.notify(
            user=recipient,
            title=f"Follow-up due: {fu.title}",
            body=f"{ch_name}" + (f" · {fu.related_label}" if fu.related_label else "") + (f" — {fu.note[:120]}" if fu.note else ""),
            url=fu.related_url or "/follow-ups/",
            category="followup",
            level="warning",
            metadata={"followup_id": fu.pk},
        )
        fu.notified_at = now
        fu.save(update_fields=["notified_at", "updated_at"])
        count += 1
    return count
