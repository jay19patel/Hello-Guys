"""App settings. Override any key via settings.HELLO_GUY_AI = {...}."""

from typing import Any

from django.conf import settings

DEFAULTS: dict[str, Any] = {
    # LLM (any LiteLLM model string: "ollama_chat/llama3.1:8b", "gpt-4o-mini", ...)
    "DEFAULT_MODEL": "ollama_chat/llama3.1:8b",
    "LLM_API_BASE": "http://localhost:11434",  # None/"" for hosted providers
    # One LLM call can take up to TIMEOUT x (RETRIES + 1) = 270s: keep it below RUN_TIME_LIMIT_SECONDS.
    "LLM_TIMEOUT_SECONDS": 90,
    "LLM_NUM_RETRIES": 2,  # retries after the first attempt; waits 1s, 2s between them
    "LLM_FALLBACK_MODELS": [],  # tried in order when the main model fails, e.g. ["ollama_chat/llama3.1:8b"]
    "DAILY_TOKEN_LIMIT_PER_USER": 0,  # prompt+completion tokens per user per day; 0 = no limit
    # RAG. The admin's default "Embedding" LLM model wins over these two (fallback when none is set).
    "EMBEDDING_MODEL": "ollama/nomic-embed-text",
    "EMBEDDING_API_BASE": "http://localhost:11434",
    "EMBEDDING_BATCH_SIZE": 32,
    "MILVUS_URI": "data/milvus.db",  # file path = Milvus Lite, http://host:19530 = Milvus server
    "MILVUS_TOKEN": "",
    "MILVUS_COLLECTION": "hello_guy_chunks",  # prefix: one collection per embedding model is created
    "CHUNK_SIZE": 1000,
    "CHUNK_OVERLAP": 150,
    "MAX_DOCUMENT_MB": 20,  # upload limit for knowledge base files
    # Built-in ORM tools: "app_label.model" allowlist. Empty = agents cannot touch your data.
    "ORM_TOOLS_MODELS": [],
    "ALLOW_DELETE": False,  # db_delete is refused unless True (it still needs the user's approval)
    # Runtime
    "REDIS_URL": "redis://localhost:6379/0",
    "HISTORY_LIMIT": 20,
    "HISTORY_MAX_CHARS": 60000,  # oldest turns are dropped to fit (~15k tokens); keeps the context window safe
    "RUN_TIME_LIMIT_SECONDS": 300,
    "STREAM_TTL_SECONDS": 900,
    "SSE_MAX_SECONDS": 600,
    "RETENTION_DAYS": 0,  # manage.py hello_guy_prune deletes chats/notifications older than this; 0 = keep forever
    # Notifications: in-app always; email too when True (uses Django's EMAIL_* settings and the user's email).
    "NOTIFY_EMAIL": False,
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
