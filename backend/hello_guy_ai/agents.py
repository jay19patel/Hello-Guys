"""One place that turns an agent id into a runnable AgentSpec (Flow: Agent.assemble).

Agents are code only: AIAssistant subclasses in each app's ai_assistants.py.
"""

import logging
from typing import Any

from hello_guy_ai.assistant import AIAssistant
from hello_guy_ai.conf import ai_settings
from hello_guy_ai.exceptions import NotAllowed
from hello_guy_ai.models import KnowledgeBase, LLMModel
from hello_guy_ai.runtime import AgentSpec

logger = logging.getLogger(__name__)


def resolve_agent(agent_id: str, user: Any) -> AgentSpec:
    """Build the spec for `agent_id`, enforcing who may run it. Raises AssistantNotFound / NotAllowed."""
    from hello_guy_ai.memory import bind_update_memory
    from hello_guy_ai.tools.knowledge import bind_search_knowledge

    code_cls = AIAssistant.get_cls(agent_id)  # raises AssistantNotFound
    if not code_cls.can_run(user):
        raise NotAllowed("You are not allowed to use this assistant")
    assistant = code_cls(user=user)
    spec = assistant.to_spec(knowledge_base_ids(assistant.get_knowledge_base_slugs()))

    if spec.rag_mode == "tool" and spec.knowledge_base_ids:
        spec.tools.append(bind_search_knowledge(spec.knowledge_base_ids))
    if spec.memory_enabled:
        spec.tools.append(bind_update_memory(spec.key))
    spec.__post_init__()  # re-check duplicates after attaching bound tools
    return spec


def list_agents(user: Any) -> list[dict]:
    agents = [
        {"id": cls.id, "name": cls.name, "description": cls.description, "suggestions": cls.suggestions}
        for cls in AIAssistant.registry().values()
        if cls.can_run(user)
    ]
    for info in agents:  # model + tools so the UI can show what each agent can do
        try:
            spec = resolve_agent(info["id"], user)
        except Exception:  # misconfigured agent: still listed, running it reports the error
            logger.exception("Could not resolve agent %s", info["id"])
            continue
        info["model"] = spec.model
        info["tools"] = [
            {
                "name": t.name,
                "description": " ".join(t.description.strip().split("\n\n")[0].split()),  # first paragraph
                "requires_confirmation": t.requires_confirmation,
            }
            for t in spec.tools
        ]
    return agents


def agent_exists(agent_id: str) -> bool:
    return agent_id in AIAssistant.registry()


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
