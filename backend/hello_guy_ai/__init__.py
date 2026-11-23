"""hello_guy_ai: lightweight AI assistant framework for Django.

Stack: LiteLLM (any LLM, local or cloud), Dramatiq + Redis (background runs),
Redis Streams (token streaming over SSE), Milvus (RAG vector store).

Everything a host app needs is importable from here:

    from hello_guy_ai import AIAssistant, KnowledgeSource, ToolError, current_context, tool
"""

from importlib import import_module

# name -> module. Loaded on first access: importing models here would break Django's app loading.
_PUBLIC = {
    "AIAssistant": "hello_guy_ai.assistants",
    "KnowledgeSource": "hello_guy_ai.knowledge",
    "ToolError": "hello_guy_ai.core.runtime",
    "current_context": "hello_guy_ai.core.runtime",
    "tool": "hello_guy_ai.core.runtime",
}

__all__ = list(_PUBLIC)


def __getattr__(name: str):
    if name in _PUBLIC:
        return getattr(import_module(_PUBLIC[name]), name)
    raise AttributeError(f"module 'hello_guy_ai' has no attribute {name!r}")
