from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models


class Trigger(models.Model):
    """Run an agent on a model event or a cron schedule (Flow: Flow Trigger)."""

    class Event(models.TextChoices):
        CREATED = "created"
        UPDATED = "updated"
        DELETED = "deleted"
        SCHEDULED = "scheduled"

    name = models.CharField(max_length=255)
    agent_id = models.CharField(max_length=64)
    event = models.CharField(max_length=16, choices=Event.choices)
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, null=True, blank=True, help_text="Model for created/updated/deleted"
    )
    condition = models.TextField(
        blank=True, help_text='Safe expression, e.g. obj.status == "new" and obj.amount > 1000'
    )
    prompt_template = models.TextField(help_text="Django template. Context: obj (field dict), event, now")
    cron = models.CharField(max_length=100, blank=True, help_text='For scheduled, e.g. "0 9 * * 1-5"')
    run_as = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    auto_approve = models.BooleanField(default=False, help_text="Nobody is there to approve tool calls")
    enabled = models.BooleanField(default=True)
    last_fired_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        from hello_guy_ai.services.triggers import validate_trigger

        validate_trigger(self)
