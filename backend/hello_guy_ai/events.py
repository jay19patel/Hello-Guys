"""Run events over Redis Streams.

Worker  --XADD-->  hello_guy_ai:run:<id>  --XREAD-->  SSE view --> browser

Streams (not pub/sub) so a client that connects late or reconnects
(Last-Event-ID) still gets every token. Keys expire after STREAM_TTL_SECONDS.
"""

import json
import time
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

import redis
import redis.asyncio as aioredis

from hello_guy_ai.conf import ai_settings

TERMINAL_EVENTS = {"done", "error", "paused", "stopped"}
XREAD_BLOCK_MS = 15000
# redis-py 8 defaults socket_timeout to 5s; a blocking XREAD must outlive its block window.
XREAD_SOCKET_TIMEOUT = XREAD_BLOCK_MS / 1000 + 10


def stream_key(run_id: Any) -> str:
    return f"hello_guy_ai:run:{run_id}"


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

    def __init__(self, run_id: Any, client: redis.Redis | None = None) -> None:
        self.key = stream_key(run_id)
        self.client = client or get_redis()

    def publish(self, event: str, data: dict | None = None) -> None:
        pipe = self.client.pipeline(transaction=False)
        pipe.xadd(self.key, {"event": event, "data": json.dumps(data or {})}, maxlen=20000, approximate=True)
        # TTL on every write: a crashed worker still never leaks the key.
        pipe.expire(self.key, ai_settings.STREAM_TTL_SECONDS)
        pipe.execute()


async def iter_run_events(
    run_id: Any, last_id: str = "0-0", client: aioredis.Redis | None = None
) -> AsyncIterator[tuple[str, str, str]]:
    """Yield (entry_id, event, json_data) until a terminal event or SSE_MAX_SECONDS.

    Yields ("", "ping", "{}") while idle so proxies keep the connection open.
    """
    own_client = client is None
    client = client or aioredis.Redis.from_url(
        ai_settings.REDIS_URL, decode_responses=True, socket_timeout=XREAD_SOCKET_TIMEOUT
    )
    deadline = time.monotonic() + ai_settings.SSE_MAX_SECONDS
    try:
        while time.monotonic() < deadline:
            response = await client.xread({stream_key(run_id): last_id}, block=XREAD_BLOCK_MS, count=200)
            if not response:
                yield "", "ping", "{}"
                continue
            for entry_id, fields in response[0][1]:
                last_id = entry_id
                yield entry_id, fields["event"], fields["data"]
                if fields["event"] in TERMINAL_EVENTS:
                    return
        yield "", "error", json.dumps({"message": "stream timeout"})
    finally:
        if own_client:
            await client.aclose()
