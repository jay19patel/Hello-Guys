"""Customers and their opportunities: from every channel, one customer per person, AI tools."""

import json

import pytest
from django.core.management import call_command

from crm import services
from crm.models import Activity, Customer, Opportunity, Service
from hello_guy_ai.core.runtime import ToolContext, registered_tools, tool_context
from hello_guy_ai.models import Message, Notification
from hello_guy_ai.services import chat
from meta_connector.models import Contact, PlatformAccount
from meta_connector.roles import AGENT, set_role

pytestmark = pytest.mark.django_db


@pytest.fixture
def bot():
    call_command("setup_website_bot")
    return chat.public_user()


def test_website_form_creates_customer_and_opportunity_and_alerts_the_team(client, django_user_model,
                                                                         django_capture_on_commit_callbacks):
    agent = django_user_model.objects.create_user("agent")
    set_role(agent, AGENT)
    with django_capture_on_commit_callbacks(execute=True):
        res = client.post("/api/cms/inquiries/", {"name": "Asha", "email": "a@x.com", "subject": "New site",
                                                  "message": "Need an app"}, content_type="application/json")
    assert res.status_code == 201
    opp = Opportunity.objects.select_related("customer").get(reference=res.json()["inquiry_id"])
    assert (opp.title, opp.details, opp.source, opp.status) == ("New site", "Need an app", "website_form", "new")
    assert (opp.customer.name, opp.customer.email, opp.customer.source) == ("Asha", "a@x.com", "website_form")
    assert Notification.objects.get(user=agent).title == "New opportunity: Asha"
    assert client.post("/api/cms/inquiries/", {"name": "B", "email": "bad", "message": "x"},
                       content_type="application/json").status_code == 400


def test_same_person_on_another_channel_is_the_same_customer():
    first = services.create_opportunity(name="Asha", email="asha@x.com", title="Website")
    account = PlatformAccount.objects.create(platform="whatsapp", display_name="WA", phone_number_id="P1")
    contact = Contact.objects.create(platform_account=account, platform_user_id="9199")
    second = services.create_opportunity(name="Asha K", email="ASHA@x.com", phone="+9199", title="Chatbot",
                                         contact=contact)
    assert second.customer == first.customer and Customer.objects.count() == 1
    customer = Customer.objects.get()
    assert customer.phone == "+9199" and list(customer.contacts.all()) == [contact]  # filled in, linked
    assert [o.title for o in customer.opportunities.order_by("created_at")] == ["Website", "Chatbot"]


def test_ai_opportunity_from_a_whatsapp_chat_links_contact_thread_and_service(bot):
    service = Service.objects.create(name="AI Chatbots")
    account = PlatformAccount.objects.create(platform="whatsapp", display_name="WA", phone_number_id="P1")
    contact = Contact.objects.create(platform_account=account, platform_user_id="9199", name="Ravi")
    thread = chat.external_thread(bot, "website_assistant", f"whatsapp:{account.pk}:9199", "dm")
    run = chat.start_agent_run(bot, "website_assistant", "hi")
    with tool_context(ToolContext(user=bot, agent_key="website_assistant", run_id=run.pk, thread_id=thread.pk)):
        out = json.loads(registered_tools()["create_opportunity"].call(
            {"name": "Ravi", "email": "ravi@x.com", "message": "Need a chatbot", "service": "chatbot"}))
    opp = Opportunity.objects.get(reference=out["reference"])
    assert (opp.source, opp.thread, opp.service) == ("whatsapp", thread, service)
    assert list(opp.customer.contacts.all()) == [contact] and opp.customer.source == "whatsapp"


def test_failed_create_opportunity_can_be_retried_but_a_saved_one_cannot(bot):
    thread = chat.external_thread(bot, "website_assistant", "web:x", "v")
    run = chat.start_agent_run(bot, "website_assistant", "hi")
    tool = registered_tools()["create_opportunity"]
    with tool_context(ToolContext(user=bot, agent_key="website_assistant", run_id=run.pk, thread_id=thread.pk)):
        first = tool.call({"name": "Asha", "email": "nope", "message": "Need a site"})
        Message.objects.create(thread=thread, run=run, role="tool", name="create_opportunity", content=first)
        second = tool.call({"name": "Asha", "email": "asha@x.com", "message": "Need a site"})
        Message.objects.create(thread=thread, run=run, role="tool", name="create_opportunity", content=second)
        third = tool.call({"name": "Asha", "email": "asha@x.com", "message": "Need a site"})
    assert first.startswith("Error:") and "reference" in second and third.startswith("Error: The inquiry was already saved")
    assert Opportunity.objects.count() == 1


def test_status_change_is_logged_on_the_customer_timeline(user):
    opp = services.create_opportunity(name="Asha", email="asha@x.com", title="Website")
    services.set_status(opp, Opportunity.Status.WON, user)
    opp.refresh_from_db()
    assert opp.status == "won" and not opp.is_open
    assert opp.customer.activities.filter(kind=Activity.Kind.STATUS, opportunity=opp).exists()


def test_visitors_cannot_read_opportunities_and_staff_change_status_only_with_approval(bot, superuser):
    services.create_opportunity(name="Asha", email="asha@x.com", title="Website")
    tools = registered_tools()
    with tool_context(ToolContext(user=bot, agent_key="website_assistant")):
        assert tools["list_opportunities"].call({}).startswith("Error: You don't have permission")
    assert tools["update_opportunity_status"].needs_confirmation({"reference": "x", "status": "won"})
    with tool_context(ToolContext(user=superuser, agent_key="sales_assistant")):
        assert json.loads(tools["list_opportunities"].call({}))[0]["customer__name"] == "Asha"
