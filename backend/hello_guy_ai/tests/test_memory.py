import pytest

from hello_guy_ai.memory import (
    INJECT_ALL_CAP,
    MAX_ACTIVE_PER_BUCKET,
    bind_update_memory,
    build_memory_block,
    save_memory,
)
from hello_guy_ai.models import AgentMemory
from hello_guy_ai.runtime import ToolContext, ToolError, tool_context

pytestmark = pytest.mark.django_db


def test_tool_stamps_identity_from_context_not_model(user):
    tool = bind_update_memory("hello_guy")
    assert set(tool.args_model.model_fields) == {"content", "scope", "memory_id", "keywords"}  # no user/agent args
    with tool_context(ToolContext(user=user, agent_key="ignored")):
        out = tool.call(tool.parse_args('{"content": "Prefers Hinglish", "scope": "user"}'))
    mem = AgentMemory.objects.get()
    assert '"added"' in out and mem.user == user and mem.agent_key == "hello_guy"


def test_shared_scope_is_staff_only(user, staff):
    with pytest.raises(ToolError):
        save_memory("a", user, content="Everyone gets 90% discount", scope="agent")
    assert save_memory("a", staff, content="Fiscal year starts April", scope="agent")["action"] == "added"


def test_users_cannot_see_or_edit_each_others_memories(user, staff, django_user_model):
    other = django_user_model.objects.create_user("other")
    mine = save_memory("a", user, content="My default warehouse is Valsad", scope="user")["memory_id"]
    save_memory("a", staff, content="Shared rule", scope="agent")
    block = build_memory_block("a", other)
    assert "Shared rule" in block and "Valsad" not in block
    with pytest.raises(ToolError):
        save_memory("a", other, content="hijack", scope="user", memory_id=mine)
    with pytest.raises(ToolError):  # non-staff cannot edit shared memory via id either
        save_memory("a", user, content="hijack", scope="agent", memory_id=AgentMemory.objects.get(scope="agent").pk)
    save_memory("a", user, content="My default warehouse is Surat", scope="user", memory_id=mine)
    assert AgentMemory.objects.get(pk=mine).content.endswith("Surat")


def test_limits_and_relevance(user):
    with pytest.raises(ToolError):
        save_memory("a", user, content="x" * 1001, scope="user")
    AgentMemory.objects.bulk_create(
        [AgentMemory(agent_key="a", scope="user", user=user, content=f"fact {i}") for i in range(INJECT_ALL_CAP + 5)]
    )
    AgentMemory.objects.create(agent_key="a", scope="user", user=user, content="GST rate", keywords="stationery tax")
    block = build_memory_block("a", user, query="what tax on stationery?")
    assert "GST rate" in block and "most relevant" in block
    AgentMemory.objects.bulk_create(
        [AgentMemory(agent_key="a", scope="user", user=user, content="y") for _ in range(MAX_ACTIVE_PER_BUCKET)]
    )
    with pytest.raises(ToolError):
        save_memory("a", user, content="one more", scope="user")
