"""End-to-end tests for WebsiteAssistant, staff agents, tools, reminders, notifications & confirmations."""

import json
from datetime import datetime
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.utils import timezone

from cms.models import BlogPost
from core import follow_ups as global_followups
from core.models import FollowUp, GlobalNotification
from crm.models import Opportunity, Service
from hello_guy_ai.core.runtime import ToolContext, registered_tools, tool_context
from hello_guy_ai.models import Run, Thread
from hello_guy_ai.services import chat
from hello_guy_ai.tests.conftest import FakeLLM, text_stream, tool_call_stream
from hello_guy_ai.tests.test_engine import execute

pytestmark = pytest.mark.django_db
IST = ZoneInfo("Asia/Kolkata")


def ist(*args) -> datetime:
    return datetime(*args, tzinfo=IST)


@pytest.fixture
def freeze(monkeypatch):
    def set_now(dt: datetime) -> None:
        monkeypatch.setattr(timezone, "now", lambda: dt.astimezone(dt_timezone.utc))

    return set_now


@pytest.fixture
def team_and_bot(django_user_model):
    call_command("setup_website_bot")
    staff = django_user_model.objects.create_superuser("boss", "boss@example.com", "pass12345")
    bot = chat.public_user()
    return staff, bot


def _public_run(bot, text: str, tz: str = "Asia/Kolkata") -> Run:
    thread = chat.external_thread(bot, "website_assistant", "web:visitor1", "Website visitor")
    thread.timezone = tz
    thread.save(update_fields=["timezone"])
    return _run_on_thread(bot, thread, text)


def _run_on_thread(user, thread: Thread, text: str) -> Run:
    from hello_guy_ai.models import Message

    run = Run.objects.create(thread=thread)
    Message.objects.create(thread=thread, run=run, role=Message.Role.USER, content=text)
    return run


def test_website_chat_inquiry_with_exact_meeting_date_and_time(
    team_and_bot, freeze, monkeypatch, django_capture_on_commit_callbacks
):
    """Visitor interested in services + '24 December 5 PM' -> Customer, Opportunity (with services),
    1 CRM FollowUp + 1 FollowUp at 24 Dec 17:00 IST (no duplicate +24h reminder), and staff notifications."""
    staff, bot = team_and_bot
    freeze(ist(2026, 11, 27, 15, 0))
    s1 = Service.objects.create(name="AI Chatbots")
    s2 = Service.objects.create(name="Web Development")

    llm = FakeLLM(
        tool_call_stream(
            "create_inquiry",
            {
                "name": "Aarav",
                "email": "aarav@example.com",
                "message": "Interested in AI Chatbots and Web Development. Can we meet on 24 December at 5 PM?",
                "service": "AI Chatbots, Web Development",
                "preferred_date": "2026-12-24",
                "preferred_time": "17:00",
            },
        ),
        text_stream("Thanks Aarav! Our team will reach out to confirm Dec 24 at 5 PM."),
    )

    run = _public_run(bot, "Interested in AI Chatbots & Web Development. Meeting on 24 Dec 5 PM?")
    with django_capture_on_commit_callbacks(execute=True):
        run = execute(run, llm, monkeypatch)

    assert run.status == Run.Status.SUCCEEDED  # public chat never pauses for internal approval
    opp = Opportunity.objects.get()
    assert opp.customer.email == "aarav@example.com"
    assert set(opp.services.all()) == {s1, s2}
    assert opp.next_follow_up == ist(2026, 12, 24, 17, 0)

    # Single unified FollowUp linked to customer & opportunity (no extra +24h duplicate)
    gfu = FollowUp.objects.get()
    assert gfu.customer == opp.customer
    assert gfu.opportunity == opp
    assert gfu.channel.name == "Meeting"
    assert gfu.due_at == ist(2026, 12, 24, 17, 0)
    assert gfu.assigned_to == staff

    # Immediate staff bell notifications (new customer, new opportunity, scheduled follow-up)
    titles = list(GlobalNotification.objects.filter(user=staff).values_list("title", flat=True))
    assert any("New opportunity: Aarav" in t for t in titles)
    assert any(t.startswith("Follow-up:") for t in titles)

    # Does not fire on Dec 23; fires once on Dec 24 at 5 PM
    freeze(ist(2026, 12, 23, 10, 0))
    assert global_followups.notify_due_follow_ups() == 0
    freeze(ist(2026, 12, 24, 17, 1))
    assert global_followups.notify_due_follow_ups() == 1
    assert global_followups.notify_due_follow_ups() == 0
    assert GlobalNotification.objects.filter(user=staff, title__startswith="Follow-up due:").count() == 1


def test_website_chat_date_without_time_schedules_reminder_day_before(
    team_and_bot, freeze, monkeypatch, django_capture_on_commit_callbacks
):
    """Visitor only says '24 December' (NO time) -> reminder & follow-up set for 23 December 10:00 AM IST
    to ask and confirm the meeting time."""
    staff, bot = team_and_bot
    freeze(ist(2026, 11, 27, 15, 0))
    Service.objects.create(name="AI Chatbots")

    llm = FakeLLM(
        tool_call_stream(
            "create_inquiry",
            {
                "name": "Meera",
                "email": "meera@example.com",
                "message": "Interested in AI Chatbots. Can we have a meeting on 24 December?",
                "service": "AI Chatbots",
                "preferred_date": "2026-12-24",
                "preferred_time": "",
            },
        ),
        text_stream("Thank you Meera! Our team will reach out to confirm a time for 24 December."),
    )

    run = _public_run(bot, "Meeting on 24 December?")
    with django_capture_on_commit_callbacks(execute=True):
        run = execute(run, llm, monkeypatch)

    assert run.status == Run.Status.SUCCEEDED
    opp = Opportunity.objects.get()
    assert opp.next_follow_up == ist(2026, 12, 23, 10, 0)

    gfu = FollowUp.objects.get()
    assert gfu.due_at == ist(2026, 12, 23, 10, 0)
    assert "no time specified" in gfu.note.lower()
    assert "24 Dec 2026" in gfu.note

    # Fires on 23 December 10:00 AM IST so team can message visitor before Dec 24
    freeze(ist(2026, 12, 23, 9, 59))
    assert global_followups.notify_due_follow_ups() == 0
    freeze(ist(2026, 12, 23, 10, 0))
    assert global_followups.notify_due_follow_ups() == 1
    due_notif = GlobalNotification.objects.filter(user=staff, title__startswith="Follow-up due:").get()
    assert "no time specified" in due_notif.body.lower()


def test_date_without_time_when_day_before_passed_schedules_same_day(team_and_bot, freeze):
    """If today is already 23 Dec 2 PM and visitor asks for 24 Dec (no time), schedule today +1h (3 PM)."""
    _, bot = team_and_bot
    freeze(ist(2026, 12, 23, 14, 0))

    with tool_context(ToolContext(user=bot, agent_key="website_assistant", timezone="Asia/Kolkata")):
        out = json.loads(
            registered_tools()["create_inquiry"].call(
                {
                    "name": "Karan",
                    "email": "karan@example.com",
                    "message": "Need a demo tomorrow 24 Dec",
                    "preferred_date": "2026-12-24",
                    "preferred_time": "",
                }
            )
        )
    assert out["scheduled_for_day_before"] is True
    assert FollowUp.objects.get().due_at == ist(2026, 12, 23, 15, 0)


def test_create_follow_up_shifts_date_only_to_day_before(team_and_bot, freeze):
    """Direct date-only `due_at='2026-12-24'` in create_follow_up schedules 23 Dec 10:00 AM."""
    staff, bot = team_and_bot
    freeze(ist(2026, 11, 27, 15, 0))

    with tool_context(ToolContext(user=bot, agent_key="website_assistant", timezone="Asia/Kolkata")):
        out = json.loads(
            registered_tools()["create_follow_up"].call(
                {
                    "title": "Follow up with visitor on meeting time",
                    "due_at": "2026-12-24",
                    "channel": "Meeting",
                }
            )
        )
    assert out["scheduled_for_day_before"] is True
    assert out["requested_date"] == "2026-12-24"
    gfu = FollowUp.objects.get(pk=out["follow_up_id"])
    assert gfu.due_at == ist(2026, 12, 23, 10, 0)
    assert gfu.assigned_to == staff


def test_staff_write_tools_pause_for_confirmation_and_honor_approve_or_deny(team_and_bot, monkeypatch):
    """Staff agents pause on confirmation-gated tools (create_blog_draft, update_opportunity_status)
    until the user clicks Approve or Deny."""
    staff, _ = team_and_bot
    opp = Opportunity.objects.create(
        customer=Opportunity._meta.get_field("customer").related_model.objects.create(name="Acme"),
        title="Chatbot",
    )

    # 1. update_opportunity_status via sales_assistant: pause -> deny -> unchanged
    t1 = Thread.objects.create(created_by=staff, assistant_id="sales_assistant")
    r1 = _run_on_thread(staff, t1, f"Mark {opp.reference} as contacted")
    r1 = execute(
        r1,
        FakeLLM(tool_call_stream("update_opportunity_status", {"reference": opp.reference, "status": "contacted"}, "c1")),
        monkeypatch,
    )
    assert r1.status == Run.Status.PAUSED
    assert Opportunity.objects.get(pk=opp.pk).status == Opportunity.Status.NEW

    Run.objects.filter(pk=r1.pk).update(status=Run.Status.QUEUED, answers={"c1": "deny"})
    r1 = execute(r1, FakeLLM(), monkeypatch, resume=True)
    assert r1.status == Run.Status.SUCCEEDED
    assert Opportunity.objects.get(pk=opp.pk).status == Opportunity.Status.NEW

    # 2. create_blog_draft via content_writer: pause -> approve -> unpublished draft saved
    t2 = Thread.objects.create(created_by=staff, assistant_id="content_writer")
    r2 = _run_on_thread(staff, t2, "Write a draft blog about Django AI")
    r2 = execute(
        r2,
        FakeLLM(tool_call_stream("create_blog_draft", {"title": "Django AI", "content": "# Hello"}, "c2")),
        monkeypatch,
    )
    assert r2.status == Run.Status.PAUSED and not BlogPost.objects.exists()

    Run.objects.filter(pk=r2.pk).update(status=Run.Status.QUEUED, answers={"c2": "approve"})
    r2 = execute(r2, FakeLLM(text_stream("Draft saved.")), monkeypatch, resume=True)
    assert r2.status == Run.Status.SUCCEEDED
    post = BlogPost.objects.get()
    assert post.title == "Django AI" and post.is_published is False
