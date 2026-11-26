"""Customers and their opportunities: from every channel, one customer per person, AI tools."""

import json

import pytest
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.management import call_command

from crm import services
from crm.models import Activity, Customer, Opportunity, Service
from hello_guy_ai.core.runtime import ToolContext, registered_tools, tool_context
from hello_guy_ai.models import Message, Notification
from hello_guy_ai.services import chat
from meta_connector.models import Contact, PlatformAccount
from meta_connector.roles import AGENT, set_role

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_throttle():
    cache.clear()


@pytest.fixture
def bot():
    call_command("setup_website_bot")
    user = chat.public_user()
    # create_opportunity is a staff tool; the public bot only reaches it in these tests when we grant it here
    user.user_permissions.add(Permission.objects.get(content_type__app_label="crm", codename="add_opportunity"))
    return chat.public_user()


def test_website_form_creates_customer_and_opportunity_and_alerts_the_team(client, django_user_model,
                                                                         django_capture_on_commit_callbacks):
    agent = django_user_model.objects.create_user("agent")
    set_role(agent, AGENT)
    with django_capture_on_commit_callbacks(execute=True):
        res = client.post("/crm/api/leads/", {"name": "Asha", "email": "a@x.com", "subject": "New site",
                                              "message": "Need an app", "inquiry_type": "Project Inquiry Modal"},
                          content_type="application/json")
    assert res.status_code == 201
    opp = Opportunity.objects.select_related("customer").get(reference=res.json()["inquiry_id"])
    assert (opp.title, opp.details, opp.source, opp.status) == ("New site", "Need an app", "website_form", "new")
    assert opp.form_type == "Project Inquiry Modal"
    assert (opp.customer.name, opp.customer.email, opp.customer.source) == ("Asha", "a@x.com", "website_form")
    assert Notification.objects.get(user=agent).title == "New opportunity: Asha"


@pytest.mark.parametrize("payload", [
    {"name": "B", "email": "a@x.com", "message": "Need an app"},       # name too short
    {"name": "Bob", "email": "bad", "message": "Need an app"},          # bad email
    {"name": "Bob", "email": "a@x.com", "message": "hi"},               # message too short
    {"name": "Bob", "email": "a@x.com", "message": "Need an app", "phone": "1" * 51},
])
def test_lead_endpoint_rejects_bad_input(client, payload):
    assert client.post("/crm/api/leads/", payload, content_type="application/json").status_code == 400
    assert not Opportunity.objects.exists()


def test_lead_endpoint_is_throttled_per_client(client, monkeypatch):
    from rest_framework.throttling import ScopedRateThrottle
    monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, "inquiry", "2/hour")
    codes = [client.post("/crm/api/leads/", {"name": "Bob", "email": "a@x.com", "message": "Need an app"},
                         content_type="application/json").status_code for _ in range(3)]
    assert codes == [201, 201, 429]


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


def test_a_shared_phone_never_merges_two_different_emails():
    first = services.get_or_create_customer(name="Asha", email="asha@x.com", phone="+91 98765 43210")
    other = services.get_or_create_customer(name="Ravi", email="ravi@x.com", phone="9876543210")
    assert other != first and Customer.objects.count() == 2


def test_phone_match_uses_all_digits_and_ignores_junk_phones():
    first = services.get_or_create_customer(name="Asha", phone="+91 98765 43210")
    assert services.get_or_create_customer(name="Asha K", phone="9876543210") == first  # same national number
    assert services.get_or_create_customer(name="Else", phone="+44 98765 43210") != first  # other country code
    a = services.get_or_create_customer(name="NoPhone1", phone="N/A")
    b = services.get_or_create_customer(name="NoPhone2", phone="-")
    assert a != b and a.phone == b.phone == ""  # a phone without digits is not stored or matched


def test_blank_email_falls_back_to_the_contact_and_whitespace_is_stripped():
    account = PlatformAccount.objects.create(platform="whatsapp", display_name="WA", phone_number_id="P1")
    contact = Contact.objects.create(platform_account=account, platform_user_id="9199", email=" ravi@x.com ")
    customer = services.get_or_create_customer(name="Ravi", email="   ", contact=contact)
    assert customer.email == "ravi@x.com"
    assert services.get_or_create_customer(name="Ravi", email="RAVI@x.com ") == customer


def test_a_failing_reminder_is_logged_and_does_not_break_the_opportunity(monkeypatch, caplog, django_user_model):
    django_user_model.objects.create_user("staff", is_staff=True)
    import core.reminders

    def boom(**kwargs):
        raise RuntimeError("reminder down")
    monkeypatch.setattr(core.reminders, "schedule_follow_up", boom)
    opp = services.create_opportunity(name="Asha", email="a@x.com", title="Site")
    assert Opportunity.objects.filter(pk=opp.pk).exists() and "reminder down" in caplog.text
