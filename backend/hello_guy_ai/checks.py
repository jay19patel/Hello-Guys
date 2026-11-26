"""System checks: misconfiguration fails at startup (`manage.py check`), not at 3 AM."""

from cryptography.fernet import Fernet
from django.apps import apps
from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

from hello_guy_ai.conf import DEFAULTS


@register()
def check_settings(app_configs, **kwargs):
    errors = []
    configured = getattr(settings, "HELLO_GUY_AI", {})
    unknown = sorted(set(configured) - set(DEFAULTS))
    if unknown:
        errors.append(Error(f"Unknown HELLO_GUY_AI keys: {unknown}", hint="Check for typos.", id="hello_guy_ai.E002"))
    for label in configured.get("ORM_TOOLS_MODELS", []):
        try:
            apps.get_model(label)
        except (LookupError, ValueError):
            errors.append(
                Warning(f"ORM_TOOLS_MODELS: no installed model {label!r}; it is ignored.", id="hello_guy_ai.W002")
            )
    if bool(configured.get("PUBLIC_AGENT")) != bool(configured.get("PUBLIC_USER")):
        errors.append(Error("Set both PUBLIC_AGENT and PUBLIC_USER (or neither).", id="hello_guy_ai.E003"))
    return errors


@register(Tags.security, deploy=True)
def check_encryption_key(app_configs, **kwargs):
    """LLM API keys are Fernet-encrypted; without FIELD_ENCRYPTION_KEY a key hard-coded in the source is used."""
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", "").strip()
    if key:
        try:
            Fernet(key.encode())
        except ValueError:
            return [Error("FIELD_ENCRYPTION_KEY is not a valid Fernet key.", id="hello_guy_ai.E001")]
        return []
    return [
        Error(
            "FIELD_ENCRYPTION_KEY is not set, so stored LLM API keys are encrypted with a public development key.",
            hint='Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"',
            id="hello_guy_ai.E001",
        )
    ]


@register(Tags.caches, deploy=True)
def check_shared_cache(app_configs, **kwargs):
    """Rate limits and the trigger index live in the cache; a per-process cache breaks both with several workers."""
    backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    if backend.endswith(("LocMemCache", "DummyCache")):
        return [
            Warning(
                f"CACHES['default'] is {backend.rsplit('.', 1)[-1]}: rate limits and trigger updates are per process.",
                hint="Use a shared cache, e.g. django.core.cache.backends.redis.RedisCache.",
                id="hello_guy_ai.W001",
            )
        ]
    return []
