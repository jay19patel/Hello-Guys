"""PMS view tests: permissions, 404 scoping, task CRUD, bulk tree builder, and slot ordering."""

import json
from decimal import Decimal

import pytest
from django.urls import reverse

from meta_connector.roles import ADMIN, AGENT, VIEWER, set_role
from pms.models import Project, Task
from pms.views import compute_builder_slots

pytestmark = pytest.mark.django_db


@pytest.fixture
def users(django_user_model):
    admin = django_user_model.objects.create_user("admin", "admin@test.com", "pass12345")
    set_role(admin, ADMIN)
    agent = django_user_model.objects.create_user("agent", "agent@test.com", "pass12345")
    set_role(agent, AGENT)
    outsider = django_user_model.objects.create_user("outsider", "out@test.com", "pass12345")
    set_role(outsider, AGENT)
    viewer = django_user_model.objects.create_user("viewer", "view@test.com", "pass12345")
    set_role(viewer, VIEWER)
    return {"admin": admin, "agent": agent, "outsider": outsider, "viewer": viewer}


def test_pages_render_for_admin_and_scoping_for_members(client, users):
    proj = Project.objects.create(name="Web App", owner=users["agent"])
    proj.members.add(users["viewer"])
    group = Task.objects.create(project=proj, title="Backend", is_group=True)
    leaf = Task.objects.create(project=proj, parent=group, title="Models", assignee=users["agent"])

    # Admin sees everything + Projects nav tab
    client.force_login(users["admin"])
    for url in (
        reverse("pms:projects"),
        reverse("pms:project-create"),
        reverse("pms:project", args=[proj.pk]),
        reverse("pms:task-create", args=[proj.pk]),
        reverse("pms:task", args=[leaf.pk]),
        reverse("pms:my-tasks"),
    ):
        res = client.get(url)
        assert res.status_code == 200
    assert b"Projects" in client.get(reverse("pms:projects")).content

    # Non-member gets 404 on project, task, task-create
    client.force_login(users["outsider"])
    assert client.get(reverse("pms:project", args=[proj.pk])).status_code == 404
    assert client.get(reverse("pms:task", args=[leaf.pk])).status_code == 404
    assert client.get(reverse("pms:task-create", args=[proj.pk])).status_code == 404

    # Viewer member gets 200 on read, 403 on create/bulk
    client.force_login(users["viewer"])
    assert client.get(reverse("pms:project", args=[proj.pk])).status_code == 200
    assert client.post(reverse("pms:project-create"), {"name": "X", "status": "active"}).status_code == 403
    assert client.post(reverse("pms:project", args=[proj.pk]), {"action": "bulk", "tree": "[]"}).status_code == 403


def test_project_task_lifecycle_and_delete_guards(client, users):
    client.force_login(users["agent"])
    res = client.post(reverse("pms:project-create"), {"name": "CRM Build", "status": "active"})
    assert res.status_code == 302
    proj = Project.objects.get(name="CRM Build")
    assert proj.owner == users["agent"]

    # Create group + subtask
    client.post(reverse("pms:task-create", args=[proj.pk]), {"title": "Phase 1", "is_group": "on", "status": "open", "priority": "medium", "expected_hours": "0", "actual_hours": "0"})
    group = Task.objects.get(project=proj, title="Phase 1")
    client.post(reverse("pms:task-create", args=[proj.pk]), {"title": "Sub 1", "parent": group.pk, "status": "open", "priority": "high", "expected_hours": "2", "actual_hours": "0"})
    sub = Task.objects.get(project=proj, title="Sub 1")

    # Non-group parent rejected
    bad = client.post(reverse("pms:task-create", args=[proj.pk]), {"title": "Bad", "parent": sub.pk, "status": "open", "priority": "medium", "expected_hours": "0", "actual_hours": "0"})
    assert bad.status_code == 200 and not Task.objects.filter(title="Bad").exists()

    # Mark complete
    client.post(reverse("pms:task", args=[sub.pk]), {"action": "status", "status": "done"})
    sub.refresh_from_db()
    assert sub.status == Task.Status.DONE

    # Delete parent blocked, leaf allowed
    client.post(reverse("pms:task", args=[group.pk]), {"action": "delete"})
    assert Task.objects.filter(pk=group.pk).exists()
    client.post(reverse("pms:task", args=[sub.pk]), {"action": "delete"})
    assert not Task.objects.filter(pk=sub.pk).exists()


def test_bulk_tree_builder_success_and_all_or_nothing_errors(client, users):
    proj = Project.objects.create(name="Bulk", owner=users["agent"])
    client.force_login(users["agent"])
    url = reverse("pms:project", args=[proj.pk])

    rows = [
        {"level": 0, "title": "Design", "assignee": str(users["agent"].pk), "due_date": "2026-12-01", "hours": "4"},
        {"level": 1, "title": "Wireframes", "assignee": "", "due_date": "", "hours": "2.5"},
        {"level": 2, "title": "Mobile", "assignee": "", "due_date": "", "hours": "1"},
    ]
    res = client.post(url, {"action": "bulk", "parent": "", "tree": json.dumps(rows)})
    assert res.status_code == 302
    design = Task.objects.get(project=proj, title="Design")
    wire = Task.objects.get(project=proj, title="Wireframes")
    mobile = Task.objects.get(project=proj, title="Mobile")
    assert design.is_group and wire.is_group and not mobile.is_group
    assert wire.parent == design and mobile.parent == wire
    assert design.assignee == users["agent"] and wire.expected_hours == Decimal("2.50")

    # Error cases: all-or-nothing + draft kept
    for bad_payload in (
        {"action": "bulk", "parent": "", "tree": "{bad json"},
        {"action": "bulk", "parent": "", "tree": "[]"},
        {"action": "bulk", "parent": "", "tree": json.dumps([{"level": 0, "title": "OK"}, {"level": 0, "title": ""}])},
    ):
        before = Task.objects.filter(project=proj).count()
        err_res = client.post(url, bad_payload)
        assert err_res.status_code == 200
        assert Task.objects.filter(project=proj).count() == before
        assert b"bulk_error" in err_res.content or b'role="alert"' in err_res.content


def test_builder_slot_orders():
    proj = Project.objects.create(name="Slots")
    g = Task.objects.create(project=proj, title="G", is_group=True)
    c1 = Task.objects.create(project=proj, parent=g, title="C1")
    t2 = Task.objects.create(project=proj, title="T2")
    rows = proj.task_rows()
    orders = [2 * i + 2 for i in range(len(rows))]
    assert orders == [2, 4, 6]
    slots = compute_builder_slots(rows)
    assert slots[str(g.pk)] == {"order": 5, "indent": 24}  # right after c1 (index 1 -> 2*1+3=5)
    assert slots[str(c1.pk)] == {"order": 5, "indent": 48}
    assert slots[str(t2.pk)] == {"order": 7, "indent": 24}
    assert slots[""] == {"order": 7, "indent": 0}
