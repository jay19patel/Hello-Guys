"""One place that turns an agent id into a runnable AgentSpec (Flow: Agent.assemble).

Code assistants (ai_assistants.py) and admin-configured Agent rows share one id
namespace; a run never needs to know which kind it is talking to.
"""

from typing import Any

from django.core.exceptions import ValidationError

from hello_guy_ai.assistant import AIAssistant
from hello_guy_ai.conf import ai_settings
from hello_guy_ai.exceptions import AssistantNotFound, NotAllowed
from hello_guy_ai.models import Agent, KnowledgeBase, LLMModel
from hello_guy_ai.runtime import AgentSpec, registered_tools

BOUND_TOOL_NAMES = {"search_knowledge", "update_memory"}  # attached from agent config, not picked


def resolve_agent(agent_id: str, user: Any) -> AgentSpec:
    """Build the spec for `agent_id`, enforcing who may run it. Raises AssistantNotFound / NotAllowed."""
    from hello_guy_ai.memory import bind_update_memory
    from hello_guy_ai.tools.knowledge import bind_search_knowledge

    code_cls = AIAssistant.registry().get(agent_id)
    if code_cls is not None:
        if not code_cls.can_run(user):
            raise NotAllowed("You are not allowed to use this assistant")
        assistant = code_cls(user=user)
        spec = assistant.to_spec(knowledge_base_ids(assistant.get_knowledge_base_slugs()))
    else:
        agent = _db_agent(agent_id)
        if not can_run_db_agent(agent, user):
            raise NotAllowed("You are not allowed to use this assistant")
        spec = _db_agent_spec(agent)

    if spec.rag_mode == "tool" and spec.knowledge_base_ids:
        spec.tools.append(bind_search_knowledge(spec.knowledge_base_ids))
    if spec.memory_enabled:
        spec.tools.append(bind_update_memory(spec.key))
    spec.__post_init__()  # re-check duplicates after attaching bound tools
    return spec


def list_agents(user: Any) -> list[dict]:
    agents = [
        {"id": cls.id, "name": cls.name, "description": cls.description, "kind": "code"}
        for cls in AIAssistant.registry().values()
        if cls.can_run(user)
    ]
    for agent in Agent.objects.filter(enabled=True).prefetch_related("allowed_groups"):
        if agent.slug not in AIAssistant.registry() and can_run_db_agent(agent, user):
            agents.append({"id": agent.slug, "name": agent.name, "description": agent.description, "kind": "db"})
    return agents


def agent_exists(agent_id: str) -> bool:
    return agent_id in AIAssistant.registry() or Agent.objects.filter(slug=agent_id, enabled=True).exists()


def can_run_db_agent(agent: Agent, user: Any) -> bool:
    if not (user and user.is_authenticated and user.is_active and agent.enabled):
        return False
    groups = list(agent.allowed_groups.all())
    return not groups or user.is_superuser or user.groups.filter(pk__in=[g.pk for g in groups]).exists()


def knowledge_base_ids(slugs: list[str]) -> list[int]:
    # Fail closed: unknown or disabled KBs silently drop out, never widen to "all".
    return list(KnowledgeBase.objects.filter(slug__in=slugs, enabled=True).values_list("pk", flat=True))


def llm_settings(llm: LLMModel | None, temperature: float | None) -> tuple[str, dict]:
    kwargs: dict[str, Any] = {
        "timeout": ai_settings.LLM_TIMEOUT_SECONDS,
        "num_retries": ai_settings.LLM_NUM_RETRIES,
        # LiteLLM's default retry has no wait, so a 429 is retried instantly and fails again.
        "retry_strategy": "exponential_backoff_retry",
        "drop_params": True,  # skip params a provider rejects (e.g. temperature on some Gemini models)
    }
    llm = llm or LLMModel.default()
    if llm is None:
        model = ai_settings.DEFAULT_MODEL
        if ai_settings.LLM_API_BASE:
            kwargs["api_base"] = ai_settings.LLM_API_BASE
    else:
        if not llm.enabled:
            raise NotAllowed(f"LLM model '{llm.name}' is disabled")
        model = llm.model_id
        kwargs.update(llm.params or {})
        if llm.api_base:
            kwargs["api_base"] = llm.api_base
        if llm.api_key:
            kwargs["api_key"] = llm.get_api_key()
    if temperature is not None:
        kwargs["temperature"] = temperature
    return model, kwargs


def validate_db_agent(agent: Agent) -> None:
    errors: dict[str, str] = {}
    if agent.slug in AIAssistant.registry():
        errors["slug"] = "A code assistant already uses this id."
    unknown = sorted(set(agent.tools or []) - set(registered_tools()))
    if not isinstance(agent.tools, list) or unknown:
        errors["tools"] = f"Unknown tools: {unknown}" if unknown else "Must be a list of tool names."
    if not 1 <= agent.max_iterations <= 40:
        errors["max_iterations"] = "Must be between 1 and 40."
    if errors:
        raise ValidationError(errors)


def _db_agent(agent_id: str) -> Agent:
    try:
        return Agent.objects.select_related("llm").get(slug=agent_id, enabled=True)
    except Agent.DoesNotExist:
        raise AssistantNotFound(f"Assistant '{agent_id}' not found") from None


def _db_agent_spec(agent: Agent) -> AgentSpec:
    model, kwargs = llm_settings(agent.llm, agent.temperature)
    available = registered_tools()
    return AgentSpec(
        key=agent.slug,
        name=agent.name,
        instructions=agent.instructions,
        model=model,
        llm_kwargs=kwargs,
        tools=[available[name] for name in agent.tools if name in available],
        knowledge_base_ids=list(agent.knowledge_bases.filter(enabled=True).values_list("pk", flat=True)),
        rag_mode=agent.rag_mode,
        memory_enabled=agent.memory_enabled,
        max_iterations=agent.max_iterations,
    )
