from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'Core Portal & System Engine'

    def ready(self) -> None:
        from django.conf import settings

        if 'hello_guy_ai' in settings.INSTALLED_APPS:
            import core.signals  # noqa: F401
