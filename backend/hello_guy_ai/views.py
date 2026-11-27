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
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from redis.exceptions import RedisError

from hello_guy_ai.access import can_chat
from hello_guy_ai.analytics import build_ai_analytics, live_snapshot
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
    if not can_chat(request.user):
        return render(request, "403.html", status=403)
    return render(request, "hello_guy_ai/chat.html")


@login_required
def ai_analytics(request: HttpRequest) -> HttpResponse:
    """Render the hello_guy_ai Analytics & System Health dashboard."""
    if not (request.user.is_staff or request.user.is_superuser):
        return render(request, "403.html", status=403)
    if request.GET.get("format") == "json":  # polled every few seconds: live runs only, not the whole dashboard
        snapshot = live_snapshot()
        return JsonResponse({k: snapshot[k] for k in ("live_runs", "active_runs", "paused_runs")})
    return render(request, "hello_guy_ai/analytics.html", build_ai_analytics(period=request.GET.get("period", "30d")))


async def run_stream(request: HttpRequest, run_id: UUID) -> HttpResponse:
    """Server-Sent Events for one run. Supports reconnect via Last-Event-ID."""
    user = await request.auser()
    run = await Run.objects.select_related("thread").filter(pk=run_id).afirst()
    if run is None:
        raise Http404
    owner = user.is_authenticated and (user.is_superuser or run.thread.created_by_id == user.pk)
    # A website visitor's run is readable by its thread id holder, logged in (e.g. staff testing the widget) or not.
    if not owner and not await sync_to_async(chat.is_public_run)(run):
        if user.is_authenticated:
            raise Http404
        return HttpResponse(status=401)

    last_id = _valid_id(request.headers.get("Last-Event-ID")) or _valid_id(request.GET.get("after")) or "0-0"
    stream = _sse(run, last_id) if isinstance(request, ASGIRequest) else _sse_sync(run, last_id)
    return _sse_response(stream)


def _sse_response(stream) -> StreamingHttpResponse:
    response = StreamingHttpResponse(stream, content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def _async_redis() -> aioredis.Redis:
    return aioredis.Redis.from_url(
        ai_settings.REDIS_URL, decode_responses=True, socket_timeout=events.XREAD_SOCKET_TIMEOUT
    )


def _sync_redis() -> redis.Redis:
    return redis.Redis.from_url(
        ai_settings.REDIS_URL, decode_responses=True, socket_timeout=events.XREAD_SOCKET_TIMEOUT
    )


async def _sse(run: Run, last_id: str) -> AsyncIterator[str]:
    client = _async_redis()
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
    client = _sync_redis()
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


async def thread_stream(request: HttpRequest, thread_id: UUID) -> HttpResponse:
    """Live-chat events of a website visitor's thread: staff replies ("message") and take over / hand back ("mode").

    The thread id is the visitor's key (same rule as the public messages endpoint). Reconnect: Last-Event-ID.
    """
    exists = await sync_to_async(lambda: chat.public_threads().filter(pk=thread_id).exists())()
    if not exists:
        raise Http404
    key = events.thread_stream_key(thread_id)
    last_id = _valid_id(request.headers.get("Last-Event-ID")) or _valid_id(request.GET.get("after")) or "$"
    stream = _thread_sse(key, last_id) if isinstance(request, ASGIRequest) else _thread_sse_sync(key, last_id)
    return _sse_response(stream)


async def _thread_sse(key: str, last_id: str) -> AsyncIterator[str]:
    client = _async_redis()
    try:
        async for entry_id, event, data in events.iter_run_events(None, last_id, client=client, key=key, terminal=frozenset()):
            yield _frame(entry_id, event, data)
    except RedisError:
        logger.exception("Thread SSE failed for %s", key)
        yield LOST_FRAME
    finally:
        await client.aclose()


def _thread_sse_sync(key: str, last_id: str) -> Iterator[str]:
    client = _sync_redis()
    try:
        for entry_id, event, data in events.iter_run_events_sync(None, last_id, client, key=key, terminal=frozenset()):
            yield _frame(entry_id, event, data)
    except RedisError:
        logger.exception("Thread SSE failed for %s", key)
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
