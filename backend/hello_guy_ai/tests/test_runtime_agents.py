from typing import Literal

import pytest

from hello_guy_ai.assistants import AIAssistant
from hello_guy_ai.exceptions import AssistantMisconfigured, AssistantNotFound, NotAllowed
from hello_guy_ai.models import LLMModel
from hello_guy_ai.services.agents import resolve_agent
from hello_guy_ai.core.runtime import (
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
    spec = resolve_agent("demo", user)
    names = {t.name for t in spec.tools}
    assert {"get_current_datetime", "db_create", "update_memory"} <= names
    assert spec.rag_mode == "auto" and spec.knowledge_base_ids == [kb.pk] and spec.memory_enabled
    with pytest.raises(AssistantNotFound):
        resolve_agent("nope", user)


class PinnedLLMBot(AIAssistant):
    id = "test_pinned_llm"
    name = "Pinned"
    llm = "acme"  # an admin LLM model, picked by name


@pytest.mark.django_db
def test_code_agent_uses_named_admin_llm(user):
    with pytest.raises(AssistantMisconfigured):  # named model missing in admin
        resolve_agent("test_pinned_llm", user)
    llm = LLMModel(name="acme", model_id="openai/gpt-x", api_base="http://llm.local")
    llm.set_api_key("sk-secret")
    llm.save()
    assert "sk-secret" not in llm.api_key  # stored encrypted
    spec = resolve_agent("test_pinned_llm", user)
    assert spec.model == "openai/gpt-x" and spec.llm_kwargs["api_key"] == "sk-secret"
    llm.enabled = False
    llm.save()
    with pytest.raises(NotAllowed):
        resolve_agent("test_pinned_llm", user)


@pytest.mark.django_db
def test_admin_default_llm_drives_code_assistants(user):
    LLMModel.objects.create(name="old", model_id="openai/x", is_default=True)
    llm = LLMModel(name="new", model_id="mistral/m", is_default=True)
    llm.set_api_key("sk-1")
    llm.save()
    assert list(LLMModel.objects.filter(is_default=True).values_list("name", flat=True)) == ["new"]  # one default
    spec = resolve_agent("demo", user)
    assert spec.model == "mistral/m" and spec.llm_kwargs["api_key"] == "sk-1"


def test_llm_model_clean_requires_provider_prefix_and_blocks_reserved_params():
    from django.core.exceptions import ValidationError

    LLMModel(name="a", model_id="openai/gpt-4o-mini", params={"top_p": 0.9}).clean()
    with pytest.raises(ValidationError) as bare:
        LLMModel(name="a", model_id="gpt-nonexistent-model").clean()
    assert "model_id" in bare.value.message_dict
    with pytest.raises(ValidationError) as reserved:
        LLMModel(name="a", model_id="openai/gpt-4o-mini", params={"stream": False, "api_key": "x"}).clean()
    assert "api_key, stream" in reserved.value.message_dict["params"][0]


def test_undecryptable_api_key_is_not_sent_as_empty_string():
    llm = LLMModel(name="a", model_id="openai/x", api_key="not-a-fernet-token")
    assert "api_key" not in llm.connection_kwargs()
