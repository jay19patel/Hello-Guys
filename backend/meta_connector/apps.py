from django.apps import AppConfig


class MetaConnectorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'meta_connector'
    verbose_name = 'Meta WhatsApp & Instagram Connector'

    def ready(self):
        from meta_connector.services import ai_reply  # noqa: F401  registers the Dramatiq actor (web + worker)
