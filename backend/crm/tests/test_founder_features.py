"""Tests for Founder OS features: Deal Values & Forecasting, PMS Handoff, Proposals, Payments,
and Two-Way Email (Gmail/SMTP)."""

import base64
from datetime import timedelta
from decimal import Decimal
import json
from unittest.mock import MagicMock, patch

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from core.models import FollowUp, GlobalNotification
from crm import analytics, email_service, services
from crm.models import Activity, Customer, EmailMessage, GoogleAccount, Opportunity, PaymentMilestone, Proposal
from hello_guy_ai.core.runtime import ToolContext, registered_tools, tool_context
from meta_connector.models import Contact, PlatformAccount
from pms.models import Project

pytestmark = pytest.mark.django_db


def test_deal_value_weighted_forecast_and_lost_reason(admin_client, user):
    cust = Customer.objects.create(name="Acme Labs", email="ceo@acme.io")
    o1 = services.create_opportunity(name="Acme Labs", customer=cust, title="ERP rollout")
    o1.estimated_value = Decimal("100000.00")
    o1.status = Opportunity.Status.PROPOSAL  # 75%
    o1.save()

    o2 = services.create_opportunity(name="Acme Labs", customer=cust, title="Chatbot")
    o2.estimated_value = Decimal("50000.00")
    o2.save()
    services.set_status(o2, Opportunity.Status.WON, user)
    o2.refresh_from_db()
    assert o2.won_value == Decimal("50000.00")

    o3 = services.create_opportunity(name="Beta Co", email="b@beta.io", title="Old deal")
    o3.estimated_value = Decimal("25000.00")
    o3.save()
    services.set_status(o3, Opportunity.Status.LOST, user, lost_reason="Budget constraints")
    o3.refresh_from_db()
    assert o3.lost_reason == "Budget constraints"
    assert o3.weighted_value == Decimal("0.00")

    ov = analytics.overview_analytics(user, days=30)
    assert ov["revenue"]["pipeline_value"] == Decimal("100000.00")
    assert ov["revenue"]["weighted_value"] == Decimal("75000.00")
    assert ov["revenue"]["won_total"] == Decimal("50000.00")


def test_convert_won_opportunity_to_pms_project_is_idempotent(admin_client, user):
    cust = Customer.objects.create(name="Ravi Corp", email="ravi@corp.in")
    opp = services.create_opportunity(name="Ravi Corp", customer=cust, title="AI Portal")
    opp.estimated_value = Decimal("200000.00")
    opp.save()

    res = admin_client.post(reverse("crm:opportunity", args=[opp.pk]), {"action": "convert-project"})
    assert res.status_code == 302
    proj = Project.objects.get(opportunity=opp)
    assert proj.customer == cust
    assert proj.budget == Decimal("200000.00")
    assert proj.tasks.count() == 4
    assert cust.activities.filter(kind=Activity.Kind.SYSTEM, opportunity=opp).exists()

    # Converting again returns the same project without duplicating tasks
    same = services.convert_opportunity_to_project(opp, user=user)
    assert same.pk == proj.pk and Project.objects.count() == 1


def test_proposals_and_payment_milestones_lifecycle(admin_client):
    cust = Customer.objects.create(name="Meera Co", email="meera@co.in")
    opp = services.create_opportunity(name="Meera Co", customer=cust, title="Mobile App")

    # Add proposal marked sent -> moves opportunity to Proposal stage and sets estimated_value
    res = admin_client.post(
        reverse("crm:opportunity", args=[opp.pk]),
        {
            "action": "proposal-add",
            "title": "MVP Scope v1",
            "amount": "150000.00",
            "currency": "INR",
            "mark_sent": "1",
        },
    )
    assert res.status_code == 302
    prop = Proposal.objects.get(opportunity=opp)
    opp.refresh_from_db()
    assert prop.status == Proposal.Status.SENT and opp.status == Opportunity.Status.PROPOSAL
    assert opp.estimated_value == Decimal("150000.00")

    # Accept proposal -> moves opportunity to Won with won_value
    admin_client.post(
        reverse("crm:opportunity", args=[opp.pk]),
        {"action": "proposal-status", "proposal_id": prop.pk, "status": "accepted"},
    )
    opp.refresh_from_db()
    assert opp.status == Opportunity.Status.WON and opp.won_value == Decimal("150000.00")

    # Add overdue payment milestone -> auto-schedules a Payment / Invoice follow-up
    due_yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
    admin_client.post(
        reverse("crm:opportunity", args=[opp.pk]),
        {
            "action": "milestone-add",
            "title": "50% Advance",
            "amount": "75000.00",
            "currency": "INR",
            "due_date": due_yesterday,
            "invoice_number": "INV-101",
            "status": "invoiced",
        },
    )
    ms = PaymentMilestone.objects.get(opportunity=opp)
    assert ms.is_overdue
    assert FollowUp.objects.filter(opportunity=opp, channel__name="Payment / Invoice").exists()

    # Mark milestone paid
    admin_client.post(
        reverse("crm:opportunity", args=[opp.pk]),
        {"action": "milestone-status", "milestone_id": ms.pk, "status": "paid"},
    )
    ms.refresh_from_db()
    assert ms.status == PaymentMilestone.Status.PAID and ms.paid_at is not None


def test_two_way_crm_email_smtp_gmail_sync_and_ai_tool(admin_client, django_user_model):
    staff = django_user_model.objects.create_superuser("boss2", "boss2@example.com", "pass12345")
    cust = Customer.objects.create(name="Kiran", email="kiran@client.com")
    opp = services.create_opportunity(name="Kiran", customer=cust, title="Custom Bot")

    # 1. SMTP fallback send from Opportunity UI
    mail.outbox.clear()
    res = admin_client.post(
        reverse("crm:opportunity", args=[opp.pk]),
        {"action": "send-email", "to_email": "kiran@client.com", "subject": "Proposal", "body": "Please review."},
    )
    assert res.status_code == 302 and len(mail.outbox) == 1
    assert EmailMessage.objects.filter(customer=cust, direction=EmailMessage.Direction.OUTGOING).count() == 1
    assert cust.activities.filter(kind=Activity.Kind.EMAIL).exists()

    # 2. Inbound Gmail sync mock
    account = GoogleAccount.get_solo()
    account.client_id = "cid"
    account.client_secret = "sec"
    account.refresh_token = "rt"
    account.email = "studio@company.com"
    account.scopes = "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly"
    account.save()

    encoded_body = base64.urlsafe_b64encode(b"Sounds great, let us proceed!").decode("ascii")
    gmail_svc = MagicMock()
    gmail_svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "gm_1"}]
    }
    gmail_svc.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "id": "gm_1",
        "threadId": "thr_1",
        "payload": {
            "headers": [
                {"name": "From", "value": "Kiran <kiran@client.com>"},
                {"name": "Subject", "value": "Re: Proposal"},
            ],
            "body": {"data": encoded_body},
        },
    }
    with patch("crm.google._service", return_value=gmail_svc):
        assert email_service.sync_inbound_emails() == 1
        assert email_service.sync_inbound_emails() == 0  # deduplicated
    assert EmailMessage.objects.filter(gmail_message_id="gm_1", direction=EmailMessage.Direction.INCOMING).exists()
    assert GlobalNotification.objects.filter(user=staff, title__icontains="Email reply from Kiran").exists()

    # 3. AI tool requires confirmation & sends cleanly
    tool = registered_tools()["send_customer_email"]
    assert tool.needs_confirmation({"customer_id": cust.pk, "subject": "Hi", "body": "Hello"})
    with patch("crm.google._service", return_value=gmail_svc):
        gmail_svc.users.return_value.messages.return_value.send.return_value.execute.return_value = {
            "id": "gm_out_1",
            "threadId": "thr_1",
        }
        with tool_context(ToolContext(user=staff, agent_key="sales_assistant")):
            out = json.loads(tool.call({"customer_id": cust.pk, "subject": "Follow-up", "body": "Checking in"}))
    assert out["status"] == "sent" and out["to"] == "kiran@client.com"
