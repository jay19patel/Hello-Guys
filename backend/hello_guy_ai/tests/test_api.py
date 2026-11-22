import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import AsyncClient
from django.utils import timezone

from hello_guy_ai.events import RunEventPublisher
from hello_guy_ai.models import AgentMemory, Document, Message, Run, Thread
from hello_guy_ai.tasks import ingest_document, resume_assistant, run_assistant

pytestmark = pytest.mark.django_db
JSON = "application/json"
QUESTIONS = [{"key": "c1", "tool": "db_create", "prompt": "Create 1 lead", "arguments": "{}"}]


def create_thread(client):
    res = client.post("/api/ai/threads", {"assistant_id": "hello_guy"}, content_type=JSON)
    assert res.status_code == 201, res.content
    return res.json()["id"]


def paused_run(user):
    thread = Thread.objects.create(created_by=user, assistant_id="hello_guy")
    return Run.objects.create(thread=thread, status=Run.Status.PAUSED, questions=QUESTIONS)


def test_requires_login(client):
    assert client.get("/api/ai/assistants").status_code == 401


def test_list_assistants(api_client):
    assert "hello_guy" in [a["id"] for a in api_client.get("/api/ai/assistants").json()]


def test_send_message_queues_run(api_client, django_capture_on_commit_callbacks, broker):
    thread_id = create_thread(api_client)
    with django_capture_on_commit_callbacks(execute=True):
        res = api_client.post(f"/api/ai/threads/{thread_id}/messages", {"content": "hello"}, content_type=JSON)
    assert res.status_code == 202, res.content
    body = res.json()
    assert body["stream_url"] == f"/api/ai/runs/{body['run_id']}/stream"
    assert broker.queues[run_assistant.queue_name].qsize() == 1
    assert Thread.objects.get(pk=thread_id).name == "hello"
    detail = api_client.get(f"/api/ai/threads/{thread_id}").json()
    assert detail["active_run"]["status"] == "queued"
    busy = api_client.post(f"/api/ai/threads/{thread_id}/messages", {"content": "again"}, content_type=JSON)
    assert busy.status_code == 409


def test_unknown_assistant_and_validation(api_client):
    assert api_client.post("/api/ai/threads", {"assistant_id": "ghost"}, content_type=JSON).status_code == 404
    thread_id = create_thread(api_client)
    res = api_client.post(f"/api/ai/threads/{thread_id}/messages", {"content": ""}, content_type=JSON)
    assert res.status_code == 422


def test_cannot_touch_other_users_threads_or_runs(api_client, django_user_model):
    other = django_user_model.objects.create_user("other", password="pass12345")
    run = paused_run(other)
    assert api_client.get(f"/api/ai/threads/{run.thread.pk}").status_code == 404
    assert api_client.get(f"/api/ai/runs/{run.pk}").status_code == 404
    res = api_client.post(f"/api/ai/runs/{run.pk}/resume", {"answers": {"c1": "approve"}}, content_type=JSON)
    assert res.status_code == 404
    assert api_client.post(f"/api/ai/runs/{run.pk}/stop").status_code == 404
    assert api_client.get("/api/ai/threads").json()["count"] == 0


def test_paused_run_flow(api_client, user, django_capture_on_commit_callbacks, broker, fake_redis):
    run = paused_run(user)
    RunEventPublisher(run.pk, client=fake_redis).publish("paused", {})
    thread_url = f"/api/ai/threads/{run.thread.pk}"

    active = api_client.get(thread_url).json()["active_run"]
    assert active["questions"] == [
        {"key": "c1", "tool": "db_create", "prompt": "Create 1 lead", "options": ["approve", "deny"]}
    ]
    assert "arguments" not in active["questions"][0]  # raw args never reach the browser
    blocked = api_client.post(f"{thread_url}/messages", {"content": "hi"}, content_type=JSON)
    assert blocked.status_code == 409 and "approval" in blocked.json()["message"]

    url = f"/api/ai/runs/{run.pk}/resume"
    assert api_client.post(url, {"answers": {}}, content_type=JSON).status_code == 400  # must answer all
    assert api_client.post(url, {"answers": {"c1": "x", "zz": "y"}}, content_type=JSON).status_code == 400
    with django_capture_on_commit_callbacks(execute=True):
        ok = api_client.post(url, {"answers": {"c1": "approve"}}, content_type=JSON)
    assert ok.status_code == 202 and "?after=" in ok.json()["stream_url"]
    assert broker.queues[resume_assistant.queue_name].qsize() == 1
    run.refresh_from_db()
    assert run.status == "queued" and run.answers == {"c1": "approve"}
    assert api_client.post(url, {"answers": {"c1": "approve"}}, content_type=JSON).status_code == 409  # double click


def test_stop_paused_and_queued(api_client, user, fake_redis):
    run = paused_run(user)
    assert api_client.post(f"/api/ai/runs/{run.pk}/stop").json() == {"status": "stopped"}
    run.refresh_from_db()
    assert run.status == "stopped" and run.questions is None

    queued = Run.objects.create(thread=Thread.objects.create(created_by=user, assistant_id="hello_guy"))
    assert api_client.post(f"/api/ai/runs/{queued.pk}/stop").json() == {"status": "stopped"}
    running = Run.objects.create(thread=queued.thread, status=Run.Status.RUNNING)
    assert api_client.post(f"/api/ai/runs/{running.pk}/stop").json() == {"status": "stopping"}
    assert fake_redis.exists(f"hello_guy_ai:run:{running.pk}:stop")


def test_feedback(client, user, staff):
    for who in (user, staff):
        client.force_login(who)
        thread = Thread.objects.create(created_by=who, assistant_id="hello_guy")
        run = Run.objects.create(thread=thread, status=Run.Status.SUCCEEDED)
        url = f"/api/ai/runs/{run.pk}/feedback"
        res = client.post(url, {"rating": "down", "comment": "Use INR not USD"}, content_type=JSON).json()
        assert res["rating"] == "down"
        # staff feedback becomes shared agent memory; a normal user's cannot (anti-poisoning)
        assert (res["memory_id"] is not None) is who.is_staff
    assert AgentMemory.objects.filter(origin="feedback", agent_key="hello_guy").count() == 1
    unfinished = Run.objects.create(thread=Thread.objects.create(created_by=staff, assistant_id="hello_guy"))
    assert client.post(f"/api/ai/runs/{unfinished.pk}/feedback", {"rating": "up"}, content_type=JSON).status_code == 409


def test_messages_hide_tool_rows_by_default(api_client, user):
    thread = Thread.objects.create(created_by=user, assistant_id="hello_guy")
    Message.objects.create(thread=thread, role="user", content="q")
    Message.objects.create(thread=thread, role="assistant", content="", tool_calls=[{"id": "1"}])
    Message.objects.create(thread=thread, role="tool", content="r", tool_call_id="1", name="t")
    Message.objects.create(thread=thread, role="assistant", content="a")
    items = api_client.get(f"/api/ai/threads/{thread.pk}/messages").json()["items"]
    assert [m["content"] for m in items] == ["q", "a"]
    assert api_client.get(f"/api/ai/threads/{thread.pk}/messages?include_tools=true").json()["count"] == 4


def test_stale_run_unlocked_but_paused_waits(api_client, user, django_capture_on_commit_callbacks, broker):
    thread = Thread.objects.create(created_by=user, assistant_id="hello_guy")
    stale = Run.objects.create(thread=thread, status=Run.Status.RUNNING)
    Run.objects.filter(pk=stale.pk).update(last_active_at=timezone.now() - timezone.timedelta(days=1))
    with django_capture_on_commit_callbacks(execute=True):
        res = api_client.post(f"/api/ai/threads/{thread.pk}/messages", {"content": "hi"}, content_type=JSON)
    assert res.status_code == 202 and Run.objects.get(pk=stale.pk).status == "failed"


def test_document_upload_permissions(client, user, staff, kb, django_capture_on_commit_callbacks, broker):
    client.force_login(user)
    upload = SimpleUploadedFile("faq.md", b"Refunds in 30 days")
    assert client.post("/api/ai/documents", {"knowledge_base": "hello_guy", "file": upload}).status_code == 403
    assert client.get("/api/ai/knowledge-bases").status_code == 403

    client.force_login(staff)
    bad = SimpleUploadedFile("x.exe", b"MZ")
    assert client.post("/api/ai/documents", {"knowledge_base": "hello_guy", "file": bad}).status_code == 400
    ghost = SimpleUploadedFile("a.md", b"x")
    assert client.post("/api/ai/documents", {"knowledge_base": "ghost", "file": ghost}).status_code == 404
    with django_capture_on_commit_callbacks(execute=True):
        ok = client.post("/api/ai/documents", {"knowledge_base": "hello_guy", "file": SimpleUploadedFile("f.md", b"x")})
    assert ok.status_code == 202 and ok.json()["knowledge_base"] == "hello_guy"
    assert ok.json()["status"] == Document.Status.PENDING
    assert broker.queues[ingest_document.queue_name].qsize() == 1
    assert [k["slug"] for k in client.get("/api/ai/knowledge-bases").json()] == ["hello_guy"]


@pytest.mark.django_db(transaction=True)
def test_sse_streams_and_settled_fallbacks(user, fake_redis):
    thread = Thread.objects.create(created_by=user, assistant_id="hello_guy")
    run = Run.objects.create(thread=thread, status=Run.Status.RUNNING)
    pub = RunEventPublisher(run.pk, client=fake_redis)
    pub.publish("token", {"text": "Hi"})
    first = fake_redis.xrange(f"hello_guy_ai:run:{run.pk}")[0][0]
    pub.publish("done", {"message_id": 1, "content": "Hi"})

    client = AsyncClient()
    client.force_login(user)
    body = _collect(client, f"/api/ai/runs/{run.pk}/stream")
    assert "event: token" in body and "event: done" in body
    after = _collect(client, f"/api/ai/runs/{run.pk}/stream?after={first}")
    assert "event: token" not in after and "event: done" in after  # resume cursor skips old events

    fake_redis.delete(f"hello_guy_ai:run:{run.pk}")
    Run.objects.filter(pk=run.pk).update(status=Run.Status.PAUSED, questions=QUESTIONS)
    body = _collect(client, f"/api/ai/runs/{run.pk}/stream")
    assert body.startswith("event: paused") and json.loads(body.split("data: ")[1])["questions"][0]["key"] == "c1"


def test_sse_streams_under_wsgi_too(user, fake_redis):
    from django.test import Client

    run = Run.objects.create(thread=Thread.objects.create(created_by=user, assistant_id="hello_guy"), status="running")
    RunEventPublisher(run.pk, client=fake_redis).publish("done", {"content": "Hi"})
    client = Client()  # WSGI request: must get a sync iterator, or runserver buffers the whole run
    client.force_login(user)
    res = client.get(f"/api/ai/runs/{run.pk}/stream")
    assert not res.is_async and "event: done" in b"".join(res.streaming_content).decode()


def _collect(client, url) -> str:
    from asgiref.sync import async_to_sync

    async def go():
        res = await client.get(url)
        assert res.status_code == 200 and res["Content-Type"] == "text/event-stream"
        return "".join([c.decode() async for c in res.streaming_content])

    return async_to_sync(go)()


def test_history_keeps_failed_run_error_after_reload(user, client):
    thread = Thread.objects.create(created_by=user, assistant_id="hello_guy")
    run = Run.objects.create(thread=thread, status=Run.Status.FAILED, error="RateLimitError: litellm 429 raw details")
    thread.messages.create(role="user", content="hi", run=run)
    client.force_login(user)
    [msg] = client.get(f"/api/ai/threads/{thread.pk}/messages").json()["items"]
    assert msg["run_status"] == "failed"
    assert "rate limit" in msg["run_error"] and "raw details" not in msg["run_error"]  # friendly, never raw
