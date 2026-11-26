"""Tests for the platform-wide GlobalNotification and GlobalFollowUp systems in core."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core import followups, notifications
from core.models import FollowUpChannel, GlobalFollowUp, GlobalNotification

pytestmark = pytest.mark.django_db


def test_notify_helpers_and_endpoints(client, django_user_model):
    staff_a = django_user_model.objects.create_user("staff_a", "a@example.com", "pass12345", is_staff=True)
    staff_b = django_user_model.objects.create_user("staff_b", "b@example.com", "pass12345", is_staff=True)
    regular = django_user_model.objects.create_user("regular", "r@example.com", "pass12345", is_staff=False)

    # Single-user push
    n1 = notifications.notify(
        staff_a,
        "Meeting rescheduled",
        body="Client proposed 4 PM",
        url="/crm/meetings/1/",
        category="meeting",
        level="success",
    )
    assert n1.pk and n1.category == "meeting" and n1.level == "success"

    # Staff broadcast
    created = notifications.notify_staff("System update", body="Sync finished", category="crm")
    assert len(created) == 2
    assert not GlobalNotification.objects.filter(user=regular).exists()

    # Unauthenticated API returns 401 JSON
    assert client.get(reverse("core:notifications")).status_code == 401

    # Authenticated API for staff_a
    client.force_login(staff_a)
    assert client.get(reverse("core:notifications-unread-count")).json() == {"count": 2, "followups_due": 0}
    res = client.get(reverse("core:notifications")).json()
    assert res["unread_count"] == 2 and len(res["items"]) == 2

    # Mark one read
    mark_one = client.post(
        reverse("core:notifications-read"),
        data={"ids": [n1.pk]},
        content_type="application/json",
    ).json()
    assert mark_one == {"count": 1, "unread_count": 1}

    # Mark all remaining read
    mark_all = client.post(
        reverse("core:notifications-read"),
        data={},
        content_type="application/json",
    ).json()
    assert mark_all == {"count": 1, "unread_count": 0}
    assert staff_b.global_notifications.filter(is_read=False).count() == 1


def test_global_followups_custom_channel_and_notification_bell(client, django_user_model):
    user = django_user_model.objects.create_user("jay", "jay@example.com", "pass12345", is_staff=True)
    client.force_login(user)

    # Create a custom channel via endpoint
    ch_res = client.post(
        reverse("core:follow-up-channel-create"),
        data={"name": "Slack DM", "icon": "fa-brands fa-slack", "color": "violet"},
        content_type="application/json",
    )
    assert ch_res.status_code == 201
    assert FollowUpChannel.objects.filter(name="Slack DM").exists()

    # Schedule a due follow-up -> appears in Follow-ups list AND pushes due warning to Notification Bell
    past_due = timezone.now() - timedelta(minutes=10)
    fu = followups.schedule_follow_up(
        title="Confirm invoice #99",
        due_at=past_due,
        channel="Slack DM",
        note="Ping finance team",
        priority="high",
        assigned_to=user,
        related_label="Invoice #99",
        related_url="/crm/",
        notify_now=False,
    )

    assert client.get(reverse("core:follow-ups")).status_code == 200
    bell = client.get(reverse("core:notifications")).json()
    assert bell["followups_due_count"] == 1
    assert len(bell["followups"]) == 1
    assert bell["followups"][0]["channel"] == "Slack DM"
    assert any(i["category"] == "followup" and "Follow-up due" in i["title"] for i in bell["items"])

    # 1-click mark done from the Notification Bell
    done_res = client.post(
        reverse("core:follow-up-action", args=[fu.pk]),
        data={"action": "done"},
        content_type="application/json",
    )
    assert done_res.status_code == 200 and done_res.json()["status"] == GlobalFollowUp.Status.DONE
