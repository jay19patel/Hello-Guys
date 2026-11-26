import json
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import Permission

from hello_guy_ai.core import engine, events, prompt
from hello_guy_ai.core.engine import AgentRunner
from hello_guy_ai.core.prompt import sanitize_history
from hello_guy_ai.models import AgentMemory, Message, Run, Thread
from hello_guy_ai.services.rag import Chunk
from hello_guy_ai.tests.conftest import FakeLLM, stream_events, text_stream, tool_call_stream
from hello_guy_ai.tests.demo_crm.models import Lead

pytestmark = pytest.mark.django_db


class StubRag:
    def __init__(self, chunks=()):
        self.chunks = list(chunks)
        self.queries = []

    def search(self, kb_ids, query, top_k, min_score):
        self.queries.append(query)
        return self.chunks


@pytest.fixture
def crm_user(user):
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="demo_crm"))
    return type(user).objects.get(pk=user.pk)  # drop perm cache


def make_run(user, content="hi", **run_fields):
    thread = Thread.objects.create(created_by=user, assistant_id="demo")
    run = Run.objects.create(thread=thread, **run_fields)
    Message.objects.create(thread=thread, run=run, role="user", content=content)
    return run


def execute(run, llm, monkeypatch, rag=None, resume=False):
    monkeypatch.setattr(engine.litellm, "completion", llm)
    monkeypatch.setattr(prompt.litellm, "completion", llm)
    runner = AgentRunner(Run.objects.get(pk=run.pk), rag=rag or StubRag())
    runner.resume() if resume else runner.execute()
    run.refresh_from_db()
    return run


def test_tool_call_then_answer(user, fake_redis, monkeypatch):
    llm = FakeLLM(tool_call_stream("get_current_datetime", {}), text_stream("It is noon."))
    run = execute(make_run(user), llm, monkeypatch)
    assert run.status == "succeeded" and run.iterations == 2 and run.config_snapshot["agent"] == "demo"
    assert list(run.thread.messages.values_list("role", flat=True)) == ["user", "assistant", "tool", "assistant"]
    sent = llm.calls[1]["messages"]
    assert sent[-2]["tool_calls"][0]["function"]["name"] == "get_current_datetime" and sent[-1]["role"] == "tool"
    names = [e for e, _ in stream_events(fake_redis, run)]
    assert names[0] == "status" and "tool_start" in names and names[-1] == "done"
    assert "".join(d["text"] for e, d in stream_events(fake_redis, run) if e == "token") == "It is noon."


def test_model_that_ignores_the_tool_less_last_round_gets_a_graceful_answer(user, monkeypatch):
    from hello_guy_ai.tests.demo_crm.ai_assistants import DemoAssistant

    monkeypatch.setattr(DemoAssistant, "max_tool_iterations", 1)
    llm = FakeLLM(tool_call_stream("get_current_datetime", {}, "c1"), tool_call_stream("get_current_datetime", {}, "c2"))
    run = execute(make_run(user), llm, monkeypatch)
    assert run.status == "succeeded" and run.iterations == 2 and "tools" not in llm.calls[1]
    assert run.thread.messages.filter(role="assistant").last().content == engine.OUT_OF_STEPS


def test_system_prompt_carries_current_date_and_thread_timezone(user, monkeypatch, settings):
    from django.utils import timezone

    settings.TIME_ZONE = "UTC"
    run = make_run(user)
    Thread.objects.filter(pk=run.thread_id).update(timezone="Asia/Kolkata")
    llm = FakeLLM(text_stream("ok"))
    execute(run, llm, monkeypatch)
    system = llm.calls[0]["messages"][0]["content"]
    today = timezone.now().astimezone(ZoneInfo("Asia/Kolkata"))
    assert f"{today:%A, %Y-%m-%d}" in system and "Asia/Kolkata" in system


def test_write_tool_pauses_then_approve_executes(crm_user, fake_redis, monkeypatch):
    args = {"model": "demo_crm.lead", "records": [{"name": "Acme", "status": "new"}]}
    run = execute(make_run(crm_user, "add lead Acme"), FakeLLM(tool_call_stream("db_create", args, "c1")), monkeypatch)
    assert run.status == "paused" and Lead.objects.count() == 0  # nothing written before approval
    [q] = run.questions
    assert q["key"] == "c1" and "Create 1 demo_crm.lead" in q["prompt"]
    assert stream_events(fake_redis, run)[-1][0] == "paused"

    Run.objects.filter(pk=run.pk).update(status="queued", answers={"c1": "approve"})
    llm = FakeLLM(text_stream("Lead Acme created."))
    run = execute(run, llm, monkeypatch, resume=True)
    assert run.status == "succeeded" and run.questions is None
    assert Lead.objects.get().name == "Acme"
    tool_msg = run.thread.messages.get(role="tool")
    assert json.loads(tool_msg.content)["created"] == [Lead.objects.get().pk]
    assert llm.calls[0]["messages"][-1]["role"] == "tool"  # LLM sees the real result


def test_deny_ends_turn_without_llm_call(crm_user, fake_redis, monkeypatch):
    args = {"model": "demo_crm.lead", "ids": [1]}
    run = execute(make_run(crm_user), FakeLLM(tool_call_stream("db_delete", args, "c1")), monkeypatch)
    Run.objects.filter(pk=run.pk).update(status="queued", answers={"c1": "deny"})
    llm = FakeLLM()  # any model call would pop from an empty script and fail
    run = execute(run, llm, monkeypatch, resume=True)
    assert run.status == "succeeded" and not llm.calls
    assert json.loads(run.thread.messages.get(role="tool").content)["status"] == "denied"
    assert stream_events(fake_redis, run)[-1] == ("done", {"denied": True})


def test_free_text_answer_redirects_llm(crm_user, monkeypatch):
    args = {"model": "demo_crm.lead", "records": [{"name": "Acme"}]}
    run = execute(make_run(crm_user), FakeLLM(tool_call_stream("db_create", args, "c1")), monkeypatch)
    Run.objects.filter(pk=run.pk).update(status="queued", answers={"c1": "Name it Acme Pvt Ltd"})
    llm = FakeLLM(tool_call_stream("db_create", {**args, "records": [{"name": "Acme Pvt Ltd"}]}, "c2"))
    run = execute(run, llm, monkeypatch, resume=True)
    feedback = json.loads(llm.calls[0]["messages"][-1]["content"])
    assert feedback["status"] == "redirect" and feedback["user_feedback"] == "Name it Acme Pvt Ltd"
    assert run.status == "paused" and run.questions[0]["key"] == "c2" and Lead.objects.count() == 0


def test_auto_approve_runs_write_tools(crm_user, monkeypatch):
    args = {"model": "demo_crm.lead", "records": [{"name": "Auto"}]}
    llm = FakeLLM(tool_call_stream("db_create", args), text_stream("done"))
    run = execute(make_run(crm_user, auto_approve=True), llm, monkeypatch)
    assert run.status == "succeeded" and Lead.objects.filter(name="Auto").exists()


def test_stop_mid_stream_keeps_partial_text(user, fake_redis, monkeypatch):
    run = make_run(user)
    events.request_stop(run.pk)
    monkeypatch.setattr(events, "stop_requested", lambda _pk: Run.objects.filter(pk=run.pk).exists() and _calls())
    seen = {"n": 0}

    def _calls():
        seen["n"] += 1
        return seen["n"] > 1  # first check (before streaming) passes, the mid-stream check stops

    run = execute(run, FakeLLM(text_stream("A long answer " * 10)), monkeypatch)
    assert run.status == "stopped" and stream_events(fake_redis, run)[-1][0] == "stopped"
    partial = run.thread.messages.get(role="assistant").content
    assert partial and len(partial) < len("A long answer " * 10)


def test_llm_failure_marks_run_failed(user, fake_redis, monkeypatch):
    run = execute(make_run(user), FakeLLM(ConnectionError("ollama down")), monkeypatch)
    assert run.status == "failed" and "ollama down" in run.error
    assert stream_events(fake_redis, run)[-1][0] == "error"


def test_unclaimable_run_is_ignored(user, monkeypatch):
    run = make_run(user, status=Run.Status.SUCCEEDED)
    llm = FakeLLM()
    assert execute(run, llm, monkeypatch).status == "succeeded" and not llm.calls  # duplicate delivery is harmless


def test_tool_loop_is_bounded(user, monkeypatch):
    loops = [tool_call_stream("get_current_datetime", {}, f"c{i}") for i in range(5)]
    llm = FakeLLM(*loops, text_stream("done"))
    run = execute(make_run(user), llm, monkeypatch)
    assert "tools" not in llm.calls[-1] and run.status == "succeeded"


def test_bad_tool_arguments_go_back_to_llm(user, monkeypatch):
    llm = FakeLLM(tool_call_stream("db_read", {"limit": 5}), text_stream("ok"))  # missing required `model`
    run = execute(make_run(user), llm, monkeypatch)
    assert run.thread.messages.get(role="tool").content.startswith("Error: invalid arguments")
    assert run.status == "succeeded"


def test_memory_and_auto_rag_reach_system_prompt(staff, kb, fake_redis, monkeypatch):
    AgentMemory.objects.create(agent_key="demo", scope="agent", content="Currency is INR")
    kb.documents.create(title="Pricing", file="pricing.txt", status="ready")
    run = make_run(staff, "aur uska price?")
    thread = run.thread
    Message.objects.filter(thread=thread).delete()
    Message.objects.create(thread=thread, role="user", content="Tell me about Plan Pro")
    Message.objects.create(thread=thread, role="assistant", content="Plan Pro is our top plan.")
    Message.objects.create(thread=thread, run=run, role="user", content="aur uska price?")

    rewrite = type(
        "R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "Plan Pro price"})})], "usage": None}
    )
    calls = []

    def llm(**params):
        calls.append(params)
        return rewrite if not params.get("stream") else iter(text_stream("Rs 999."))

    rag = StubRag([Chunk(doc_id=1, title="Pricing", text="Plan Pro costs Rs 999/month", score=0.9)])
    execute(run, llm, monkeypatch, rag=rag)
    system = calls[1]["messages"][0]["content"]
    assert rag.queries == ["Plan Pro price"]
    assert "Plan Pro costs Rs 999/month" in system and "untrusted" in system
    assert "<agent_memory>" in system and "Currency is INR" in system
    sources = [d for e, d in stream_events(fake_redis, run) if e == "sources"][0]["sources"]
    assert sources == [{"doc_id": 1, "title": "Pricing", "score": 0.9}]


def test_sanitize_history_drops_unanswered_and_orphan_tool_messages(user):
    thread = Thread.objects.create(created_by=user, assistant_id="demo")
    msgs = [
        Message(thread=thread, role="tool", content="orphan", tool_call_id="old", name="t"),
        Message(thread=thread, role="user", content="q"),
        Message(thread=thread, role="assistant", content="", tool_calls=[{"id": "a"}, {"id": "b"}]),
        Message(thread=thread, role="tool", content="ra", tool_call_id="a", name="t"),
        Message(thread=thread, role="assistant", content="partial", tool_calls=[{"id": "never"}]),
    ]
    out = sanitize_history(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "assistant"]
    assert [c["id"] for c in out[1]["tool_calls"]] == ["a"] and "tool_calls" not in out[3]


def test_retrieval_outage_degrades_gracefully(user, kb, monkeypatch):
    class DownRag:
        def search(self, *a, **k):
            raise ConnectionError("ollama down")

    kb.documents.create(title="Pricing", file="pricing.txt", status="ready")
    run = execute(make_run(user), FakeLLM(text_stream("Answer without docs.")), monkeypatch, rag=DownRag())
    assert run.status == "succeeded"


def test_auto_rag_skipped_when_kb_has_no_ready_documents(user, kb, monkeypatch):
    class NeverRag:
        def search(self, *a, **k):
            raise AssertionError("vector store must not be touched")

    kb.documents.create(title="Draft", file="draft.txt", status="pending")
    run = execute(make_run(user), FakeLLM(text_stream("Hi.")), monkeypatch, rag=NeverRag())
    assert run.status == "succeeded"
