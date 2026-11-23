import pytest

from hello_guy_ai.exceptions import NotAllowed
from hello_guy_ai.services.agents import list_agents, resolve_agent

pytestmark = pytest.mark.django_db


def test_cms_agents_are_staff_only(user, staff):
    assert "content_writer" not in {a["id"] for a in list_agents(user)}
    assert "content_writer" in {a["id"] for a in list_agents(staff)}
    with pytest.raises(NotAllowed):
        resolve_agent("content_writer", user)
