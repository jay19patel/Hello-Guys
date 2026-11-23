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
from hello_guy_ai.core.runtime import AgentSpec, Tool, registered_tools, tool  # noqa: F401  (re-export)
from hello_guy_ai.exceptions import AssistantMisconfigured, AssistantNotFound

ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class AIAssistant:
    id: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str] = ""
    suggestions: ClassVar[list[str]] = []  # starter prompts shown on the chat welcome screen
    instructions: ClassVar[str] = "You are a helpful assistant."
    # Which LLM to use, in priority order:
    #   llm   = name of a model in admin "LLM models" (API key stays in the UI), e.g. "Mistral"
    #   model = raw LiteLLM id, e.g. "ollama_chat/llama3.1:8b" (no stored key)
    #   neither: the admin model marked "Default model", else settings DEFAULT_MODEL
    llm: ClassVar[str | None] = None
    model: ClassVar[str | None] = None
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
        if not (user and user.is_authenticated and user.is_active):
            return False
        # The public website user may only ever run the public agent.
        return user.get_username() != ai_settings.PUBLIC_USER or cls.id == ai_settings.PUBLIC_AGENT

    def get_instructions(self) -> str:
        return self.instructions

    def get_model(self) -> str:
        return self.model or ai_settings.DEFAULT_MODEL

    def get_llm_kwargs(self) -> dict[str, Any]:
        from hello_guy_ai.services.agents import base_llm_kwargs  # agents imports this module

        kwargs = base_llm_kwargs()
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
        from hello_guy_ai.services.agents import llm_settings  # agents imports this module
        from hello_guy_ai.models import LLMModel

        if self.llm:
            configured = LLMModel.objects.filter(name=self.llm, kind=LLMModel.Kind.CHAT).first()
            if configured is None:
                raise AssistantMisconfigured(f"{type(self).__name__}.llm: no chat LLM model named {self.llm!r} in admin")
        else:
            configured = None if self.model else LLMModel.default()
        if configured:  # llm_settings raises NotAllowed if it is disabled
            model, llm_kwargs = llm_settings(configured, self.temperature)
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
