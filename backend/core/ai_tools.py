"""Global AI tools for Follow-ups and Notifications (autodiscovered by hello_guy_ai)."""

from datetime import timedelta
from typing import Any, Literal

from django.db.models import Q
from django.utils import timezone

from core import follow_ups, notifications
from core.models import FollowUp, FollowUpChannel
from crm.services import default_staff_assignee
from hello_guy_ai import ToolError, current_context, tool
from hello_guy_ai.services.reminders import plan, resolve_requested_slot, zone


def _resolve_crm_target(ctx: Any, opportunity_reference: str) -> tuple[Any, Any]:
    """Resolve optional CRM customer and opportunity from explicit reference or Meta thread context."""
    from crm import services as crm_services
    from crm.models import Opportunity
    from hello_guy_ai.models import Thread

    ref = (opportunity_reference or "").strip()
    if ref:
        opp = Opportunity.objects.select_related("customer").filter(reference=ref).first()
        if opp is None:
            raise ToolError(f"No opportunity {ref!r}.")
        return opp.customer, opp
    if ctx.thread_id and getattr(ctx.user, "is_staff", False):
        thread = Thread.objects.filter(pk=ctx.thread_id).first()
        contact = crm_services.contact_for_thread(thread) if thread else None
        if contact is not None:
            customer = crm_services.get_or_create_customer(
                name=contact.name or contact.username or contact.phone or "Lead",
                contact=contact,
                source=crm_services.THREAD_SOURCES.get(contact.platform, "manual"),
            )
            return customer, None
    return None, None


@tool
def create_follow_up(
    title: str,
    due_at: str = "",
    hours_from_now: int = 24,
    channel: str = "General",
    note: str = "",
    priority: Literal["low", "normal", "high", "urgent"] = "normal",
    related_label: str = "",
    opportunity_reference: str = "",
    time_specified: bool = True,
) -> dict:
    """Create a follow-up (shown in Follow-ups and Notification Bell) for any topic, lead, or channel
    (e.g. Email, WhatsApp, Phone Call, Meeting, Payment / Invoice, General).
    Pass `due_at` as an ISO datetime in the user's local timezone (e.g. 'YYYY-MM-DDTHH:MM'), or
    `YYYY-MM-DD` (with `time_specified=false`) when no time was given — date-only requests automatically
    schedule a confirmation follow-up the day before at 10:00 AM. Otherwise `hours_from_now` is used."""
    ctx = current_context()
    user = ctx.user
    is_staff = getattr(user, "is_staff", False)
    title = (title or "").strip()[:200]
    if not title:
        raise ToolError("Give the follow-up a title.")

    if not is_staff:
        # Public website visitors: one follow-up per run, no arbitrary channels, no priority escalation.
        from hello_guy_ai.models import Message

        if ctx.run_id and Message.objects.filter(run_id=ctx.run_id, name="create_follow_up").exists():
            raise ToolError("A follow-up was already created in this reply. Do not create another one.")
        priority = "normal"
        channel = (
            FollowUpChannel.objects.filter(name__iexact=(channel or "").strip()).values_list("name", flat=True).first()
            or "General"
        )

    tz = zone(ctx.timezone)
    now = timezone.now()
    due_str = (due_at or "").strip()
    day_before = False
    if due_str:
        date_only = ("T" not in due_str and " " not in due_str) or not time_specified
        try:
            if date_only:
                parsed_due, day_before = resolve_requested_slot(date_str=due_str.split("T")[0], tz=tz, now=now)
            else:
                parsed_due = plan(due_at=due_str, recurrence=None, tz=tz, now=now)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
    else:
        if hours_from_now <= 0:
            raise ToolError("hours_from_now must be positive; pass due_at for a specific time.")
        parsed_due = now + timedelta(hours=min(hours_from_now, 24 * 90))

    clean_note = (note or "").strip()
    if day_before and "time" not in clean_note.lower():
        clean_note = f"No time specified for {due_str.split('T')[0]} — follow up to confirm preferred time. {clean_note}".strip()

    assigned_to = user if is_staff else (default_staff_assignee() or user)
    customer, opportunity = _resolve_crm_target(ctx, opportunity_reference) if is_staff else (None, None)

    if customer is not None:
        from crm import services as crm_services

        fu = crm_services.schedule_follow_up(
            customer=customer,
            opportunity=opportunity,
            due_at=parsed_due,
            channel=(channel or "").strip() or "WhatsApp",
            note=clean_note[:2000],
            assigned_to=assigned_to,
            created_by=user,
            title=title,
        )
    else:
        fu = follow_ups.schedule_follow_up(
            title=title,
            due_at=parsed_due,
            channel=(channel or "").strip() or "General",
            note=clean_note[:500 if not is_staff else 2000],
            priority=priority,
            assigned_to=assigned_to,
            created_by=user,
            related_label=(related_label or "").strip(),
            related_url="/follow-ups/",
            notify_now=True,
        )

    out: dict[str, Any] = {
        "follow_up_id": fu.pk,
        "title": fu.title,
        "channel": fu.channel_name,
        "due_at": fu.due_at.isoformat(),
    }
    if customer is not None:
        out["customer"] = customer.name
    if day_before:
        out["scheduled_for_day_before"] = True
        out["requested_date"] = due_str.split("T")[0]
    return out


@tool
def list_follow_ups(status: Literal["pending", "done", "all"] = "pending", limit: int = 15) -> list[dict]:
    """List follow-ups for the current user (pending, done, or all)."""
    user = current_context().user
    qs = FollowUp.objects.select_related("channel", "customer").filter(
        Q(assigned_to=user) | Q(assigned_to__isnull=True, created_by=user)
    )
    if status != "all":
        qs = qs.filter(status=status)
    rows = qs.order_by("due_at")[: max(1, min(int(limit), 50))]
    return [
        {
            "id": r.pk,
            "title": r.title,
            "channel": r.channel_name,
            "priority": r.priority,
            "status": r.status,
            "due_at": r.due_at.isoformat(),
            "related_label": r.related_label,
            "customer": r.customer.name if r.customer_id else "",
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
