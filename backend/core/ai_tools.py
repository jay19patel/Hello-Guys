"""Global AI tools for Reminders and Notifications (autodiscovered by hello_guy_ai)."""

from datetime import timedelta
from typing import Literal

from django.db.models import Q
from django.utils import timezone

from core import notifications, reminders
from core.models import FollowUpChannel, GlobalFollowUp
from hello_guy_ai import ToolError, current_context, tool
from hello_guy_ai.services.reminders import plan, zone


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
    Pass `due_at` as an ISO datetime in the user's local timezone (e.g. 'YYYY-MM-DDTHH:MM'),
    or `hours_from_now` if only a relative offset is known. A time that is invalid or already past is an error."""
    ctx = current_context()
    user = ctx.user
    is_staff = getattr(user, "is_staff", False)
    title = (title or "").strip()[:200]
    if not title:
        raise ToolError("Give the reminder a title.")

    if not is_staff:
        # Public website visitors: one reminder per run, no new channels, no priority escalation.
        from hello_guy_ai.models import Message

        if ctx.run_id and Message.objects.filter(run_id=ctx.run_id, name="create_global_reminder").exists():
            raise ToolError("A reminder was already created in this reply. Do not create another one.")
        priority = "normal"
        channel = (
            FollowUpChannel.objects.filter(name__iexact=(channel or "").strip()).values_list("name", flat=True).first()
            or "General"
        )

    tz = zone(ctx.timezone)
    now = timezone.now()
    if (due_at or "").strip():
        try:
            parsed_due = plan(due_at=due_at, recurrence=None, tz=tz, now=now)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
    else:
        if hours_from_now <= 0:
            raise ToolError("hours_from_now must be positive; pass due_at for a specific time.")
        parsed_due = now + timedelta(hours=min(hours_from_now, 24 * 90))

    # If triggered by public website-bot, assign to active staff so the team sees it in the Notification Bell.
    assigned_to = user
    if not is_staff:
        from django.contrib.auth import get_user_model
        assigned_to = get_user_model().objects.filter(is_active=True, is_staff=True).order_by("-is_superuser", "id").first() or user

    fu = reminders.schedule_follow_up(
        title=title,
        due_at=parsed_due,
        channel=(channel or "").strip() or "General",
        note=(note or "").strip()[:500 if not is_staff else 2000],
        priority=priority,
        assigned_to=assigned_to,
        created_by=user,
        related_label=(related_label or "").strip(),
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
