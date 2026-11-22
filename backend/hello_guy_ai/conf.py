"""App settings. Override any key via settings.HELLO_GUY_AI = {...}."""

from typing import Any

from django.conf import settings

DEFAULTS: dict[str, Any] = {
    # LLM (any LiteLLM model string: "ollama_chat/llama3.1:8b", "gpt-4o-mini", ...)
    "DEFAULT_MODEL": "ollama_chat/llama3.1:8b",
    "LLM_API_BASE": "http://localhost:11434",  # None/"" for hosted providers
    "LLM_TIMEOUT_SECONDS": 120,
    "LLM_NUM_RETRIES": 4,  # total attempts; waits 1s, 2s, 4s between them
    # RAG
    "EMBEDDING_MODEL": "ollama/nomic-embed-text",
    "EMBEDDING_API_BASE": "http://localhost:11434",
    "EMBEDDING_DIM": 768,
    "EMBEDDING_BATCH_SIZE": 32,
    "MILVUS_URI": "data/milvus.db",  # file path = Milvus Lite, http://host:19530 = Milvus server
    "MILVUS_TOKEN": "",
    "MILVUS_COLLECTION": "hello_guy_chunks_v2",  # v2 schema: kb_id instead of assistant_id
    "CHUNK_SIZE": 1000,
    "CHUNK_OVERLAP": 150,
    # Built-in ORM tools: "app_label.model" allowlist. Empty = agents cannot touch your data.
    "ORM_TOOLS_MODELS": [],
    # Runtime
    "REDIS_URL": "redis://localhost:6379/0",
    "HISTORY_LIMIT": 20,
    "RUN_TIME_LIMIT_SECONDS": 300,
    "STREAM_TTL_SECONDS": 900,
    "SSE_MAX_SECONDS": 600,
    # Public website chat (/api/ai/public/*): visitors chat with PUBLIC_AGENT, running as PUBLIC_USER.
    # Empty = public chat disabled. Create the user with: python manage.py setup_website_bot
    "PUBLIC_AGENT": "",
    "PUBLIC_USER": "",
    "PUBLIC_MESSAGES_PER_MINUTE": 10,  # per visitor IP
}


class _Settings:
    """Read lazily so override_settings() works in tests."""

    def __getattr__(self, name: str) -> Any:
        if name not in DEFAULTS:
            raise AttributeError(f"Unknown hello_guy_ai setting: {name}")
        return getattr(settings, "HELLO_GUY_AI", {}).get(name, DEFAULTS[name])


ai_settings = _Settings()
