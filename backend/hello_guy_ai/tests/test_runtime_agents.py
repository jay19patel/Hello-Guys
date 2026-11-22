from typing import Literal

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError

from hello_guy_ai.agents import list_agents, resolve_agent
from hello_guy_ai.assistant import AIAssistant
from hello_guy_ai.exceptions import AssistantMisconfigured, AssistantNotFound, NotAllowed
from hello_guy_ai.models import Agent, KnowledgeBase, LLMModel
from hello_guy_ai.runtime import (
    TOOL_REGISTRY,
    ToolContext,
    ToolError,
    current_context,
    registered_tools,
    serialize_result,
    tool,
    tool_context,
)


@tool(register=False)
def add(a: int, b: int = 1, mode: Literal["fast", "slow"] = "fast") -> int:
    """Add two integers."""
    return a + b


class CalcBot(AIAssistant):
    id = "test_calc"
    name = "Calc"
    builtin_tools = ["db_read"]

    @tool(requires_confirmation=True, confirm_prompt=lambda a: f"Explode {a['target']}?")
    def explode(self, target: str) -> str:
        """Blow something up."""
        raise RuntimeError("kaboom")


def test_tool_schema_and_validation():
    t = add._hg_tool
    params = t.schema()["function"]["parameters"]
    assert params["required"] == ["a"] and params["properties"]["mode"]["enum"] == ["fast", "slow"]
    assert t.parse_args('{"a": "2"}') == {"a": 2, "b": 1, "mode": "fast"}
    for bad in ('{"a": "x"}', "not json", "[1]", '{"a": 1, "mode": "warp"}'):
        with pytest.raises(ToolError):
            t.parse_args(bad)
    assert t.call({"a": 2, "b": 3, "mode": "fast"}) == "5"


def test_tool_registration_rules():
    assert "add" not in TOOL_REGISTRY  # register=False
    assert {"db_read", "db_create"} <= set(registered_tools())  # builtins registered on app ready
    with pytest.raises(ValueError):
        tool(lambda x: x)  # no docstring
    with pytest.raises(ValueError):
        tool(name="bad name!")(add)


def test_tool_errors_never_crash_and_results_truncate():
    spec = CalcBot(user=None).get_tools()
    explode = next(t for t in spec if t.name == "explode")
    assert explode.requires_confirmation and explode.describe_call({"target": "moon"}) == "Explode moon?"
    assert explode.call({"target": "x"}) == "Error: tool 'explode' failed (RuntimeError)"
    assert serialize_result("x" * 30000).endswith("chars total]")


def test_tool_context_is_required_and_scoped():
    with pytest.raises(RuntimeError):
        current_context()
    with tool_context(ToolContext(user="u", agent_key="a")):
        assert current_context().agent_key == "a"
    with pytest.raises(RuntimeError):
        current_context()


def test_code_assistant_misconfig():
    with pytest.raises(AssistantMisconfigured):
        type("Bad", (AIAssistant,), {"id": "has space", "name": "x"})
    bot = type("Ghost", (AIAssistant,), {"id": "ghost_tools", "name": "x", "builtin_tools": ["nope"]})
    with pytest.raises(AssistantMisconfigured):
        bot().get_tools()
    AIAssistant._registry.pop("ghost_tools")


@pytest.mark.django_db
def test_resolve_code_agent(user, kb):
    spec = resolve_agent("hello_guy", user)
    names = {t.name for t in spec.tools}
    assert {"get_current_datetime", "db_create", "update_memory"} <= names
    assert spec.rag_mode == "auto" and spec.knowledge_base_ids == [kb.pk] and spec.memory_enabled
    with pytest.raises(AssistantNotFound):
        resolve_agent("nope", user)


@pytest.mark.django_db
def test_db_agent_resolution_permissions_and_llm(user):
    llm = LLMModel(name="acme", model_id="openai/gpt-x", api_base="http://llm.local")
    llm.set_api_key("sk-secret")
    llm.save()
    assert "sk-secret" not in llm.api_key  # stored encrypted
    on = KnowledgeBase.objects.create(slug="on", name="On", description="Pricing docs")
    off = KnowledgeBase.objects.create(slug="off", name="Off", enabled=False)
    agent = Agent.objects.create(slug="sales", name="Sales", instructions="Sell.", llm=llm, tools=["db_read"])
    agent.knowledge_bases.set([on, off])

    spec = resolve_agent("sales", user)
    assert spec.model == "openai/gpt-x" and spec.llm_kwargs["api_key"] == "sk-secret"
    assert spec.knowledge_base_ids == [on.pk]  # disabled KB never searchable
    assert {t.name for t in spec.tools} == {"db_read", "search_knowledge"}
    assert "Pricing docs" in spec.tool_map["search_knowledge"].description
    assert any(a["id"] == "sales" and a["kind"] == "db" for a in list_agents(user))

    agent.allowed_groups.add(Group.objects.create(name="sales-team"))
    with pytest.raises(NotAllowed):
        resolve_agent("sales", user)
    assert all(a["id"] != "sales" for a in list_agents(user))
    llm.enabled = False
    llm.save()
    user.groups.add(Group.objects.get(name="sales-team"))
    with pytest.raises(NotAllowed):
        resolve_agent("sales", user)


@pytest.mark.django_db
def test_db_agent_validation():
    with pytest.raises(ValidationError) as err:
        Agent(slug="hello_guy", name="x", instructions="x", tools=["nope"], max_iterations=99).full_clean()
    assert {"slug", "tools", "max_iterations"} <= set(err.value.message_dict)


@pytest.mark.django_db
def test_admin_default_llm_drives_code_assistants(user):
    LLMModel.objects.create(name="old", model_id="openai/x", is_default=True)
    llm = LLMModel(name="new", model_id="mistral/m", is_default=True)
    llm.set_api_key("sk-1")
    llm.save()
    assert list(LLMModel.objects.filter(is_default=True).values_list("name", flat=True)) == ["new"]  # one default
    spec = resolve_agent("hello_guy", user)
    assert spec.model == "mistral/m" and spec.llm_kwargs["api_key"] == "sk-1"
