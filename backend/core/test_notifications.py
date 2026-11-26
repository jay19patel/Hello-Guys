"""Tests for the platform-wide GlobalNotification and GlobalFollowUp systems in core."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core import reminders as followups, notifications
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
    from meta_connector.roles import AGENT, set_role

    user = django_user_model.objects.create_user("jay", "jay@example.com", "pass12345")
    set_role(user, AGENT)
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

    assert client.get(reverse("core:reminders")).status_code == 200
    bell = client.get(reverse("core:notifications")).json()
    assert bell["followups_due_count"] == 1
    assert len(bell["followups"]) == 1
    assert bell["followups"][0]["channel"] == "Slack DM"
    assert any(i["category"] == "followup" and "Reminder due" in i["title"] for i in bell["items"])

    # 1-click mark done from the Notification Bell
    done_res = client.post(
        reverse("core:reminder-action", args=[fu.pk]),
        data={"action": "done"},
        content_type="application/json",
    )
    assert done_res.status_code == 200 and done_res.json()["status"] == GlobalFollowUp.Status.DONE


# ---- security / logic fixes ----

def _agent(django_user_model, name="agent"):
    from meta_connector.roles import AGENT, set_role

    user = django_user_model.objects.create_user(name, f"{name}@example.com", "pass12345")
    set_role(user, AGENT)
    return user


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:text/html,x", "//evil.example", "/\t/evil.example", "vbscript:x"])
def test_unsafe_urls_are_dropped(django_user_model, bad):
    user = _agent(django_user_model)
    fu = followups.schedule_follow_up(title="x", due_at=timezone.now(), assigned_to=user, related_url=bad)
    assert fu.related_url == ""
    assert GlobalNotification.objects.get(user=user).url == "/reminders/"
    assert notifications.notify(user, "t", url=bad).url == ""


def test_safe_urls_are_kept(django_user_model):
    user = _agent(django_user_model)
    assert notifications.notify(user, "t", url="/crm/1/").url == "/crm/1/"
    assert notifications.notify(user, "t", url="https://example.com/a").url == "https://example.com/a"


def test_open_redirect_is_blocked(client, django_user_model):
    client.force_login(_agent(django_user_model))
    res = client.post(reverse("core:reminder-create"), {"title": "", "next": "https://evil.example"})
    assert res.url == reverse("core:reminders")
    res = client.post(reverse("core:reminder-create"), {"title": "", "next": "/crm/"})
    assert res.url == "/crm/"


def test_invalid_due_at_is_an_error_not_plus_24h(client, django_user_model):
    client.force_login(_agent(django_user_model))
    client.post(reverse("core:reminder-create"), {"title": "x", "due_at": "garbage"})
    assert not GlobalFollowUp.objects.exists()


def test_reminders_need_a_role_and_are_scoped(client, django_user_model):
    from meta_connector.roles import ADMIN, set_role

    owner, other = _agent(django_user_model, "owner"), _agent(django_user_model, "other")
    mine = followups.schedule_follow_up(title="mine", due_at=timezone.now(), assigned_to=owner, created_by=owner)
    # roleless user: 403
    client.force_login(django_user_model.objects.create_user("nobody", "n@example.com", "pass12345"))
    assert client.get(reverse("core:reminders")).status_code == 403
    # another agent cannot act on, or see, the reminder
    client.force_login(other)
    assert client.post(reverse("core:reminder-action", args=[mine.pk]), {"action": "done"}).status_code == 404
    assert mine.title not in client.get(reverse("core:reminders")).content.decode()
    # admin can
    admin = django_user_model.objects.create_user("boss", "b@example.com", "pass12345")
    set_role(admin, ADMIN)
    client.force_login(admin)
    assert client.post(reverse("core:reminder-action", args=[mine.pk]), {"action": "done"}).status_code == 302


def test_bad_integers_do_not_500(client, django_user_model):
    agent = _agent(django_user_model)
    fu = followups.schedule_follow_up(title="x", due_at=timezone.now(), assigned_to=agent)
    client.force_login(agent)
    assert client.get(reverse("core:notifications"), {"limit": "abc"}).status_code == 200
    assert client.post(reverse("core:reminder-action", args=[fu.pk]), {"action": "snooze", "hours": "x"}).status_code == 302
    res = client.post(reverse("core:notifications-read"), data={"ids": ["a"]}, content_type="application/json")
    assert res.status_code == 400


def test_due_notification_is_claimed_once(django_user_model):
    user = _agent(django_user_model)
    followups.schedule_follow_up(title="due", due_at=timezone.now() - timedelta(minutes=1), assigned_to=user, notify_now=False)
    orphan = GlobalFollowUp.objects.create(title="orphan", due_at=timezone.now() - timedelta(minutes=1))
    assert followups.notify_due_follow_ups() == 1
    assert followups.notify_due_follow_ups() == 0
    orphan.refresh_from_db()
    assert orphan.notified_at is not None


def test_public_reminder_tool_is_restricted(django_user_model, monkeypatch):
    from types import SimpleNamespace

    from core import ai_tools
    from hello_guy_ai import ToolError

    django_user_model.objects.create_user("owner", "o@example.com", "pass12345", is_staff=True)
    visitor = django_user_model.objects.create_user("website-bot", "w@example.com", "pass12345")
    ctx = SimpleNamespace(user=visitor, run_id=None, timezone="Asia/Kolkata")
    monkeypatch.setattr(ai_tools, "current_context", lambda: ctx)
    fn = ai_tools.create_global_reminder.__wrapped__ if hasattr(ai_tools.create_global_reminder, "__wrapped__") else ai_tools.create_global_reminder
    out = fn(title="Call me", channel="Brand New Channel", priority="urgent")
    fu = GlobalFollowUp.objects.get(pk=out["reminder_id"])
    assert fu.priority == "normal" and out["channel"] == "General"
    assert not FollowUpChannel.objects.filter(name="Brand New Channel").exists()
    with pytest.raises(ToolError):
        fn(title="x", due_at="2001-01-01T10:00")
    with pytest.raises(ToolError):
        fn(title="x", due_at="not a date")
    with pytest.raises(ToolError):
        fn(title="x", hours_from_now=0)
