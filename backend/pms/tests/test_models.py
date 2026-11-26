"""PMS model tests: tree constraints, cycle/group guards, visible_to, roll-ups, and delete behaviour."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db.models import RestrictedError

from meta_connector.roles import ADMIN, AGENT, VIEWER, set_role
from pms.models import Project, Task

pytestmark = pytest.mark.django_db


def test_visible_to_admin_member_and_non_member(django_user_model):
    admin = django_user_model.objects.create_user("admin")
    set_role(admin, ADMIN)
    owner = django_user_model.objects.create_user("owner")
    set_role(owner, AGENT)
    member = django_user_model.objects.create_user("member")
    set_role(member, VIEWER)
    outsider = django_user_model.objects.create_user("outsider")
    set_role(outsider, AGENT)

    proj = Project.objects.create(name="Alpha", owner=owner)
    proj.members.add(member)

    assert list(Project.objects.visible_to(admin)) == [proj]
    assert list(Project.objects.visible_to(owner)) == [proj]
    assert list(Project.objects.visible_to(member)) == [proj]
    assert list(Project.objects.visible_to(outsider)) == []


def test_task_hierarchy_validation_and_helpers(django_user_model):
    u1 = django_user_model.objects.create_user("u1")
    p1 = Project.objects.create(name="P1", owner=u1)
    p2 = Project.objects.create(name="P2", owner=u1)

    group = Task.objects.create(project=p1, title="Phase 1", is_group=True)
    leaf = Task.objects.create(project=p1, title="Leaf", is_group=False)
    child_group = Task.objects.create(project=p1, parent=group, title="Subgroup", is_group=True)
    grandchild = Task.objects.create(project=p1, parent=child_group, title="Grandchild")

    assert group.code.startswith("TASK-")
    assert grandchild.code.startswith("SUB-TASK-")
    assert grandchild.ancestor_ids() == [group.pk, child_group.pk]
    assert group.descendant_ids() == {child_group.pk, grandchild.pk}

    # Parent must be a group
    bad_leaf_parent = Task(project=p1, parent=leaf, title="Under leaf")
    with pytest.raises(ValidationError) as exc:
        bad_leaf_parent.full_clean()
    assert "parent" in exc.value.message_dict

    # Parent must be in same project
    other_proj_group = Task.objects.create(project=p2, title="Other", is_group=True)
    bad_proj = Task(project=p1, parent=other_proj_group, title="Cross project")
    with pytest.raises(ValidationError) as exc:
        bad_proj.full_clean()
    assert "parent" in exc.value.message_dict

    # Cycle blocked
    group.parent = grandchild
    with pytest.raises(ValidationError) as exc:
        group.full_clean()
    assert "parent" in exc.value.message_dict
    group.parent = None

    # Group with children cannot stop being a group
    group.is_group = False
    with pytest.raises(ValidationError) as exc:
        group.full_clean()
    assert "is_group" in exc.value.message_dict
    group.is_group = True

    # Delete blocked for parent, but project delete cascades
    with pytest.raises(RestrictedError):
        group.delete()
    p1.delete()
    assert not Task.objects.filter(project_id=p1.pk).exists()


def test_project_task_rows_rollups(django_user_model):
    u1 = django_user_model.objects.create_user("u1")
    u2 = django_user_model.objects.create_user("u2")
    proj = Project.objects.create(name="Rollups", owner=u1)

    g = Task.objects.create(project=proj, title="Group", is_group=True, assignee=u1, expected_hours=Decimal("1.00"))
    t1 = Task.objects.create(project=proj, parent=g, title="T1", status=Task.Status.DONE, assignee=u2, expected_hours=Decimal("2.00"), actual_hours=Decimal("1.50"))
    t2 = Task.objects.create(project=proj, parent=g, title="T2", status=Task.Status.OPEN, expected_hours=Decimal("3.00"))
    Task.objects.create(project=proj, parent=g, title="T3", status=Task.Status.CANCELLED, expected_hours=Decimal("5.00"))

    rows = proj.task_rows()
    assert len(rows) == 4
    root = rows[0]
    assert root["task"] == g
    assert root["depth"] == 0 and root["indent"] == 0
    assert root["child_count"] == 3
    assert root["progress"] == 50  # 1 done out of 2 non-cancelled leaves
    assert root["expected_hours"] == Decimal("11.00")
    assert root["actual_hours"] == Decimal("1.50")
    assert root["people"] == [u1, u2]
    assert rows[1]["ancestors"] == [g.pk]
    assert rows[1]["task"] == t1 and rows[2]["task"] == t2
