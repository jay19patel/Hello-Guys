import json

import pytest
from django.contrib.auth.models import Permission

from demo_crm.models import Lead
from hello_guy_ai.runtime import ToolContext, registered_tools, tool_context

pytestmark = pytest.mark.django_db
TOOLS = registered_tools()


def call(user, name, **args):
    """Invoke like the engine does: validate model JSON, then run inside the user's context."""
    t = TOOLS[name]
    with tool_context(ToolContext(user=user, agent_key="test")):
        out = t.call(t.parse_args(json.dumps(args)))
    return out if out.startswith("Error") else json.loads(out)


def grant(user, *actions):
    codenames = [f"{a}_lead" for a in actions]
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="demo_crm", codename__in=codenames))
    return type(user).objects.get(pk=user.pk)


def test_write_tools_require_confirmation():
    assert {n for n, t in TOOLS.items() if t.name.startswith("db_") and t.requires_confirmation} == {
        "db_create",
        "db_update",
        "db_delete",
    }


def test_allowlist_and_permissions(user, settings):
    assert call(user, "db_list_models") == []  # no view perm
    assert "permission to view" in call(user, "db_read", model="demo_crm.lead")
    user = grant(user, "view")
    assert call(user, "db_list_models") == [{"model": "demo_crm.lead", "name": "leads"}]
    assert "Unknown model" in call(user, "db_read", model="auth.user")  # not allowlisted, even for superusers
    assert "permission to add" in call(user, "db_create", model="demo_crm.lead", records=[{"name": "x"}])
    settings.HELLO_GUY_AI = {**settings.HELLO_GUY_AI, "ORM_TOOLS_MODELS": []}
    assert "Unknown model" in call(user, "db_read", model="demo_crm.lead")


def test_describe_and_read_with_filters(staff):
    staff = grant(staff, "view")
    Lead.objects.create(name="Acme", company="Acme Inc", value=5000, status="new")
    Lead.objects.create(name="Beta", value=10, status="lost")
    info = call(staff, "db_describe", model="demo_crm.lead")
    by_name = {f["name"]: f for f in info["fields"]}
    assert by_name["status"]["choices"] == ["new", "contacted", "won", "lost"] and by_name["name"]["required"]
    assert by_name["owner_id"]["references"] == "auth.user" and info["permissions"]["add"] is False

    out = call(staff, "db_read", model="demo_crm.lead", filters={"value__gte": 100}, fields=["name", "status"])
    assert [r["name"] for r in out["records"]] == ["Acme"] and set(out["records"][0]) == {"pk", "name", "status"}
    assert call(staff, "db_read", model="demo_crm.lead", order_by="-value", limit=1)["truncated"] is True


def test_read_blocks_injection_style_filters(superuser):
    for bad in ({"owner__password__startswith": "pbkdf2"}, {"name__regex": ".*"}, {"secret": 1}):
        assert call(superuser, "db_read", model="demo_crm.lead", filters=bad).startswith("Error")
    assert call(superuser, "db_read", model="demo_crm.lead", order_by="owner__password").startswith("Error")
    assert call(superuser, "db_read", model="demo_crm.lead", fields=["nope"]).startswith("Error")


def test_row_scope_hook_limits_non_staff(user, django_user_model):
    user = grant(user, "view", "change", "delete")
    other = django_user_model.objects.create_user("other")
    mine = Lead.objects.create(name="Mine", owner=user)
    theirs = Lead.objects.create(name="Theirs", owner=other)
    assert [r["name"] for r in call(user, "db_read", model="demo_crm.lead")["records"]] == ["Mine"]
    out = call(user, "db_update", model="demo_crm.lead", ids=[mine.pk, theirs.pk], values={"status": "won"})
    assert out["updated"] == [mine.pk] and out["failures"][0]["id"] == theirs.pk
    assert call(user, "db_delete", model="demo_crm.lead", ids=[theirs.pk])["deleted"] == []
    assert Lead.objects.filter(pk=theirs.pk, status="new").exists()


def test_create_validates_and_reports_per_row(staff):
    staff = grant(staff, "add")
    out = call(
        staff,
        "db_create",
        model="demo_crm.lead",
        records=[
            {"name": "Good", "email": "a@b.co"},
            {"name": "Bad", "email": "not-an-email"},
            {"pk": 99, "name": "x"},
        ],
    )
    assert len(out["created"]) == 1 and [f["row"] for f in out["failures"]] == [1, 2]
    assert "email" in out["failures"][0]["error"] and "not writable" in out["failures"][1]["error"]
    assert call(staff, "db_create", model="demo_crm.lead", records=[{"name": "x"}] * 51).startswith("Error: At most")


def test_update_and_delete(superuser):
    lead = Lead.objects.create(name="Old")
    assert call(superuser, "db_update", model="demo_crm.lead", ids=[lead.pk], values={"name": "New"})["updated"] == [
        lead.pk
    ]
    assert call(superuser, "db_update", model="demo_crm.lead", ids=[lead.pk], values={"status": "bogus"})["failures"]
    assert Lead.objects.get().name == "New"
    out = call(superuser, "db_delete", model="demo_crm.lead", ids=[lead.pk, 999])
    assert out == {"model": "demo_crm.lead", "deleted": [lead.pk], "not_found": [999]}


def test_confirm_prompts_are_human_readable():
    prompt = TOOLS["db_delete"].describe_call({"model": "demo_crm.lead", "ids": list(range(12))})
    assert prompt.startswith("DELETE 12 demo_crm.lead") and "+4 more" in prompt
