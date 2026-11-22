import pytest
from django.core.management import call_command

from cms.models import BlogPost
from hello_guy_ai import services
from hello_guy_ai.agents import resolve_agent
from hello_guy_ai.exceptions import NotAllowed
from hello_guy_ai.models import Thread
from hello_guy_ai.runtime import ToolContext, registered_tools, tool_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def bot():
    call_command("setup_website_bot")
    return services.public_user()


def test_website_bot_is_locked_down(bot):
    assert not (bot.is_staff or bot.is_superuser or bot.has_usable_password())
    with pytest.raises(NotAllowed):
        resolve_agent("hello_guy", bot)  # public user runs only the public agent
    BlogPost.objects.create(title="Draft", slug="draft", content="x", is_published=False)
    tools = registered_tools()
    with tool_context(ToolContext(user=bot, agent_key="website_assistant")):
        assert "draft" not in tools["search_blog_posts"].call({})  # visitors never see drafts
        assert tools["list_inquiries"].call({}).startswith("Error: You don't have permission")
        assert tools["create_blog_draft"].call({"title": "t", "content": "c"}).startswith("Error: You don't have permission")


def test_public_api_needs_no_login_but_never_reaches_internal_chats(bot, client, user):
    assert client.get("/api/ai/public/agent").json()["name"] == "Website Assistant"
    res = client.post("/api/ai/public/threads")
    assert res.status_code == 201 and Thread.objects.get(pk=res.json()["thread_id"]).created_by == bot
    internal = Thread.objects.create(created_by=user, assistant_id="hello_guy")
    assert client.get(f"/api/ai/public/threads/{internal.pk}/messages").status_code == 404
