import json

import pytest
from django.contrib.auth.models import Permission

from cms.models import BlogPost
from hello_guy_ai.core.runtime import ToolContext, registered_tools, tool_context

pytestmark = pytest.mark.django_db


def call(user, name, **args):
    with tool_context(ToolContext(user=user, agent_key="hello_guy")):
        return registered_tools()[name].call(args)


def test_blog_tools_answer_in_one_call_and_respect_perms(user, django_user_model):
    BlogPost.objects.create(title="Celery bot", slug="celery-bot", content="Redis queues", is_published=True)
    BlogPost.objects.create(title="Draft", slug="draft", content="wip", is_published=False)
    admin = django_user_model.objects.create_superuser("root", "r@example.com", "pass12345")

    assert json.loads(call(admin, "site_content_summary"))["Blog Posts"] == {"published": 1, "drafts": 1}
    assert [p["slug"] for p in json.loads(call(admin, "search_blog_posts", query="redis"))] == ["celery-bot"]
    assert json.loads(call(admin, "get_blog_post", slug="celery-bot"))["content"] == "Redis queues"
    assert call(admin, "get_blog_post", slug="nope").startswith("Error: No blog post")
    assert call(user, "search_blog_posts").startswith("Error: You don't have permission")  # no view perm


def test_list_tech_skills_groups_active_skills(staff):
    from cms.models import TechSkill

    TechSkill.objects.create(name="Frappe", category="backend")
    TechSkill.objects.create(name="Old tool", category="backend", is_active=False)
    staff.user_permissions.add(*Permission.objects.filter(codename="view_techskill"))
    staff = type(staff).objects.get(pk=staff.pk)
    with tool_context(ToolContext(user=staff, agent_key="t")):
        out = json.loads(registered_tools()["list_tech_skills"].call({}))
    assert out == {"backend": ["Frappe"]}
