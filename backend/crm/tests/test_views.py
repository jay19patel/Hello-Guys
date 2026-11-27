"""Every CRM page renders for an Admin; roles and login are enforced."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from crm.models import Customer, Meeting, Opportunity
from hello_guy_ai.services import chat
from meta_connector.roles import ADMIN, VIEWER, set_role

pytestmark = pytest.mark.django_db


@pytest.fixture
def data():
    call_command("setup_website_bot")
    customer = Customer.objects.create(name="Acme", email="ops@acme.test", instagram_username="acme")
    opportunity = Opportunity.objects.create(customer=customer, title="Need a CRM")
    start = timezone.now() + timedelta(days=1)
    meeting = Meeting.objects.create(title="Kickoff", customer=customer, start_at=start, end_at=start + timedelta(hours=1))
    thread = chat.external_thread(chat.public_user(), "website_assistant", "web:abc", "Visitor")
    return {"customer": customer, "opportunity": opportunity, "meeting": meeting, "thread": thread}


@pytest.fixture
def admin_client(client, django_user_model):
    user = django_user_model.objects.create_user("boss", "boss@example.com", "pass12345")
    set_role(user, ADMIN)
    client.force_login(user)
    return client


def test_every_page_renders_for_admin(admin_client, data):
    urls = [
        reverse("crm:overview"),
        reverse("crm:opportunities"), reverse("crm:opportunities") + "?status=all&q=x",
        reverse("crm:opportunity", args=[data["opportunity"].pk]),
        reverse("crm:customers"), reverse("crm:customer", args=[data["customer"].pk]),
        reverse("crm:inbox"), reverse("crm:inbox") + "?show=all",
        reverse("crm:inbox-thread", args=[data["thread"].pk]),
        reverse("crm:settings"),
        reverse("crm:opportunity-create"), reverse("crm:customer-create"),
        reverse("crm:customer-create") + f"?thread={data['thread'].pk}",
    ]
    for url in urls:
        assert admin_client.get(url).status_code == 200, url
    res = admin_client.get(reverse("crm:inbox-thread", args=[data["thread"].pk]) + "?format=json")
    assert res.status_code == 200 and res.json()["human"] is False


def test_viewer_cannot_change_and_anonymous_must_log_in(client, django_user_model, data):
    url = reverse("crm:opportunity", args=[data["opportunity"].pk])
    assert client.get(url).status_code == 302  # login
    viewer = django_user_model.objects.create_user("viewer")
    set_role(viewer, VIEWER)
    client.force_login(viewer)
    assert client.get(url).status_code == 200
    assert client.post(url, {"action": "note", "kind": "note", "body": "x"}).status_code == 403
    assert client.get(reverse("crm:settings")).status_code == 403


def test_agent_messages_a_customer_on_whatsapp(admin_client, data):
    from unittest.mock import patch

    from meta_connector.models import Contact, PlatformAccount

    account = PlatformAccount.objects.create(platform="whatsapp", display_name="WA", phone_number_id="P1")
    contact = Contact.objects.create(platform_account=account, platform_user_id="9199", name="Ravi")
    data["customer"].contacts.add(contact)
    url = reverse("crm:customer", args=[data["customer"].pk])
    with patch("meta_connector.services.message_service.WhatsAppService.send_whatsapp_message",
               return_value=(True, {"messages": [{"id": "wamid.1"}]}, "")) as send:
        assert admin_client.post(url, {"action": "message", "contact": contact.pk, "text": "Quote ready"}).status_code == 302
    assert send.call_args.kwargs["to_number"] == "9199" and send.call_args.kwargs["text"] == "Quote ready"
    assert data["customer"].activities.filter(kind="message", body="Quote ready").exists()


def test_next_redirects_stay_on_this_site(admin_client, data):
    url = reverse("crm:opportunity-stage", args=[data["opportunity"].pk])
    assert admin_client.post(url, {"status": "won", "next": "https://evil.example/x"})["Location"] == reverse("crm:opportunities")
    assert admin_client.post(url, {"status": "won", "next": "//evil.example"})["Location"] == reverse("crm:opportunities")
    assert admin_client.post(url, {"status": "won", "next": "/crm/customers/"})["Location"] == "/crm/customers/"


def test_bad_numbers_in_requests_do_not_crash(admin_client, data):
    assert admin_client.get(reverse("crm:inbox-thread", args=[data["thread"].pk]) + "?format=json&after=abc").status_code == 200
    assert admin_client.post(reverse("crm:follow-up-create"), {"customer_id": "abc"}).status_code == 404


def test_invalid_follow_up_due_at_does_not_create_record(admin_client, data):
    from core.models import FollowUp
    res = admin_client.post(reverse("crm:follow-up-create"), {"customer_id": data["customer"].pk, "due_at": "not-a-date"})
    assert res.status_code == 302
    assert not FollowUp.objects.exists()


def test_editing_an_opportunity_saves_its_services(admin_client, data):
    from crm.models import Service
    a, b = Service.objects.create(name="Web"), Service.objects.create(name="Apps")
    opp = data["opportunity"]
    res = admin_client.post(reverse("crm:opportunity", args=[opp.pk]), {
        "action": "update", "customer": data["customer"].pk, "services": [a.pk, b.pk], "title": opp.title,
        "status": opp.status})
    assert res.status_code == 302
    assert set(opp.services.all()) == {a, b}


def test_customer_detail_shows_where_the_opportunity_came_from(admin_client, data):
    Opportunity.objects.filter(pk=data["opportunity"].pk).update(source="website_form", form_type="Contact Form")
    assert "Website form · Contact Form" in admin_client.get(reverse("crm:customer", args=[data["customer"].pk])).content.decode()
