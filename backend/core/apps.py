from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'Core Portal & System Engine'


    def ready(self):
        from core import meta_ai  # noqa: F401  AI auto-reply for Meta DMs (registers its signal receiver + worker actor)
