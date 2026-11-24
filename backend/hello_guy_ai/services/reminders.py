"""Reminders and notifications.

Exactly once: firing is a compare-and-set on (pk, status, due_at) in the same transaction that creates the
Notification, so a second scheduler, a restart or a retry can never notify twice. A recurring reminder's next
time is computed from *now*, so a long outage fires it once, not once per missed slot.

Channels: in-app always. Other channels connect to `notification_created` (email ships built in, NOTIFY_EMAIL).
"""

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from django.conf import settings
from django.db import transaction
from django.dispatch import Signal
from django.urls import reverse
from django.utils import timezone

from hello_guy_ai.models import Message, Notification, Reminder, Run

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 500
FIRE_BATCH = 500

# Sent after commit: receivers get `notification` (a Notification). Hook WhatsApp/Slack/SMS here.
notification_created = Signal()


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or settings.TIME_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(settings.TIME_ZONE)


def parse_local(value: str, tz: ZoneInfo) -> datetime:
    """ISO date-time; a naive value is read in the user's timezone."""
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"'{value}' is not an ISO date-time like 2026-11-23T17:00") from None
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt


def next_occurrence(cron: str, tz: ZoneInfo, after: datetime) -> datetime:
    return croniter(cron, after.astimezone(tz)).get_next(datetime)


def plan(*, due_at: str | None, recurrence: str | None, tz: ZoneInfo, now: datetime | None = None) -> datetime:
    """Validate a reminder's time and return its first due datetime. Raises ValueError with a user-facing message."""
    now = now or timezone.now()
    recurrence = (recurrence or "").strip()
    if recurrence and not croniter.is_valid(recurrence):
        raise ValueError(f"'{recurrence}' is not a valid cron expression (e.g. '0 10 * * 1' = every Monday 10:00)")
    if due_at:
        due = parse_local(due_at, tz)
        if due <= now:
            local_now = now.astimezone(tz).strftime("%Y-%m-%d %H:%M")
            raise ValueError(
                f"That time has already passed (it is now {local_now} {tz.key}). "
                "Ask the user whether they meant the next day at the same time."
            )
        return due
    if recurrence:
        return next_occurrence(recurrence, tz, now)
    raise ValueError("Give a date-time (due_at) or a recurrence")


def describe(due: datetime, tz: ZoneInfo, recurrence: str = "", now: datetime | None = None) -> str:
    """'Today 5:00 PM (Asia/Kolkata)', 'Tomorrow ...', or a full date; recurring ones show the cron."""
    local = due.astimezone(tz)
    today = (now or timezone.now()).astimezone(tz).date()
    day = {0: "Today", 1: "Tomorrow"}.get((local.date() - today).days, local.strftime("%a %d %b %Y"))
    text = f"{day} {local.strftime('%I:%M %p').lstrip('0')} ({tz.key})"
    return f"{text}, then repeating: {recurrence}" if recurrence else text


def create_reminder(
    user: Any, message: str, *, due_at: str | None, recurrence: str | None, tz_name: str, agent_id: str = ""
) -> Reminder:
    message = (message or "").strip()
    if not message or len(message) > MAX_MESSAGE_CHARS:
        raise ValueError(f"message must be 1-{MAX_MESSAGE_CHARS} characters")
    tz = zone(tz_name)
    due = plan(due_at=due_at, recurrence=recurrence, tz=tz)
    return Reminder.objects.create(
        user=user,
        message=message,
        due_at=due,
        recurrence=(recurrence or "").strip(),
        timezone=tz.key,
        agent_id=agent_id,
    )


def scheduled_reminders(user: Any):
    return Reminder.objects.filter(user=user, status=Reminder.Status.SCHEDULED)


def active_reminders(user: Any):
    """What the user can still manage: scheduled and paused."""
    return Reminder.objects.filter(user=user, status__in=[Reminder.Status.SCHEDULED, Reminder.Status.PAUSED])


def cancel_reminder(user: Any, reminder_id: int) -> bool:
    return bool(active_reminders(user).filter(pk=reminder_id).update(status=Reminder.Status.CANCELLED))


def pause_reminder(user: Any, reminder_id: int) -> bool:
    return bool(scheduled_reminders(user).filter(pk=reminder_id).update(status=Reminder.Status.PAUSED))


def resume_reminder(user: Any, reminder_id: int) -> Reminder | None:
    """A recurring reminder continues from its next slot after now; a one-time one that passed fires on the next tick."""
    reminder = active_reminders(user).filter(pk=reminder_id, status=Reminder.Status.PAUSED).first()
    if reminder is None:
        return None
    now = timezone.now()
    if reminder.recurrence and reminder.due_at <= now:
        reminder.due_at = next_occurrence(reminder.recurrence, zone(reminder.timezone), now)
    reminder.status = Reminder.Status.SCHEDULED
    reminder.save(update_fields=["status", "due_at", "updated_at"])
    return reminder


def delete_reminder(user: Any, reminder_id: int) -> bool:
    return active_reminders(user).filter(pk=reminder_id).delete()[0] > 0


def notifications_for(user: Any, unread_only: bool = False):
    qs = Notification.objects.filter(user=user)
    return qs.filter(is_read=False) if unread_only else qs


def mark_read(user: Any, ids: list[int] | None = None) -> int:
    """Mark the user's notifications read (all unread when ids is None). Returns how many changed."""
    qs = notifications_for(user, unread_only=True)
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    return qs.update(is_read=True, read_at=timezone.now())


def notify(user: Any, title: str, body: str = "", url: str = "", reminder: Reminder | None = None) -> Notification:
    notification = Notification.objects.create(user=user, title=title[:200], body=body, url=url, reminder=reminder)
    transaction.on_commit(lambda: notification_created.send(sender=Notification, notification=notification))
    return notification


def fire_due_reminders(now: datetime | None = None) -> int:
    """Fire every due reminder exactly once. Called by the scheduler each tick."""
    now = now or timezone.now()
    fired = 0
    due = Reminder.objects.filter(status=Reminder.Status.SCHEDULED, due_at__lte=now).select_related("user")
    for reminder in due[:FIRE_BATCH]:
        if not reminder.user.is_active:
            Reminder.objects.filter(pk=reminder.pk).update(status=Reminder.Status.CANCELLED)
            continue
        try:
            next_due = next_occurrence(reminder.recurrence, zone(reminder.timezone), now) if reminder.recurrence else None
        except Exception:  # bad cron/timezone (e.g. edited in admin): park this one, keep the scheduler running
            logger.exception("Reminder %s has an invalid schedule; paused", reminder.pk)
            Reminder.objects.filter(pk=reminder.pk).update(status=Reminder.Status.PAUSED)
            notify(reminder.user, "Reminder paused", f"{reminder.message}\n\nIts schedule is invalid: fix it to resume.")
            continue
        with transaction.atomic():
            claimed = Reminder.objects.filter(
                pk=reminder.pk, status=Reminder.Status.SCHEDULED, due_at=reminder.due_at
            ).update(
                due_at=next_due or reminder.due_at,
                status=Reminder.Status.SCHEDULED if next_due else Reminder.Status.DONE,
                last_fired_at=now,
            )
            if not claimed:  # another scheduler (or a cancel) got there first
                continue
            if reminder.agent_id:
                from hello_guy_ai.tasks.actors import run_reminder_agent

                transaction.on_commit(lambda pk=reminder.pk: run_reminder_agent.send(pk))
            else:
                notify(reminder.user, "Reminder", reminder.message, reminder=reminder)
        fired += 1
    return fired


def run_reminder_agent(reminder_id: int) -> None:
    """Recurring agent task ("every Monday send the sales summary"): run the assistant, notify with its answer."""
    from hello_guy_ai.core.engine import AgentRunner
    from hello_guy_ai.services.chat import start_agent_run

    reminder = Reminder.objects.select_related("user").filter(pk=reminder_id).first()
    if reminder is None:
        return
    try:
        run = start_agent_run(reminder.user, reminder.agent_id, reminder.message)
    except Exception as exc:  # assistant removed or user lost access
        notify(reminder.user, "Scheduled task failed", f"{reminder.message}\n\n{exc}", reminder=reminder)
        return
    AgentRunner(run).execute()
    run.refresh_from_db()
    answer = (
        Message.objects.filter(run=run, role=Message.Role.ASSISTANT).exclude(content="").order_by("-id")
        .values_list("content", flat=True).first()
    )
    if run.status == Run.Status.PAUSED:
        body = "It needs your approval to continue. Open the chat to review."
    else:
        body = answer if run.status == Run.Status.SUCCEEDED else run.public_error
    url = f"{reverse('hello_guy_ai_chat')}?assistant={run.thread.assistant_id}&thread={run.thread_id}"
    notify(reminder.user, reminder.message[:200], body or "", url=url, reminder=reminder)


def send_notification_email(notification_id: int) -> None:
    from django.core.mail import send_mail

    n = Notification.objects.select_related("user").filter(pk=notification_id).first()
    if n and n.user.email:
        send_mail(n.title, n.body or n.title, None, [n.user.email])
