"""Test settings: `pytest` picks these up via [tool.pytest.ini_options] in pyproject.toml."""

import dramatiq
from dramatiq.brokers.stub import StubBroker

from .local import *  # noqa: F401,F403

INSTALLED_APPS = [*INSTALLED_APPS, "hello_guy_ai.tests.demo_crm"]  # noqa: F405

HELLO_GUY_AI = {**HELLO_GUY_AI, "ORM_TOOLS_MODELS": [*HELLO_GUY_AI["ORM_TOOLS_MODELS"], "demo_crm.lead"]}  # noqa: F405

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast user creation

# In-memory queue: actors enqueue here instead of Redis.
_broker = StubBroker()
_broker.emit_after("process_boot")
dramatiq.set_broker(_broker)

# Tests run with DEBUG off, where an encryption key is required. Test-only key, never used elsewhere.
FIELD_ENCRYPTION_KEY = "3Jk2lJ0yW1pVtq8m3Q2h4P0cX6s9n5R7b1d8f2g4h6k="
