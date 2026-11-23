"""Reminders and notifications, including acceptance scenarios 1 and 2 end to end (LLM mocked, time frozen)."""

import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.utils import timezone

from hello_guy_ai.core.runtime import ToolContext, registered_tools, tool_context
from hello_guy_ai.models import Notification, Reminder, Run
from hello_guy_ai.services import reminders
from hello_guy_ai.tasks import actors
from hello_guy_ai.tests.conftest import FakeLLM, text_stream, tool_call_stream
from hello_guy_ai.tests.test_engine import execute, make_run

pytestmark = pytest.mark.django_db
IST = ZoneInfo("Asia/Kolkata")
TOOLS = registered_tools()


def ist(*args) -> datetime:
    return datetime(*args, tzinfo=IST)


@pytest.fixture
def freeze(monkeypatch):
    def set_now(dt: datetime) -> None:
        monkeypatch.setattr(timezone, "now", lambda: dt.astimezone(dt_timezone.utc))

    return set_now


def call(user, name, tz="Asia/Kolkata", **args):
    t = TOOLS[name]
    with tool_context(ToolContext(user=user, agent_key="demo", timezone=tz)):
        parsed = t.parse_args(json.dumps(args))
        return t, parsed, t.call(parsed)


def test_acceptance_1_invoice_reminder_today_5pm(user, freeze, monkeypatch, settings, django_capture_on_commit_callbacks):
    """'Aaj 5 baje mujhe yaad dilana ki client ko invoice bhejna hai' -> confirm card in IST -> exactly one notification."""
    settings.HELLO_GUY_AI = {**settings.HELLO_GUY_AI, "NOTIFY_EMAIL": True}
    freeze(ist(2026, 11, 23, 11, 30))
    run = make_run(user, "Aaj 5 baje mujhe yaad dilana ki client ko invoice bhejna hai")
    run.thread.timezone = "Asia/Kolkata"
    run.thread.save()

    args = {"message": "Send invoice to client", "due_at": "2026-11-23T17:00"}
    run = execute(run, FakeLLM(tool_call_stream("create_reminder", args)), monkeypatch)
    assert run.status == Run.Status.PAUSED and not Reminder.objects.exists()  # nothing saved before approval
    assert run.questions[0]["prompt"] == "Reminder: Send invoice to client\nToday 5:00 PM (Asia/Kolkata)"

    Run.objects.filter(pk=run.pk).update(status=Run.Status.QUEUED, answers={"call_1": "approve"})
    run = execute(run, FakeLLM(text_stream("Done, I'll remind you at 5 PM.")), monkeypatch, resume=True)
    reminder = Reminder.objects.get()
    assert run.status == Run.Status.SUCCEEDED and reminder.user == user
    assert reminder.due_at == ist(2026, 11, 23, 17, 0) and reminder.timezone == "Asia/Kolkata"

    assert reminders.fire_due_reminders(now=ist(2026, 11, 23, 16, 59)) == 0
    sent = []
    monkeypatch.setattr(actors.send_notification_email, "send", lambda pk: sent.append(pk))
    with django_capture_on_commit_callbacks(execute=True):
        assert reminders.fire_due_reminders(now=ist(2026, 11, 23, 17, 0, 10)) == 1
    # A second scheduler, a restart or a retry: nothing fires twice.
    assert reminders.fire_due_reminders(now=ist(2026, 11, 23, 17, 0, 40)) == 0
    note = Notification.objects.get()
    assert (note.user, note.body, note.is_read) == (user, "Send invoice to client", False)
    assert Reminder.objects.get().status == Reminder.Status.DONE

    assert sent == [note.pk]  # email channel enabled -> queued once
    reminders.send_notification_email(note.pk)
    assert mail.outbox[0].to == [user.email] and mail.outbox[0].body == "Send invoice to client"


def test_past_time_asks_instead_of_asking_for_approval(user, freeze):
    freeze(ist(2026, 11, 23, 18, 0))
    tool, args, out = call(user, "create_reminder", message="Send invoice", due_at="2026-11-23T17:00")
    with tool_context(ToolContext(user=user, agent_key="demo", timezone="Asia/Kolkata")):
        assert not tool.needs_confirmation(args)  # invalid: no approval card, the LLM gets the error and asks
    assert out.startswith("Error: That time has already passed (it is now 2026-11-23 18:00 Asia/Kolkata)")
    assert "next day" in out and not Reminder.objects.exists()


def test_acceptance_2_weekly_recurring_list_and_cancel(user, freeze, monkeypatch):
    """'Har Monday 10 baje weekly sales summary bhejo' -> recurring task, list and cancel work."""
    freeze(ist(2026, 11, 23, 12, 0))  # a Monday, after 10:00
    _, _, out = call(user, "create_reminder", message="Weekly sales summary", recurrence="0 10 * * 1", run_assistant=True)
    reminder = Reminder.objects.get()
    assert reminder.due_at == ist(2026, 11, 30, 10, 0) and reminder.agent_id == "demo"
    assert "Mon 30 Nov 2026 10:00 AM (Asia/Kolkata)" in json.loads(out)["when"]

    _, _, listed = call(user, "list_reminders")
    assert [r["recurrence"] for r in json.loads(listed)] == ["0 10 * * 1"]

    monkeypatch.setattr(actors.run_reminder_agent, "send", lambda pk: None)
    assert reminders.fire_due_reminders(now=ist(2026, 11, 30, 10, 0, 5)) == 1
    reminder.refresh_from_db()
    assert reminder.status == Reminder.Status.SCHEDULED and reminder.due_at == ist(2026, 12, 7, 10, 0)

    call(user, "cancel_reminder", reminder_id=reminder.pk)
    assert Reminder.objects.get().status == Reminder.Status.CANCELLED
    assert reminders.fire_due_reminders(now=ist(2026, 12, 7, 10, 0, 5)) == 0


def test_recurring_after_long_outage_fires_once(user):
    reminder = Reminder.objects.create(
        user=user, message="Standup", due_at=ist(2026, 11, 1, 9, 0), recurrence="0 9 * * *", timezone="Asia/Kolkata"
    )
    assert reminders.fire_due_reminders(now=ist(2026, 11, 23, 12, 0)) == 1  # 22 missed days -> one notification
    reminder.refresh_from_db()
    assert reminder.due_at == ist(2026, 11, 24, 9, 0) and Notification.objects.count() == 1


def test_scheduled_task_runs_assistant_and_notifies_answer(user, monkeypatch):
    reminder = Reminder.objects.create(
        user=user, message="Weekly sales summary", due_at=timezone.now(), timezone="UTC", agent_id="demo"
    )
    llm = FakeLLM(text_stream("Sales this week: Rs 10 lakh."))
    monkeypatch.setattr("hello_guy_ai.core.engine.litellm.completion", llm)
    reminders.run_reminder_agent(reminder.pk)
    note = Notification.objects.get()
    assert note.body == "Sales this week: Rs 10 lakh." and "thread=" in note.url


def test_list_notifications_shows_unseen_then_marks_them_read(user, django_user_model):
    reminders.notify(user, "Reminder", "Call Ravi")
    reminders.notify(user, "Reminder", "Pay rent")
    reminders.notify(django_user_model.objects.create_user("other"), "Reminder", "Not yours")

    _, _, out = call(user, "list_notifications")
    data = json.loads(out)
    assert {n["body"] for n in data["notifications"]} == {"Call Ravi", "Pay rent"} and data["unread_remaining"] == 0
    assert all(n.read_at for n in Notification.objects.filter(user=user))

    assert json.loads(call(user, "list_notifications")[2])["notifications"] == []  # nothing unseen now
    everything = json.loads(call(user, "list_notifications", unread_only=False)[2])["notifications"]
    assert len(everything) == 2 and not any(n["was_unread"] for n in everything)


def test_notification_and_reminder_api(api_client, user, django_user_model):
    mine = reminders.notify(user, "Reminder", "Mine")
    reminders.notify(django_user_model.objects.create_user("other"), "Reminder", "Theirs")
    assert api_client.get("/api/ai/notifications/unread-count").json() == {"count": 1}
    assert [n["body"] for n in api_client.get("/api/ai/notifications").json()["items"]] == ["Mine"]
    res = api_client.post("/api/ai/notifications/read", {"ids": [mine.pk]}, content_type="application/json")
    assert res.json() == {"count": 1} and api_client.get("/api/ai/notifications/unread-count").json() == {"count": 0}

    reminder = Reminder.objects.create(user=user, message="x", due_at=timezone.now() + timedelta(hours=1), timezone="UTC")
    assert [r["id"] for r in api_client.get("/api/ai/reminders").json()["items"]] == [reminder.pk]
    assert api_client.post(f"/api/ai/reminders/{reminder.pk}/cancel").status_code == 200
    assert api_client.post(f"/api/ai/reminders/{reminder.pk}/cancel").status_code == 404


def test_scheduled_panel_pause_resume_delete(api_client, user, django_user_model, freeze):
    """The chat UI's Scheduled panel: count, list, pause (never fires), resume (continues), delete; owner only."""
    freeze(ist(2026, 11, 23, 12, 0))
    weekly = Reminder.objects.create(
        user=user, message="Weekly report", due_at=ist(2026, 11, 30, 10, 0), recurrence="0 10 * * 1", timezone="Asia/Kolkata"
    )
    once = Reminder.objects.create(user=user, message="Call Ravi", due_at=ist(2026, 11, 23, 15, 0), timezone="Asia/Kolkata")
    other = Reminder.objects.create(
        user=django_user_model.objects.create_user("other"), message="x", due_at=ist(2027, 1, 1, 9, 0), timezone="UTC"
    )
    listing = api_client.get("/api/ai/reminders").json()
    assert listing["count"] == 2 and [r["message"] for r in listing["items"]] == ["Call Ravi", "Weekly report"]

    assert api_client.post(f"/api/ai/reminders/{weekly.pk}/pause").status_code == 200
    assert reminders.fire_due_reminders(now=ist(2026, 11, 30, 10, 0, 5)) == 1  # only "Call Ravi"; paused stays silent
    statuses = {r["message"]: r["status"] for r in api_client.get("/api/ai/reminders").json()["items"]}
    assert statuses == {"Weekly report": "paused"}  # "Call Ravi" fired (done) and left the list

    freeze(ist(2026, 12, 2, 12, 0))  # resumed after its slot passed: continues from the next Monday
    res = api_client.post(f"/api/ai/reminders/{weekly.pk}/resume")
    assert res.status_code == 200 and res.json()["status"] == "scheduled"
    assert Reminder.objects.get(pk=weekly.pk).due_at == ist(2026, 12, 7, 10, 0)

    assert api_client.post(f"/api/ai/reminders/{other.pk}/pause").status_code == 404  # not yours
    assert api_client.delete(f"/api/ai/reminders/{other.pk}").status_code == 404
    assert api_client.delete(f"/api/ai/reminders/{weekly.pk}").status_code == 204
    assert not Reminder.objects.filter(pk=weekly.pk).exists() and Reminder.objects.filter(pk=other.pk).exists()
    assert once.pk  # history of fired reminders is kept until pruned
