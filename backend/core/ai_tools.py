"""Global AI tools for Reminders and Notifications (autodiscovered by hello_guy_ai)."""

from datetime import datetime, timedelta
from typing import Literal

from django.db.models import Q
from django.utils import timezone

from core import notifications, reminders
from core.models import GlobalFollowUp
from hello_guy_ai import current_context, tool


@tool
def create_global_reminder(
    title: str,
    due_at: str = "",
    hours_from_now: int = 24,
    channel: str = "General",
    note: str = "",
    priority: Literal["low", "normal", "high", "urgent"] = "normal",
    related_label: str = "",
) -> dict:
    """Create a global Reminder (shown in the Reminders page and Notification Bell) for any topic and channel
    (e.g. Email, WhatsApp, Call, Meeting, Payment, Slack, General).
    Pass `due_at` as an ISO datetime in the user's local timezone (e.g. '2026-11-29T20:00' for Sunday 8 PM),
    or `hours_from_now` if only a relative offset is known."""
    ctx = current_context()
    user = ctx.user
    parsed_due = None
    if (due_at or "").strip():
        try:
            parsed_due = datetime.fromisoformat(due_at.strip())
            if timezone.is_naive(parsed_due):
                parsed_due = timezone.make_aware(parsed_due, timezone.get_current_timezone())
        except ValueError:
            parsed_due = None

    if parsed_due is None:
        hours = max(1, min(int(hours_from_now), 24 * 90))
        parsed_due = timezone.now() + timedelta(hours=hours)

    # If triggered by public website-bot, assign to active staff so the team sees it in the Notification Bell.
    assigned_to = user
    if not getattr(user, "is_staff", False):
        from django.contrib.auth import get_user_model
        assigned_to = get_user_model().objects.filter(is_active=True, is_staff=True).order_by("-is_superuser", "id").first() or user

    fu = reminders.schedule_follow_up(
        title=title.strip()[:200],
        due_at=parsed_due,
        channel=channel.strip() or "General",
        note=note.strip(),
        priority=priority,
        assigned_to=assigned_to,
        created_by=user,
        related_label=related_label.strip(),
        related_url="/reminders/",
        notify_now=True,
    )
    return {
        "reminder_id": fu.pk,
        "title": fu.title,
        "channel": fu.channel.name if fu.channel else "General",
        "due_at": fu.due_at.isoformat(),
    }


@tool
def list_global_reminders(status: Literal["pending", "done", "all"] = "pending", limit: int = 15) -> list[dict]:
    """List global reminders for the current user (pending, done, or all)."""
    user = current_context().user
    qs = GlobalFollowUp.objects.select_related("channel").filter(
        Q(assigned_to=user) | Q(assigned_to__isnull=True, created_by=user)
    )
    if status != "all":
        qs = qs.filter(status=status)
    rows = qs.order_by("due_at")[: max(1, min(int(limit), 50))]
    return [
        {
            "id": r.pk,
            "title": r.title,
            "channel": r.channel.name if r.channel else "General",
            "priority": r.priority,
            "status": r.status,
            "due_at": r.due_at.isoformat(),
            "related_label": r.related_label,
        }
        for r in rows
    ]


@tool
def send_global_notification(
    title: str,
    body: str = "",
    category: Literal["crm", "meeting", "ai", "meta", "system"] = "ai",
    level: Literal["info", "success", "warning", "error"] = "info",
    notify_all_staff: bool = False,
) -> dict:
    """Send an in-app notification to the Global Notification Bell (for the current user or all staff)."""
    user = current_context().user
    if notify_all_staff and getattr(user, "is_staff", False):
        created = notifications.notify_staff(title=title, body=body, category=category, level=level)
        return {"sent": len(created), "scope": "all_staff"}
    n = notifications.notify(user=user, title=title, body=body, category=category, level=level)
    return {"notification_id": n.pk, "scope": "user"}
