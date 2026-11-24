from django.apps import AppConfig


class CrmConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "crm"
    verbose_name = "CRM"

    def ready(self) -> None:
        from crm import signals  # noqa: F401  live-chat alerts
