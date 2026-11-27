"""Timezone, cron, and slot-resolution helpers used by follow-up tools and prompts."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from django.conf import settings
from django.utils import timezone


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
    """Validate a schedule time and return its first due datetime."""
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


def resolve_requested_slot(
    *, date_str: str, time_str: str = "", tz: ZoneInfo, now: datetime | None = None
) -> tuple[datetime, bool]:
    """Resolve a requested date (+ optional time) into (due_datetime, is_day_before)."""
    now = now or timezone.now()
    now_local = now.astimezone(tz)
    raw_date = (date_str or "").strip()
    raw_time = (time_str or "").strip()

    if "T" in raw_date or " " in raw_date:
        due = parse_local(raw_date, tz)
        if due <= now:
            raise ValueError(f"That time has already passed (it is now {now_local:%Y-%m-%d %H:%M} {tz.key}).")
        return due, False

    try:
        target_date = date.fromisoformat(raw_date)
    except ValueError:
        raise ValueError(f"'{raw_date}' is not a valid date like 2026-12-24") from None

    if raw_time:
        try:
            parsed_time = time.fromisoformat(raw_time)
        except ValueError:
            raise ValueError(f"'{raw_time}' is not a valid 24h time like 17:00") from None
        due = datetime.combine(target_date, parsed_time, tzinfo=tz)
        if due <= now:
            raise ValueError(f"That time has already passed (it is now {now_local:%Y-%m-%d %H:%M} {tz.key}).")
        return due, False

    if target_date < now_local.date():
        raise ValueError(f"'{raw_date}' is already in the past (today is {now_local:%Y-%m-%d}).")

    day_before = datetime.combine(target_date - timedelta(days=1), time(10, 0), tzinfo=tz)
    if day_before <= now:
        day_before = (now_local + timedelta(hours=1)).replace(second=0, microsecond=0)
    return day_before, True
