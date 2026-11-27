from datetime import timedelta

import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils import timezone

from hello_guy_ai.core import engine, prompt
from hello_guy_ai.core.safe_expr import ExpressionError, compile_expression, evaluate
from hello_guy_ai.models import Run, Source, Thread, Trigger
from hello_guy_ai.services import triggers
from hello_guy_ai.tasks.actors import fire_trigger
from hello_guy_ai.tests.conftest import FakeLLM, text_stream, tool_call_stream
from hello_guy_ai.tests.demo_crm.models import Lead

pytestmark = pytest.mark.django_db
NAMES = {"obj", "event"}


# ---- safe expressions ----------------------------------------------------------


def test_safe_expr_allows_the_useful_subset():
    obj = {"status": "new", "value": 5000, "tags": "VIP, Retail", "email": None}
    assert evaluate('obj.status == "new" and obj.value >= 1000', {"obj": obj, "event": "created"})
    assert evaluate('"vip" in lower(obj.tags) and not obj.email', {"obj": obj, "event": "x"})
    assert evaluate('obj.status in ["new", "contacted"] and event != "deleted"', {"obj": obj, "event": "created"})
    assert not evaluate("obj.email > 5", {"obj": obj, "event": "x"})  # None > 5 -> false, not a crash
    assert not evaluate("obj.missing == 1", {"obj": obj, "event": "x"})


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os').system('id')",
        "obj.__class__",
        "open('/etc/passwd')",
        "[x for x in obj]",
        "lambda: 1",
        "obj.status.upper()",
        "secret == 1",
        "x" * 1001,
        "len(obj, key=1)",
    ],
)
def test_safe_expr_rejects_dangerous_code(source):
    with pytest.raises(ExpressionError):
        compile_expression(source, NAMES)


# ---- validation ------------------------------------------------------------------


def test_trigger_validation(user):
    ct = ContentType.objects.get_for_model(Lead)
    bad = Trigger(
        name="t", agent_id="ghost", event="created", condition="obj.__dict__", prompt_template="{% if %}", run_as=user
    )
    with pytest.raises(ValidationError) as err:
        bad.full_clean()
    assert {"agent_id", "content_type", "condition", "prompt_template"} <= set(err.value.message_dict)
    with pytest.raises(ValidationError) as err:
        Trigger(
            name="s", agent_id="demo", event="scheduled", cron="every day", prompt_template="x", run_as=user
        ).full_clean()
    assert "cron" in err.value.message_dict
    own = ContentType.objects.get_for_model(Trigger)
    with pytest.raises(ValidationError):  # cannot watch the AI app's own models
        Trigger(
            name="t", agent_id="demo", event="created", content_type=own, prompt_template="x", run_as=user
        ).full_clean()
    Trigger(
        name="ok",
        agent_id="demo",
        event="created",
        content_type=ct,
        prompt_template="New lead {{ obj.name }}",
        run_as=user,
    ).full_clean()


# ---- dispatch -----------------------------------------------------------------------


def lead_trigger(user, **kw):
    defaults = {
        "name": "New lead",
        "agent_id": "demo",
        "event": "created",
        "content_type": ContentType.objects.get_for_model(Lead),
        "prompt_template": "Lead {{ obj.name }} ({{ obj.value }}) was {{ event }}.",
        "run_as": user,
    }
    return Trigger.objects.create(**{**defaults, **kw})


def test_model_save_enqueues_matching_triggers_on_commit(user, broker, django_capture_on_commit_callbacks):
    lead_trigger(user)
    lead_trigger(user, name="On delete", event="deleted")
    with django_capture_on_commit_callbacks(execute=True):
        lead = Lead.objects.create(name="Acme")
    queue = broker.queues[fire_trigger.queue_name]
    assert queue.qsize() == 1
    with django_capture_on_commit_callbacks(execute=True):
        lead.delete()
    assert queue.qsize() == 2
    msg = broker.queues[fire_trigger.queue_name].queue[-1] if hasattr(queue, "queue") else None
    assert msg is None or b"Acme" in msg  # deleted rows travel as a snapshot


def test_index_cache_invalidates_on_trigger_change(user, broker, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        Lead.objects.create(name="before")  # builds + caches an empty index
    lead_trigger(user)
    with django_capture_on_commit_callbacks(execute=True):
        Lead.objects.create(name="after")
    assert broker.queues[fire_trigger.queue_name].qsize() == 1


def test_fire_runs_agent_as_run_as_with_condition(user, monkeypatch):
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="demo_crm"))
    trigger = lead_trigger(user, condition="obj.value >= 1000", auto_approve=True)
    small = Lead.objects.create(name="Tiny", value=10)
    assert triggers.fire(trigger.pk, str(small.pk)) is None  # condition false

    big = Lead.objects.create(name="Acme", value=5000, owner=user)  # row scope: non-staff can touch own leads
    llm = FakeLLM(
        tool_call_stream("db_update", {"model": "demo_crm.lead", "ids": [big.pk], "values": {"status": "contacted"}}),
        text_stream("Marked contacted."),
    )
    monkeypatch.setattr(engine.litellm, "completion", llm)
    monkeypatch.setattr(prompt.litellm, "completion", llm)
    monkeypatch.setattr(prompt, "RagService", lambda: type("NoRag", (), {"search": lambda *a, **k: []})())
    run = triggers.fire(trigger.pk, str(big.pk))

    assert run.status == "succeeded" and run.source == Source.TRIGGER and run.trigger == trigger
    assert run.thread.created_by == user and run.thread.source == Source.TRIGGER and run.thread.name == "⚡ New lead"
    first = run.thread.messages.first().content
    assert first == triggers.UNTRUSTED_RECORD_NOTE + "Lead Acme (5000.0) was created."  # record data marked untrusted
    big.refresh_from_db()
    assert big.status == "contacted"  # auto_approve: unattended write went through
    assert Run.objects.filter(source=Source.TRIGGER).count() == 1  # the update did NOT re-fire (recursion guard)


def test_fire_skips_deleted_rows_and_inactive_run_as(user):
    trigger = lead_trigger(user)
    assert triggers.fire(trigger.pk, "999") is None
    user.is_active = False
    user.save()
    assert triggers.fire(trigger.pk, "1") is None


def test_trigger_threads_hidden_from_chat_list(api_client, user):
    Thread.objects.create(created_by=user, assistant_id="demo", source=Source.TRIGGER)
    assert api_client.get("/api/ai/threads").json()["count"] == 0


def test_scheduled_triggers_fire_exactly_once(user):
    trigger = Trigger.objects.create(
        name="Daily", agent_id="demo", event="scheduled", cron="0 9 * * *", prompt_template="Report", run_as=user
    )
    Trigger.objects.filter(pk=trigger.pk).update(created_at=timezone.now() - timedelta(days=3))
    now = timezone.now()
    assert triggers.due_scheduled_triggers(now) == [trigger.pk]
    assert triggers.due_scheduled_triggers(now) == []  # claimed; a second scheduler gets nothing
    assert triggers.due_scheduled_triggers(now + timedelta(days=1, minutes=1)) == [trigger.pk]


def test_scheduler_command_once(user, broker):
    from django.core.management import call_command

    trigger = Trigger.objects.create(
        name="Hourly", agent_id="demo", event="scheduled", cron="0 * * * *", prompt_template="x", run_as=user
    )
    Trigger.objects.filter(pk=trigger.pk).update(created_at=timezone.now() - timedelta(hours=2))
    call_command("hello_guy_scheduler", "--once")
    assert broker.queues[fire_trigger.queue_name].qsize() == 1


def test_scheduler_notifies_due_follow_ups_and_survives_its_failure(monkeypatch, caplog, fake_redis):
    from django.core.management import call_command
    from hello_guy_ai.management.commands import hello_guy_scheduler as scheduler

    from core import follow_ups as global_followups

    monkeypatch.setattr(scheduler, "get_redis", lambda: fake_redis)  # the command binds get_redis at first import
    calls = []
    monkeypatch.setattr(global_followups, "notify_due_follow_ups", lambda *a, **k: calls.append(1) or 0)
    call_command("hello_guy_scheduler", "--once")
    assert calls == [1]

    def boom(*a, **k):
        raise RuntimeError("db blip")

    monkeypatch.setattr(global_followups, "notify_due_follow_ups", boom)
    fake_redis.delete(scheduler.LOCK_KEY)  # fakeredis has no EVAL, so release the leader lock instead of renewing it
    call_command("hello_guy_scheduler", "--once")  # logged, not raised
    assert "Could not notify due follow-ups" in caplog.text


def test_cache_or_broker_outage_never_breaks_saves(user, monkeypatch, django_capture_on_commit_callbacks):
    lead_trigger(user)

    def boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(fire_trigger, "send", boom)
    with django_capture_on_commit_callbacks(execute=True):
        Lead.objects.create(name="Saved anyway")  # broker down after commit
    monkeypatch.setattr(triggers.cache, "get", boom)
    with django_capture_on_commit_callbacks(execute=True):
        Lead.objects.create(name="Also saved")  # cache down before commit
    assert Lead.objects.count() == 2
