"""Global Follow-ups service: customizable channels, scheduling, snooze/complete, and notification bridging."""

from datetime import datetime, time, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.text import slugify

from core import notifications
from core.models import FollowUp, FollowUpChannel
from core.safety import safe_url

DEFAULT_CHANNELS = [
    ("General", "fa-solid fa-bolt", "indigo"),
    ("WhatsApp", "fa-brands fa-whatsapp", "emerald"),
    ("Phone Call", "fa-solid fa-phone", "blue"),
    ("Email", "fa-solid fa-envelope", "violet"),
    ("Meeting", "fa-solid fa-calendar-days", "amber"),
    ("Payment / Invoice", "fa-solid fa-receipt", "rose"),
]

_CHANNEL_ALIASES = {
    "call": "Phone Call",
    "phone": "Phone Call",
    "phone call": "Phone Call",
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "email": "Email",
    "meeting": "Meeting",
    "general": "General",
}


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
        cleaned = channel.strip()
        canonical = _CHANNEL_ALIASES.get(cleaned.lower(), cleaned)
        existing = FollowUpChannel.objects.filter(Q(slug=slugify(canonical)) | Q(name__iexact=canonical)).first()
        return existing or get_or_create_channel(canonical)
    return None


def _sync_opportunity_next_followup(opportunity) -> None:
    """Keep Opportunity.next_follow_up in sync with its earliest pending follow-up."""
    if opportunity is None:
        return
    next_pending = (
        FollowUp.objects.filter(opportunity_id=opportunity.pk, status=FollowUp.Status.PENDING)
        .order_by("due_at")
        .values_list("due_at", flat=True)
        .first()
    )
    if opportunity.next_follow_up != next_pending:
        opportunity.next_follow_up = next_pending
        opportunity.save(update_fields=["next_follow_up", "updated_at"])


@transaction.atomic
def schedule_follow_up(
    *,
    title: str,
    due_at,
    channel: FollowUpChannel | str | int | None = None,
    note: str = "",
    priority: str = FollowUp.Priority.NORMAL,
    assigned_to: Any = None,
    created_by: Any = None,
    customer: Any = None,
    opportunity: Any = None,
    related_label: str = "",
    related_url: str = "",
    entity_type: str = "",
    entity_id: str = "",
    notify_now: bool = True,
) -> FollowUp:
    """Schedule a unified follow-up (optionally linked to a CRM customer/opportunity) and notify if requested."""
    ensure_default_channels()
    resolved_channel = _resolve_channel(channel)
    if priority not in FollowUp.Priority.values:
        priority = FollowUp.Priority.NORMAL

    target_user = assigned_to or created_by
    fu = FollowUp.objects.create(
        title=(title or "Follow-up").strip()[:200],
        note=(note or "").strip()[:2000],
        channel=resolved_channel,
        due_at=due_at,
        priority=priority,
        assigned_to=target_user if getattr(target_user, "is_authenticated", False) else None,
        created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
        customer=customer,
        opportunity=opportunity,
        related_label=(related_label or (customer.name if customer else "")).strip()[:150],
        related_url=safe_url(related_url)[:500],
        entity_type=(entity_type or ("opportunity" if opportunity else "customer" if customer else "")).strip()[:40],
        entity_id=str(entity_id or (opportunity.pk if opportunity else customer.pk if customer else ""))[:64],
    )
    _sync_opportunity_next_followup(opportunity)

    if notify_now and fu.assigned_to:
        ch_name = fu.channel_name
        when = timezone.localtime(fu.due_at).strftime("%d %b, %I:%M %p").lstrip("0")
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


def complete_follow_up(follow_up: FollowUp, user: Any = None) -> FollowUp:
    follow_up.status = FollowUp.Status.DONE
    follow_up.completed_at = timezone.now()
    follow_up.save(update_fields=["status", "completed_at", "updated_at"])
    _sync_opportunity_next_followup(follow_up.opportunity)
    if follow_up.customer_id:
        from crm.models import Activity

        Activity.objects.create(
            customer=follow_up.customer,
            opportunity=follow_up.opportunity,
            kind=Activity.Kind.SYSTEM,
            created_by=user if getattr(user, "is_authenticated", False) else None,
            body=f"Completed follow-up ({follow_up.channel_name}): {follow_up.note or follow_up.title or 'No note'}",
        )
    return follow_up


def snooze_follow_up(follow_up: FollowUp, hours: int = 24, due_at=None) -> FollowUp:
    follow_up.due_at = due_at if due_at is not None else timezone.now() + timedelta(hours=max(1, min(hours, 24 * 30)))
    follow_up.notified_at = None
    follow_up.save(update_fields=["due_at", "notified_at", "updated_at"])
    _sync_opportunity_next_followup(follow_up.opportunity)
    return follow_up


def cancel_follow_up(follow_up: FollowUp) -> FollowUp:
    follow_up.status = FollowUp.Status.CANCELLED
    follow_up.save(update_fields=["status", "updated_at"])
    _sync_opportunity_next_followup(follow_up.opportunity)
    return follow_up


def notify_due_follow_ups(user: Any = None) -> int:
    """Push a warning notification once for any pending follow-up that has reached its due_at.

    `user=None` covers every user (callable from a scheduler)."""
    now = timezone.now()
    qs = FollowUp.objects.select_related("channel", "assigned_to", "created_by").filter(
        status=FollowUp.Status.PENDING,
        due_at__lte=now,
        notified_at__isnull=True,
    )
    if user is not None:
        qs = qs.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True, created_by=user))

    count = 0
    for fu in qs[:50]:
        # Claim the row first so two concurrent bell polls cannot both notify.
        if not FollowUp.objects.filter(pk=fu.pk, notified_at__isnull=True).update(notified_at=now):
            continue
        recipient = fu.assigned_to or fu.created_by
        if not recipient:
            continue
        ch_name = fu.channel_name
        notifications.notify(
            user=recipient,
            title=f"Follow-up due: {fu.title}",
            body=f"{ch_name}" + (f" · {fu.related_label}" if fu.related_label else "") + (f" — {fu.note[:120]}" if fu.note else ""),
            url=fu.related_url or "/follow-ups/",
            category="followup",
            level="warning",
            metadata={"followup_id": fu.pk},
        )
        count += 1
    return count


def visible_followups(user: Any):
    """Admins see every follow-up; everyone else only their own (assigned to or created by them)."""
    from meta_connector.roles import ADMIN, has_role

    qs = FollowUp.objects.all()
    return qs if has_role(user, ADMIN) else qs.filter(Q(assigned_to=user) | Q(created_by=user))


def followup_analytics(user: Any, days: int = 14) -> dict[str, Any]:
    """Compute follow-up KPIs, 14-day local-timezone trend, and channel/priority breakdowns."""
    from meta_connector.roles import ADMIN, has_role

    now = timezone.now()
    tz = timezone.get_current_timezone()
    today_date = timezone.localdate(now)
    today_start = datetime.combine(today_date, time.min, tzinfo=tz)
    today_end = today_start + timedelta(days=1)
    start_day = today_date - timedelta(days=days - 1)
    trend_start = datetime.combine(start_day, time.min, tzinfo=tz)

    all_fu = visible_followups(user)
    agg = all_fu.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=FollowUp.Status.PENDING)),
        overdue=Count("id", filter=Q(status=FollowUp.Status.PENDING, due_at__lt=now)),
        today=Count("id", filter=Q(status=FollowUp.Status.PENDING, due_at__gte=today_start, due_at__lt=today_end)),
        done=Count("id", filter=Q(status=FollowUp.Status.DONE)),
        cancelled=Count("id", filter=Q(status=FollowUp.Status.CANCELLED)),
    )
    total_all = agg["total"] or 0
    done_count = agg["done"] or 0
    pending_count = agg["pending"] or 0
    overdue_count = agg["overdue"] or 0

    scheduled_by_day = dict(
        all_fu.filter(created_at__gte=trend_start)
        .annotate(day=TruncDate("created_at", tzinfo=tz))
        .values_list("day")
        .annotate(c=Count("id"))
    )
    completed_by_day = dict(
        all_fu.filter(status=FollowUp.Status.DONE, completed_at__gte=trend_start)
        .annotate(day=TruncDate("completed_at", tzinfo=tz))
        .values_list("day")
        .annotate(c=Count("id"))
    )
    day_dates = [start_day + timedelta(days=i) for i in range(days)]
    daily_trend = {
        "labels": [d.strftime("%d %b") for d in day_dates],
        "scheduled": [scheduled_by_day.get(d, 0) for d in day_dates],
        "completed": [completed_by_day.get(d, 0) for d in day_dates],
    }

    mine = Q() if has_role(user, ADMIN) else (
        Q(follow_ups__assigned_to=user) | Q(follow_ups__created_by=user)
    )
    channel_rows = (
        FollowUpChannel.objects.filter(is_active=True)
        .annotate(
            total=Count("follow_ups", filter=mine),
            done_cnt=Count("follow_ups", filter=mine & Q(follow_ups__status=FollowUp.Status.DONE)),
            pending_cnt=Count("follow_ups", filter=mine & Q(follow_ups__status=FollowUp.Status.PENDING)),
        )
        .order_by("-total", "name")
    )
    max_ch = max((c.total for c in channel_rows), default=1) or 1
    channel_stats = [
        {
            "name": c.name,
            "slug": c.slug,
            "icon": c.icon,
            "total": c.total,
            "done": c.done_cnt,
            "pending": c.pending_cnt,
            "pct": round((c.total / max_ch) * 100),
        }
        for c in channel_rows
        if c.total > 0
    ]

    priority_counts = dict(
        all_fu.filter(status=FollowUp.Status.PENDING)
        .values_list("priority")
        .annotate(c=Count("id"))
    )
    priority_stats = [
        {"label": label, "value": val, "count": priority_counts.get(val, 0)}
        for val, label in FollowUp.Priority.choices
    ]

    return {
        "stats": {
            "pending": pending_count,
            "overdue": overdue_count,
            "ontime": max(0, pending_count - overdue_count),
            "today": agg["today"] or 0,
            "done": done_count,
            "cancelled": agg["cancelled"] or 0,
            "total": total_all,
            "completion_rate": round((done_count / total_all) * 100) if total_all else None,
        },
        "daily_trend": daily_trend,
        "channel_stats": channel_stats,
        "priority_stats": priority_stats,
    }
