"""Runtime building blocks for code agents (AIAssistant subclasses).

Mirrors Flow's `flow.lib`: a Tool is a validated callable with a JSON schema, an
AgentSpec is everything one run needs. Both are plain data, built per run.
"""

import inspect
import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from decimal import Decimal
from functools import partial
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from pydantic import BaseModel, create_model
from pydantic import ValidationError as PydanticValidationError

logger = logging.getLogger(__name__)

# Field names never shown to the LLM or to trigger prompts.
SENSITIVE_FIELD_PATTERN = re.compile(r"password|passwd|secret|token|api_?key|private|otp|salt|hash|session", re.IGNORECASE)
TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
MAX_TOOL_RESULT_CHARS = 20000  # a huge tool result would blow the context window


class ToolError(Exception):
    """Raise inside a tool to send a clean message back to the LLM."""


# ---- per-run context -------------------------------------------------------


@dataclass(frozen=True)
class ToolContext:
    """Who/what a tool runs for. Tools read it; the model can never set it."""

    user: Any
    agent_key: str
    run_id: Any = None
    thread_id: Any = None
    timezone: str = ""  # user's IANA timezone (from the browser); "" = settings.TIME_ZONE


_context: ContextVar[ToolContext | None] = ContextVar("hello_guy_tool_context", default=None)


def current_context() -> ToolContext:
    ctx = _context.get()
    if ctx is None:
        raise RuntimeError("Tool called outside an agent run")
    return ctx


@contextmanager
def tool_context(ctx: ToolContext) -> Iterator[None]:
    token = _context.set(ctx)
    try:
        yield
    finally:
        _context.reset(token)


# ---- tools -----------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    func: Callable[..., Any]
    # True, or a callable(args) -> bool when only some calls need approval (e.g. shared memory writes).
    requires_confirmation: bool | Callable[[dict], bool] = False
    confirm_prompt: Callable[[dict], str] | None = None

    def needs_confirmation(self, args: dict) -> bool:
        rule = self.requires_confirmation
        return bool(rule(args) if callable(rule) else rule)

    def schema(self) -> dict:
        params = self.args_model.model_json_schema()
        params.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": params},
        }

    def parse_args(self, raw: str | None) -> dict:
        """Validate model-supplied JSON. Raises ToolError with a message the LLM can act on."""
        try:
            data = json.loads(raw or "{}")
            if not isinstance(data, dict):
                raise ToolError("arguments must be a JSON object")
            parsed = self.args_model.model_validate(data)
        except json.JSONDecodeError as exc:
            raise ToolError(f"invalid JSON arguments: {exc}") from None
        except PydanticValidationError as exc:
            raise ToolError(f"invalid arguments: {exc.errors(include_url=False)}") from None
        return {name: getattr(parsed, name) for name in self.args_model.model_fields}

    def call(self, args: dict) -> str:
        """Run the tool. Every failure becomes text for the LLM; the run never crashes on a tool."""
        try:
            return serialize_result(self.func(**args))
        except (ToolError, PermissionDenied, ValidationError, ValueError) as exc:
            return f"Error: {exc}"
        except Exception as exc:  # tool code is app code: log it, keep the run alive
            logger.exception("Tool %s failed", self.name)
            return f"Error: tool '{self.name}' failed ({type(exc).__name__})"

    def describe_call(self, args: dict) -> str:
        """Plain-English text shown to the human who approves the call."""
        if self.confirm_prompt:
            try:
                return self.confirm_prompt(args)
            except Exception:
                logger.exception("confirm_prompt for %s failed", self.name)
        return f"{self.name}\n{json.dumps(args, indent=2, default=str)}"

    def bind(self, instance: Any) -> "Tool":
        return replace(self, func=partial(self.func, instance))


TOOL_REGISTRY: dict[str, Tool] = {}


def tool(
    func: Callable | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    requires_confirmation: bool | Callable[[dict], bool] = False,
    confirm_prompt: Callable[[dict], str] | None = None,
    register: bool = True,
):
    """Turn a function (or an AIAssistant method) into an LLM tool.

    Type hints become the JSON schema; the docstring is the description. Module-level
    functions are registered by name so assistants can list them in builtin_tools.
    """

    def decorate(fn: Callable) -> Callable:
        spec = build_tool(fn, name, description, requires_confirmation, confirm_prompt)
        fn._hg_tool = spec
        if register and not _is_method(fn):
            if spec.name in TOOL_REGISTRY and TOOL_REGISTRY[spec.name].func is not fn:
                raise ValueError(f"Duplicate tool name: {spec.name!r}")
            TOOL_REGISTRY[spec.name] = spec
        return fn

    return decorate(func) if func else decorate


def build_tool(
    fn: Callable,
    name: str | None = None,
    description: str | None = None,
    requires_confirmation: bool | Callable[[dict], bool] = False,
    confirm_prompt: Callable[[dict], str] | None = None,
) -> Tool:
    tool_name = name or fn.__name__
    if not TOOL_NAME.match(tool_name):
        raise ValueError(f"Tool name {tool_name!r} must match {TOOL_NAME.pattern}")
    desc = description or inspect.getdoc(fn)
    if not desc:
        raise ValueError(f"Tool {tool_name!r} needs a docstring or description")
    params = [p for p in inspect.signature(fn).parameters.values() if p.name not in ("self", "cls")]
    fields = {
        p.name: (
            Any if p.annotation is inspect.Parameter.empty else p.annotation,
            ... if p.default is inspect.Parameter.empty else p.default,
        )
        for p in params
    }
    return Tool(
        name=tool_name,
        description=desc,
        args_model=create_model(f"{tool_name}_args", **fields),
        func=fn,
        requires_confirmation=requires_confirmation,
        confirm_prompt=confirm_prompt,
    )


def registered_tools() -> dict[str, Tool]:
    return dict(TOOL_REGISTRY)


def _json_default(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else str(value)  # numbers stay numbers for the LLM


def serialize_result(result: Any) -> str:
    text = result if isinstance(result, str) else json.dumps(result, default=_json_default, ensure_ascii=False)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        return text[:MAX_TOOL_RESULT_CHARS] + f"\n…[truncated, {len(text)} chars total]"
    return text


def _is_method(fn: Callable) -> bool:
    params = list(inspect.signature(fn).parameters)
    return bool(params) and params[0] == "self"


# ---- agent spec ------------------------------------------------------------


@dataclass
class AgentSpec:
    """Everything one run needs, resolved from a code assistant or an Agent row."""

    key: str
    name: str
    instructions: str
    model: str
    llm_kwargs: dict = field(default_factory=dict)
    tools: list[Tool] = field(default_factory=list)
    knowledge_base_ids: list[int] = field(default_factory=list)
    rag_mode: str = "off"  # off | auto | tool
    rag_top_k: int = 4
    rag_min_score: float = 0.3
    rewrite_query: bool = True
    memory_enabled: bool = False
    max_iterations: int = 8

    def __post_init__(self) -> None:
        names = [t.name for t in self.tools]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"Agent {self.key!r} has duplicate tools: {sorted(dupes)}")

    @property
    def tool_map(self) -> dict[str, Tool]:
        return {t.name: t for t in self.tools}

    def snapshot(self) -> dict:
        """Stored on every Run so you can audit exactly what config produced an answer."""
        return {
            "agent": self.key,
            "model": self.model,
            "instructions": self.instructions,
            "tools": [t.name for t in self.tools],
            "knowledge_base_ids": self.knowledge_base_ids,
            "rag_mode": self.rag_mode,
            "memory_enabled": self.memory_enabled,
            "max_iterations": self.max_iterations,
        }
