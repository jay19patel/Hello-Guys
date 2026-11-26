import json

import dramatiq
import fakeredis
import pytest
from litellm.types.utils import ChatCompletionDeltaToolCall, Delta, Function, ModelResponseStream, StreamingChoices

from hello_guy_ai.core import events
from hello_guy_ai.models import KnowledgeBase
from hello_guy_ai import views as sse


@pytest.fixture(autouse=True)
def clear_cache():
    from django.core.cache import cache

    cache.clear()  # trigger index is cached; never leak it across tests


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """Sync + async fakeredis sharing one in-memory server, for every test."""
    server = fakeredis.FakeServer()
    sync_client = fakeredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(events, "get_redis", lambda: sync_client)
    monkeypatch.setattr(
        sse.aioredis.Redis, "from_url", lambda *a, **k: fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    )
    monkeypatch.setattr(sse.redis.Redis, "from_url", lambda *a, **k: fakeredis.FakeRedis(server=server, decode_responses=True))
    return sync_client


@pytest.fixture
def broker():
    b = dramatiq.get_broker()
    b.flush_all()
    yield b
    b.flush_all()


@pytest.fixture
def user(django_user_model):
    from meta_connector.roles import VIEWER, set_role

    member = django_user_model.objects.create_user("jay", "jay@example.com", "pass12345")
    set_role(member, VIEWER)  # the internal chat needs a dashboard role
    return member


@pytest.fixture
def staff(django_user_model):
    return django_user_model.objects.create_user("admin", "a@example.com", "pass12345", is_staff=True)


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser("root", "r@example.com", "pass12345")


@pytest.fixture
def api_client(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def kb(db):
    return KnowledgeBase.objects.get_or_create(slug="hello_guy", defaults={"name": "Hello Guy"})[0]


def text_stream(text: str):
    """Chunks shaped exactly like a real LiteLLM stream."""
    chunks = [
        ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(role="assistant", content=text[i : i + 4]))])
        for i in range(0, len(text), 4)
    ]
    chunks.append(ModelResponseStream(choices=[StreamingChoices(index=0, finish_reason="stop", delta=Delta())]))
    return chunks


def tool_call_stream(name: str, arguments: dict, call_id: str = "call_1"):
    call = ChatCompletionDeltaToolCall(
        id=call_id, type="function", index=0, function=Function(name=name, arguments=json.dumps(arguments))
    )
    return [
        ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(role="assistant", tool_calls=[call]))]),
        ModelResponseStream(choices=[StreamingChoices(index=0, finish_reason="tool_calls", delta=Delta())]),
    ]


class FakeLLM:
    """Replays scripted streams and records every request (messages snapshotted)."""

    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.calls: list[dict] = []

    def __call__(self, **params):
        self.calls.append({**params, "messages": [dict(m) for m in params["messages"]]})
        script = self.scripts.pop(0)
        if isinstance(script, Exception):
            raise script
        return iter(script)


def stream_events(redis, run) -> list[tuple[str, dict]]:
    return [(f["event"], json.loads(f["data"])) for _, f in redis.xrange(events.stream_key(run.pk))]
