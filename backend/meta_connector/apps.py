from django.apps import AppConfig, apps


class MetaConnectorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'meta_connector'
    verbose_name = 'Meta WhatsApp & Instagram Connector'

    def ready(self):
        if apps.is_installed('hello_guy_ai'):  # AI DM replies + durable webhook queue (its Dramatiq/Redis)
            from meta_connector import ai_reply, tasks  # noqa: F401  signal receiver, actors
