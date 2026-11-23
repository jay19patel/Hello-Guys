"""Built-in tools: current time, reminders and notifications. Always scoped to the requesting user.

Times are read in the user's timezone (sent by the browser, stored on the thread; settings.TIME_ZONE otherwise).
"""

from typing import Any

from django.utils import timezone

from hello_guy_ai.core.runtime import ToolError, current_context, tool
from hello_guy_ai.services import reminders


def _tz():
    return reminders.zone(current_context().timezone)


def _plan(args: dict) -> Any:
    try:
        return reminders.plan(due_at=args.get("due_at"), recurrence=args.get("recurrence"), tz=_tz())
    except ValueError as exc:
        raise ToolError(str(exc)) from None


def _valid(args: dict) -> bool:
    """Ask for approval only for a valid request; an invalid one runs at once and returns the error to the LLM,
    so the user is asked a question ("5 PM has passed, tomorrow?") instead of approving something doomed."""
    try:
        _plan(args)
    except ToolError:
        return False
    return True


def _confirm_create(args: dict) -> str:
    due = _plan(args)
    kind = "Scheduled task (the assistant runs it and notifies you)" if args.get("run_assistant") else "Reminder"
    return f"{kind}: {args.get('message')}\n{reminders.describe(due, _tz(), args.get('recurrence') or '')}"


@tool
def get_current_datetime() -> dict:
    """Current date and time in the user's timezone. Call it before working out 'today', 'tomorrow',
    'last month' or any relative date."""
    tz = _tz()
    now = timezone.now().astimezone(tz)
    return {"now": now.isoformat(timespec="minutes"), "weekday": now.strftime("%A"), "timezone": tz.key}


@tool(requires_confirmation=_valid, confirm_prompt=_confirm_create)
def create_reminder(
    message: str, due_at: str | None = None, recurrence: str | None = None, run_assistant: bool = False
) -> dict:
    """Remind the user. due_at: local ISO date-time (e.g. 2026-11-23T17:00) for a one-time reminder.
    recurrence: cron in the user's timezone for repeating ones, e.g. '0 10 * * 1' = every Monday 10:00,
    '0 9 * * *' = daily 9:00 (due_at optional then). run_assistant=true: at that time the assistant
    runs `message` as a task (e.g. "send the weekly sales summary") and notifies the user with its answer."""
    ctx = current_context()
    try:
        r = reminders.create_reminder(
            ctx.user,
            message,
            due_at=due_at,
            recurrence=recurrence,
            tz_name=ctx.timezone,
            agent_id=ctx.agent_key if run_assistant else "",
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    return {"reminder_id": r.pk, "when": reminders.describe(r.due_at, _tz(), r.recurrence)}


@tool
def list_reminders() -> list[dict]:
    """The user's upcoming reminders and recurring tasks (scheduled or paused)."""
    tz = _tz()
    return [
        {
            "reminder_id": r.pk,
            "message": r.message,
            "next": reminders.describe(r.due_at, tz),
            "recurrence": r.recurrence or None,
            "runs_assistant": bool(r.agent_id),
            "status": r.status,
        }
        for r in reminders.active_reminders(current_context().user)[:50]
    ]


def _confirm_update(args: dict) -> str:
    changes = []
    if args.get("message"):
        changes.append(f"message: {args['message']}")
    if args.get("due_at") or args.get("recurrence"):
        changes.append(f"time: {reminders.describe(_plan(args), _tz(), args.get('recurrence') or '')}")
    return f"Change reminder {args.get('reminder_id')}:\n" + "\n".join(changes or ["nothing"])


def _update_needs_approval(args: dict) -> bool:
    return _valid(args) if args.get("due_at") or args.get("recurrence") else True


@tool(requires_confirmation=_update_needs_approval, confirm_prompt=_confirm_update)
def update_reminder(
    reminder_id: int, message: str | None = None, due_at: str | None = None, recurrence: str | None = None
) -> dict:
    """Edit or snooze a scheduled reminder (new message, new local due_at, or new recurrence cron)."""
    reminder = reminders.active_reminders(current_context().user).filter(pk=reminder_id).first()
    if reminder is None:
        raise ToolError(f"No scheduled reminder {reminder_id}. Use list_reminders.")
    if message:
        reminder.message = message.strip()[: reminders.MAX_MESSAGE_CHARS]
    if due_at or recurrence:
        reminder.due_at = _plan({"due_at": due_at, "recurrence": recurrence})
        reminder.recurrence = (recurrence or "").strip() if recurrence is not None else reminder.recurrence
    reminder.save(update_fields=["message", "due_at", "recurrence", "updated_at"])
    return {"reminder_id": reminder.pk, "next": reminders.describe(reminder.due_at, _tz(), reminder.recurrence)}


@tool(requires_confirmation=True, confirm_prompt=lambda a: f"Cancel reminder {a.get('reminder_id')}")
def cancel_reminder(reminder_id: int) -> dict:
    """Cancel a scheduled reminder or recurring task."""
    if not reminders.cancel_reminder(current_context().user, reminder_id):
        raise ToolError(f"No scheduled reminder {reminder_id}. Use list_reminders.")
    return {"reminder_id": reminder_id, "status": "cancelled"}


@tool
def list_notifications(unread_only: bool = True, limit: int = 20) -> dict:
    """The user's notifications (reminders that fired, scheduled task results). Default: only the ones they
    have not seen yet. Showing them here counts as seen, so they are marked read. unread_only=false also
    lists already-read ones, each with its read status."""
    user = current_context().user
    tz = _tz()
    rows = list(reminders.notifications_for(user, unread_only)[: min(max(int(limit), 1), 50)])
    reminders.mark_read(user, [n.pk for n in rows if not n.is_read])
    return {
        "unread_remaining": reminders.notifications_for(user, unread_only=True).count(),
        "notifications": [
            {
                "id": n.pk,
                "title": n.title,
                "body": n.body[:2000],
                "at": n.created_at.astimezone(tz).strftime("%Y-%m-%d %I:%M %p"),
                "was_unread": not n.is_read,
                "url": n.url,
            }
            for n in rows
        ],
    }
