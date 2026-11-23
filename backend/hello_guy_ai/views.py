"""Chat page and the Server-Sent Events (SSE) token stream for runs."""

import json
import logging
import re
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import redis
import redis.asyncio as aioredis
from asgiref.sync import sync_to_async
from django.contrib.auth.decorators import login_required
from django.core.handlers.asgi import ASGIRequest
from django.http import Http404, HttpRequest, HttpResponse, StreamingHttpResponse
from django.shortcuts import render
from redis.exceptions import RedisError

from hello_guy_ai.conf import ai_settings
from hello_guy_ai.core import events
from hello_guy_ai.core.engine import public_questions
from hello_guy_ai.models import Run
from hello_guy_ai.services import chat

logger = logging.getLogger(__name__)
LOST_FRAME = f"event: error\ndata: {json.dumps({'message': 'Live connection lost. Please refresh and try again.'})}\n\n"
STREAM_ID = re.compile(r"^\d{1,20}-\d{1,20}$")


@login_required
def chat_page(request: HttpRequest) -> HttpResponse:
    return render(request, "hello_guy_ai/chat.html")


async def run_stream(request: HttpRequest, run_id: UUID) -> HttpResponse:
    """Server-Sent Events for one run. Supports reconnect via Last-Event-ID."""
    user = await request.auser()
    run = await Run.objects.select_related("thread").filter(pk=run_id).afirst()
    if run is None:
        raise Http404
    if user.is_authenticated:
        if not (user.is_superuser or run.thread.created_by_id == user.pk):
            raise Http404
    elif not await sync_to_async(chat.is_public_run)(run):
        return HttpResponse(status=401)

    last_id = _valid_id(request.headers.get("Last-Event-ID")) or _valid_id(request.GET.get("after")) or "0-0"
    stream = _sse(run, last_id) if isinstance(request, ASGIRequest) else _sse_sync(run, last_id)
    response = StreamingHttpResponse(stream, content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


async def _sse(run: Run, last_id: str) -> AsyncIterator[str]:
    client = aioredis.Redis.from_url(
        ai_settings.REDIS_URL, decode_responses=True, socket_timeout=events.XREAD_SOCKET_TIMEOUT
    )
    try:
        settled = run.is_finished or run.status == Run.Status.PAUSED
        if settled and not await client.exists(events.stream_key(run.pk)):
            yield _final_from_db(await Run.objects.aget(pk=run.pk))
            return
        async for entry_id, event, data in events.iter_run_events(run.pk, last_id, client=client):
            yield _frame(entry_id, event, data)
    except RedisError:
        logger.exception("SSE stream failed for run %s", run.pk)
        yield LOST_FRAME
    finally:
        await client.aclose()


def _sse_sync(run: Run, last_id: str) -> Iterator[str]:
    client = redis.Redis.from_url(ai_settings.REDIS_URL, decode_responses=True, socket_timeout=events.XREAD_SOCKET_TIMEOUT)
    try:
        settled = run.is_finished or run.status == Run.Status.PAUSED
        if settled and not client.exists(events.stream_key(run.pk)):
            yield _final_from_db(Run.objects.get(pk=run.pk))
            return
        for entry_id, event, data in events.iter_run_events_sync(run.pk, last_id, client):
            yield _frame(entry_id, event, data)
    except RedisError:
        logger.exception("SSE stream failed for run %s", run.pk)
        yield LOST_FRAME
    finally:
        client.close()


def _frame(entry_id: str, event: str, data: str) -> str:
    return ": ping\n\n" if event == "ping" else f"id: {entry_id}\nevent: {event}\ndata: {data}\n\n"


def _valid_id(value: str | None) -> str | None:
    return value if value and STREAM_ID.match(value) else None


def _final_from_db(run: Run) -> str:
    if run.status == Run.Status.SUCCEEDED:
        return f"event: done\ndata: {json.dumps({'run_id': str(run.pk)})}\n\n"
    if run.status == Run.Status.FAILED:
        return f"event: error\ndata: {json.dumps({'message': run.public_error})}\n\n"
    if run.status == Run.Status.STOPPED:
        return "event: stopped\ndata: {}\n\n"
    if run.status == Run.Status.PAUSED:
        return f"event: paused\ndata: {json.dumps({'questions': public_questions(run.questions or [])})}\n\n"
    return f"event: status\ndata: {json.dumps({'status': run.status})}\n\n"
