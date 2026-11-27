import pytest
from django.core.management import call_command

from cms.models import BlogPost
from hello_guy_ai.core.runtime import ToolContext, registered_tools, tool_context
from hello_guy_ai.exceptions import NotAllowed
from hello_guy_ai.models import Thread
from hello_guy_ai.services import chat
from hello_guy_ai.services.agents import resolve_agent

pytestmark = pytest.mark.django_db


@pytest.fixture
def bot():
    call_command("setup_website_bot")
    return chat.public_user()


def test_website_bot_is_locked_down(bot):
    assert not (bot.is_staff or bot.is_superuser or bot.has_usable_password())
    with pytest.raises(NotAllowed):
        resolve_agent("hello_guy", bot)  # public user runs only the public agent
    BlogPost.objects.create(title="Draft", slug="draft", content="x", is_published=False)
    tools = registered_tools()
    with tool_context(ToolContext(user=bot, agent_key="website_assistant")):
        assert "draft" not in tools["search_blog_posts"].call({})  # visitors never see drafts
        assert tools["list_opportunities"].call({}).startswith("Error: You don't have permission")
        assert tools["create_blog_draft"].call({"title": "t", "content": "c"}).startswith("Error: You don't have permission")


def test_public_api_needs_no_login_but_never_reaches_internal_chats(bot, client, user):
    assert client.get("/api/ai/public/agent").json()["name"] == "Website Assistant"
    res = client.post("/api/ai/public/threads")
    assert res.status_code == 201 and Thread.objects.get(pk=res.json()["thread_id"]).created_by == bot
    internal = Thread.objects.create(created_by=user, assistant_id="hello_guy")
    assert client.get(f"/api/ai/public/threads/{internal.pk}/messages").status_code == 404


def test_visitors_on_one_ip_get_separate_threads_and_can_continue_their_own(bot, client):
    first = client.post("/api/ai/public/threads", REMOTE_ADDR="1.2.3.4").json()["thread_id"]
    second = client.post("/api/ai/public/threads", REMOTE_ADDR="1.2.3.4").json()["thread_id"]
    assert first != second  # same NAT/office: never share one conversation
    assert Thread.objects.get(pk=first).external_id != Thread.objects.get(pk=second).external_id
    for thread_id in (first, second):  # the id is the visitor's key: it reads the thread back from any connection
        assert client.get(f"/api/ai/public/threads/{thread_id}/messages", REMOTE_ADDR="5.6.7.8").status_code == 200


def test_bot_can_create_inquiries_in_crm_but_nothing_else_there(bot):
    from crm.models import Opportunity

    assert set(bot.get_all_permissions()) >= {"crm.add_opportunity", "crm.add_customer"}
    assert not {p for p in bot.get_all_permissions() if p.startswith("crm.")} - {"crm.add_opportunity", "crm.add_customer"}
    with tool_context(ToolContext(user=bot, agent_key="website_assistant")):
        tools = registered_tools()
        out = tools["create_inquiry"].call({"name": "Ravi", "email": "ravi@example.com", "message": "I need a chatbot"})
        assert '"status": "received"' in out
        assert tools["get_opportunity"].call({"reference": "opp_x"}).startswith("Error: You don't have permission")
    assert Opportunity.objects.get().customer.email == "ravi@example.com"


def test_public_message_passes_the_visitor_timezone_to_the_thread(bot, client, broker):
    thread_id = client.post("/api/ai/public/threads").json()["thread_id"]
    res = client.post(
        f"/api/ai/public/threads/{thread_id}/messages", {"content": "hi", "timezone": "Asia/Kolkata"}, content_type="application/json"
    )
    assert res.status_code == 202 and Thread.objects.get(pk=thread_id).timezone == "Asia/Kolkata"
