"""Independent Dramatiq background tasks for Hello Guy Meet."""

import logging

import dramatiq
from django.contrib.auth import get_user_model

from hello_guy_meet.models import MeetRoom

logger = logging.getLogger(__name__)


@dramatiq.actor(max_retries=2)
def process_meet_post_call_task(room_id: str, user_id: int | None = None) -> None:
    """Generate and store Minutes of Meeting in MeetMinutes in the background."""
    from hello_guy_meet.services import finalize_room

    room = MeetRoom.objects.select_related("host").filter(pk=room_id).first()
    if room is None:
        return
    user = get_user_model().objects.filter(pk=user_id).first() if user_id else None
    finalize_room(room, user=user)
