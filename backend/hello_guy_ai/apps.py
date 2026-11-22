import importlib.util
from importlib import import_module

from django.apps import AppConfig, apps


class HelloGuyAiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "hello_guy_ai"
    verbose_name = "Hello Guy AI"

    def ready(self) -> None:
        import hello_guy_ai.tools  # noqa: F401  registers built-in tools
        from hello_guy_ai import signals, triggers  # noqa: F401

        # Autodiscover per-app modules: ai_tools.py (registered @tool functions) and
        # ai_assistants.py (code agents). Tools first so assistants can reference them.
        for module_name in ("ai_tools", "ai_assistants"):
            for app in apps.get_app_configs():
                module = f"{app.name}.{module_name}"
                if importlib.util.find_spec(module):
                    import_module(module)
        triggers.connect_signals()
