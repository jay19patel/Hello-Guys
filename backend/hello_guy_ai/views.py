"""Non-JSON views: SSE token stream and a minimal chat page."""

import json
import logging
import re
from collections.abc import AsyncIterator
from uuid import UUID

import redis.asyncio as aioredis
from redis.exceptions import RedisError
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, StreamingHttpResponse
from django.shortcuts import render

from hello_guy_ai import events
from hello_guy_ai.conf import ai_settings
from hello_guy_ai.models import Run

logger = logging.getLogger(__name__)
STREAM_ID = re.compile(r"^\d{1,20}-\d{1,20}$")


async def run_stream(request: HttpRequest, run_id: UUID) -> HttpResponse:
    """Server-Sent Events for one run. Supports reconnect via Last-Event-ID."""
    user = await request.auser()
    if not user.is_authenticated:
        return HttpResponse(status=401)
    run = await Run.objects.select_related("thread").filter(pk=run_id).afirst()
    if run is None or not (user.is_superuser or run.thread.created_by_id == user.pk):
        raise Http404

    # Last-Event-ID (browser reconnect) wins; ?after= starts a resumed run after its pre-pause events.
    last_id = _valid_id(request.headers.get("Last-Event-ID")) or _valid_id(request.GET.get("after")) or "0-0"
    response = StreamingHttpResponse(_sse(run, last_id), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # nginx: don't buffer the stream
    return response


async def _sse(run: Run, last_id: str) -> AsyncIterator[str]:
    client = aioredis.Redis.from_url(
        ai_settings.REDIS_URL, decode_responses=True, socket_timeout=events.XREAD_SOCKET_TIMEOUT
    )
    try:
        # Stream key expired but run is finished: answer from DB.
        settled = run.is_finished or run.status == Run.Status.PAUSED
        if settled and not await client.exists(events.stream_key(run.pk)):
            yield _final_from_db(await Run.objects.aget(pk=run.pk))
            return
        async for entry_id, event, data in events.iter_run_events(run.pk, last_id, client=client):
            if event == "ping":
                yield ": ping\n\n"
            else:
                yield f"id: {entry_id}\nevent: {event}\ndata: {data}\n\n"
    except RedisError:
        logger.exception("SSE stream failed for run %s", run.pk)
        yield f"event: error\ndata: {json.dumps({'message': 'Live connection lost. Please refresh and try again.'})}\n\n"
    finally:
        await client.aclose()


def _valid_id(value: str | None) -> str | None:
    return value if value and STREAM_ID.match(value) else None


def _final_from_db(run: Run) -> str:
    if run.status == Run.Status.SUCCEEDED:
        return f"event: done\ndata: {json.dumps({'run_id': str(run.pk)})}\n\n"
    if run.status == Run.Status.FAILED:
        return f"event: error\ndata: {json.dumps({'message': 'The assistant failed to respond.'})}\n\n"
    if run.status == Run.Status.STOPPED:
        return "event: stopped\ndata: {}\n\n"
    if run.status == Run.Status.PAUSED:
        from hello_guy_ai.engine import public_questions

        return f"event: paused\ndata: {json.dumps({'questions': public_questions(run.questions or [])})}\n\n"
    return f"event: status\ndata: {json.dumps({'status': run.status})}\n\n"


@login_required
def chat_page(request: HttpRequest) -> HttpResponse:
    return render(request, "hello_guy_ai/chat.html")
