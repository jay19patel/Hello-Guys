"""Live chat: the AI answers first; a team member can take over, reply, and hand back."""

import json

import pytest
from django.core.management import call_command

from hello_guy_ai.core import events
from hello_guy_ai.core.runtime import ToolContext, registered_tools, tool_context
from hello_guy_ai.models import Message, Notification, Run, Thread
from hello_guy_ai.services import chat
from meta_connector.models import Contact, Message as MetaMessage, PlatformAccount
from meta_connector.roles import AGENT, set_role

pytestmark = pytest.mark.django_db


@pytest.fixture
def bot():
    call_command("setup_website_bot")
    return chat.public_user()


@pytest.fixture
def agent(django_user_model):
    user = django_user_model.objects.create_user("agent", first_name="Priya")
    set_role(user, AGENT)
    return user


def thread_events(redis, thread):
    return [(f["event"], json.loads(f["data"])) for _, f in redis.xrange(events.thread_stream_key(thread.pk))]


def test_team_takes_over_a_website_chat_and_replies_live(bot, agent, client, fake_redis, django_capture_on_commit_callbacks):
    thread_id = client.post("/api/ai/public/threads").json()["thread_id"]
    thread = Thread.objects.get(pk=thread_id)

    client.force_login(agent)
    client.post(f"/crm/inbox/{thread_id}/", {"action": "take"})
    client.post(f"/crm/inbox/{thread_id}/", {"action": "reply", "content": "Hi, Priya here!"})
    client.logout()

    with django_capture_on_commit_callbacks(execute=True):
        res = client.post(f"/api/ai/public/threads/{thread_id}/messages", {"content": "Great, my budget is 5k"},
                          content_type="application/json")
    assert res.status_code == 202 and res.json()["mode"] == "human" and res.json()["run_id"] is None
    assert not Run.objects.filter(thread=thread).exists()  # no AI while a person handles it

    kinds = [(e, d.get("content")) for e, d in thread_events(fake_redis, thread)]
    assert ("message", "Hi, Priya here!") in kinds and ("message", "Great, my budget is 5k") in kinds
    staff = Message.objects.get(thread=thread, role="assistant")
    assert staff.name == "staff:Priya"
    assert Notification.objects.filter(user=agent, title__startswith="New message").exists()

    client.force_login(agent)
    data = client.get(f"/crm/inbox/{thread_id}/?format=json").json()
    assert [m["who"] for m in data["messages"]] == ["staff", "visitor"] and data["human"]
    client.post(f"/crm/inbox/{thread_id}/", {"action": "release"})
    thread.refresh_from_db()
    assert not thread.human_mode and thread.handled_by is None


def test_ai_or_visitor_asks_for_a_person(bot, agent, client, django_capture_on_commit_callbacks):
    thread_id = client.post("/api/ai/public/threads").json()["thread_id"]
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(f"/api/ai/public/threads/{thread_id}/handoff").status_code == 202
    thread = Thread.objects.get(pk=thread_id)
    assert thread.needs_human
    assert Notification.objects.get(user=agent).url == f"/crm/inbox/{thread_id}/"

    other = chat.external_thread(bot, "website_assistant", "web:other", "v2")
    with tool_context(ToolContext(user=bot, agent_key="website_assistant", thread_id=other.pk)):
        assert json.loads(registered_tools()["request_human"].call({"reason": "wants a discount"}))["status"]
    other.refresh_from_db()
    assert other.needs_human


def test_dm_ai_stays_quiet_once_handed_to_the_team(bot):
    from meta_connector import ai_reply as meta_ai

    account = PlatformAccount.objects.create(platform="whatsapp", display_name="WA", phone_number_id="P", ai_auto_reply=True)
    contact = Contact.objects.create(platform_account=account, platform_user_id="9111")
    thread = chat.external_thread(bot, "website_assistant", meta_ai.thread_key(account, contact), "dm")
    msg = MetaMessage.objects.create(platform_account=account, contact=contact, message_id="w1", message_text="hi")
    assert meta_ai.skip_reason(msg, bot) is None
    chat.request_human(thread, "complaint")
    assert meta_ai.skip_reason(msg, bot) == "handed to the team"


def test_ai_turn_refused_while_human_mode(bot, agent):
    thread = chat.external_thread(bot, "website_assistant", "web:z", "v")
    chat.take_over(thread, agent)
    from hello_guy_ai.exceptions import ThreadBusy

    with pytest.raises(ThreadBusy):
        chat.post_message(bot, thread, "hello")
