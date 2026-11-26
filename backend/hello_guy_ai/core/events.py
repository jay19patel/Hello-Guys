"""Run (and live-chat thread) events over Redis Streams.

Worker  --XADD-->  hello_guy_ai:run:<id>  --XREAD-->  SSE view --> browser

Streams (not pub/sub) so a client that connects late or reconnects
(Last-Event-ID) still gets every token. Keys expire after STREAM_TTL_SECONDS.
"""

import json
import time
from collections.abc import AsyncIterator, Iterator
from functools import lru_cache
from typing import Any

import redis
import redis.asyncio as aioredis

from hello_guy_ai.conf import ai_settings

TERMINAL_EVENTS = frozenset({"done", "error", "paused", "stopped"})
XREAD_BLOCK_MS = 15000
# redis-py 8 defaults socket_timeout to 5s; a blocking XREAD must outlive its block window.
XREAD_SOCKET_TIMEOUT = XREAD_BLOCK_MS / 1000 + 10


def stream_key(run_id: Any) -> str:
    return f"hello_guy_ai:run:{run_id}"


def thread_stream_key(thread_id: Any) -> str:
    """Live-chat events of one thread (staff replies, take over / hand back), read by the visitor's widget."""
    return f"hello_guy_ai:thread:{thread_id}"


THREAD_STREAM_TTL_SECONDS = 7 * 24 * 3600


def publish_thread_event(thread_id: Any, event: str, data: dict | None = None) -> None:
    RunEventPublisher(None, key=thread_stream_key(thread_id), ttl=THREAD_STREAM_TTL_SECONDS).publish(event, data)


def stop_key(run_id: Any) -> str:
    return f"hello_guy_ai:run:{run_id}:stop"


def request_stop(run_id: Any) -> None:
    get_redis().set(stop_key(run_id), "1", ex=ai_settings.RUN_TIME_LIMIT_SECONDS + 60)


def stop_requested(run_id: Any) -> bool:
    return bool(get_redis().exists(stop_key(run_id)))


def last_event_id(run_id: Any) -> str:
    """Newest entry id, so a resumed run's stream starts after the pre-pause events."""
    entries = get_redis().xrevrange(stream_key(run_id), count=1)
    return entries[0][0] if entries else "0-0"


@lru_cache(maxsize=1)
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(ai_settings.REDIS_URL, decode_responses=True)


class RunEventPublisher:
    """Used by the worker to emit events for one run."""

    def __init__(self, run_id: Any, client: redis.Redis | None = None, *, key: str = "", ttl: int = 0) -> None:
        self.key = key or stream_key(run_id)
        self.ttl = ttl or ai_settings.STREAM_TTL_SECONDS
        self.client = client or get_redis()

    def publish(self, event: str, data: dict | None = None) -> None:
        pipe = self.client.pipeline(transaction=False)
        pipe.xadd(self.key, {"event": event, "data": json.dumps(data or {}, default=str)}, maxlen=20000, approximate=True)
        # TTL on every write: a crashed worker still never leaks the key.
        pipe.expire(self.key, self.ttl)
        pipe.execute()


def _entries(response: list) -> list[tuple[str, str, str]]:
    return [(entry_id, f["event"], f["data"]) for entry_id, f in response[0][1]] if response else []


def iter_run_events_sync(
    run_id: Any, last_id: str, client: redis.Redis, *, key: str = "", terminal: frozenset[str] | set[str] = TERMINAL_EVENTS
) -> Iterator[tuple[str, str, str]]:
    """Blocking twin of iter_run_events for WSGI servers (runserver, gunicorn sync workers)."""
    key = key or stream_key(run_id)
    deadline = time.monotonic() + ai_settings.SSE_MAX_SECONDS
    while time.monotonic() < deadline:
        entries = _entries(client.xread({key: last_id}, block=XREAD_BLOCK_MS, count=200))
        if not entries:
            yield "", "ping", "{}"
        for entry_id, event, data in entries:
            last_id = entry_id
            yield entry_id, event, data
            if event in terminal:
                return
    yield "", "error", json.dumps({"message": "stream timeout"})


async def iter_run_events(
    run_id: Any, last_id: str = "0-0", client: aioredis.Redis | None = None, *, key: str = "",
    terminal: frozenset[str] | set[str] = TERMINAL_EVENTS,
) -> AsyncIterator[tuple[str, str, str]]:
    """Yield (entry_id, event, json_data) until a terminal event or SSE_MAX_SECONDS.

    Yields ("", "ping", "{}") while idle so proxies keep the connection open.
    """
    own_client = client is None
    client = client or aioredis.Redis.from_url(
        ai_settings.REDIS_URL, decode_responses=True, socket_timeout=XREAD_SOCKET_TIMEOUT
    )
    key = key or stream_key(run_id)
    deadline = time.monotonic() + ai_settings.SSE_MAX_SECONDS
    try:
        while time.monotonic() < deadline:
            entries = _entries(await client.xread({key: last_id}, block=XREAD_BLOCK_MS, count=200))
            if not entries:
                yield "", "ping", "{}"
            for entry_id, event, data in entries:
                last_id = entry_id
                yield entry_id, event, data
                if event in terminal:
                    return
        yield "", "error", json.dumps({"message": "stream timeout"})
    finally:
        if own_client:
            await client.aclose()
