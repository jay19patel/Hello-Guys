"""Code-defined assistants (the django-ai-assistant style). Compile to an AgentSpec.

class SupportBot(AIAssistant):
    id = "support"
    name = "Support Bot"
    instructions = "You help customers."
    knowledge_bases = ["support-docs"]          # KB slugs; retrieved every turn
    builtin_tools = ["db_read", "db_update"]    # registered tools by name
    memory = True

    @tool(requires_confirmation=True)
    def refund(self, order_id: int) -> str:
        '''Refund an order.'''
"""

import inspect
import re
from typing import Any, ClassVar

from hello_guy_ai.conf import ai_settings
from hello_guy_ai.exceptions import AssistantMisconfigured, AssistantNotFound
from hello_guy_ai.runtime import AgentSpec, Tool, registered_tools, tool  # noqa: F401  (re-export)

ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class AIAssistant:
    id: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str] = ""
    instructions: ClassVar[str] = "You are a helpful assistant."
    model: ClassVar[str | None] = None  # None = admin "Default model", else settings DEFAULT_MODEL
    temperature: ClassVar[float | None] = 0.3
    max_tool_iterations: ClassVar[int] = 5
    builtin_tools: ClassVar[list[str]] = []
    memory: ClassVar[bool] = False
    # RAG: has_rag=True retrieves every turn from `knowledge_bases` (default: a KB with this id)
    has_rag: ClassVar[bool] = False
    knowledge_bases: ClassVar[list[str]] = []
    rag_top_k: ClassVar[int] = 4
    rag_min_score: ClassVar[float] = 0.3
    rewrite_query: ClassVar[bool] = True

    _registry: ClassVar[dict[str, type["AIAssistant"]]] = {}
    _method_tools: ClassVar[list[Tool]] = []

    def __init__(self, *, user: Any = None) -> None:
        self.user = user

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        assistant_id = getattr(cls, "id", None)
        if not assistant_id or not ID_PATTERN.match(assistant_id):
            raise AssistantMisconfigured(f"{cls.__name__}.id must match {ID_PATTERN.pattern}")
        if not getattr(cls, "name", None):
            raise AssistantMisconfigured(f"{cls.__name__}.name is required")
        members = inspect.getmembers(cls, predicate=lambda m: hasattr(m, "_hg_tool"))
        cls._method_tools = [m._hg_tool for _, m in members]
        AIAssistant._registry[assistant_id] = cls

    # ---- registry -----------------------------------------------------------

    @classmethod
    def registry(cls) -> dict[str, type["AIAssistant"]]:
        return dict(AIAssistant._registry)

    @classmethod
    def get_cls(cls, assistant_id: str) -> type["AIAssistant"]:
        try:
            return AIAssistant._registry[assistant_id]
        except KeyError:
            raise AssistantNotFound(f"Assistant '{assistant_id}' not found") from None

    # ---- overridable hooks --------------------------------------------------

    @classmethod
    def can_run(cls, user: Any) -> bool:
        return bool(user and user.is_authenticated and user.is_active)

    def get_instructions(self) -> str:
        return self.instructions

    def get_model(self) -> str:
        return self.model or ai_settings.DEFAULT_MODEL

    def get_llm_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "timeout": ai_settings.LLM_TIMEOUT_SECONDS,
            "num_retries": ai_settings.LLM_NUM_RETRIES,
            "retry_strategy": "exponential_backoff_retry",  # wait between retries (429s)
            "drop_params": True,  # skip params a provider rejects
        }
        if ai_settings.LLM_API_BASE:
            kwargs["api_base"] = ai_settings.LLM_API_BASE
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        return kwargs

    def get_tools(self) -> list[Tool]:
        available = registered_tools()
        missing = [n for n in self.builtin_tools if n not in available]
        if missing:
            raise AssistantMisconfigured(f"{type(self).__name__}: unknown builtin tools {missing}")
        bound = [t.bind(self) for t in self._method_tools]
        return bound + [available[n] for n in self.builtin_tools]

    def get_knowledge_base_slugs(self) -> list[str]:
        return list(self.knowledge_bases) or ([self.id] if self.has_rag else [])

    def to_spec(self, knowledge_base_ids: list[int]) -> AgentSpec:
        from hello_guy_ai.agents import llm_settings  # agents imports this module
        from hello_guy_ai.models import LLMModel

        default = None if self.model else LLMModel.default()
        if default:  # admin "Default model" wins over settings DEFAULT_MODEL
            model, llm_kwargs = llm_settings(default, self.temperature)
        else:
            model, llm_kwargs = self.get_model(), self.get_llm_kwargs()
        return AgentSpec(
            key=self.id,
            name=self.name,
            instructions=self.get_instructions(),
            model=model,
            llm_kwargs=llm_kwargs,
            tools=self.get_tools(),
            knowledge_base_ids=knowledge_base_ids,
            rag_mode="auto" if self.has_rag else "off",
            rag_top_k=self.rag_top_k,
            rag_min_score=self.rag_min_score,
            rewrite_query=self.rewrite_query,
            memory_enabled=self.memory,
            max_iterations=self.max_tool_iterations,
        )
