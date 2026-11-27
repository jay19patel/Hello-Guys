"""Date and time helper tool for assistants."""

from django.utils import timezone

from hello_guy_ai.core.runtime import current_context, tool
from hello_guy_ai.services.scheduling import zone


@tool
def get_current_datetime() -> dict:
    """Return the current date, time, weekday and timezone of the user."""
    tz = zone(current_context().timezone)
    now = timezone.now().astimezone(tz)
    return {
        "iso": now.isoformat(),
        "date": now.date().isoformat(),
        "time": now.strftime("%H:%M"),
        "weekday": now.strftime("%A"),
        "timezone": tz.key,
    }
