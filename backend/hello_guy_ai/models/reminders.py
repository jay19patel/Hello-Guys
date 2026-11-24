from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import models


class Reminder(models.Model):
    """A one-time or recurring reminder. The scheduler fires it into a Notification exactly once per due time."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled"
        PAUSED = "paused"  # kept, never fires until resumed
        DONE = "done"
        CANCELLED = "cancelled"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hello_guy_reminders")
    message = models.CharField(max_length=500)
    due_at = models.DateTimeField(help_text="Next time it fires")
    recurrence = models.CharField(max_length=100, blank=True, help_text="Cron in `timezone`; blank = one-time")
    timezone = models.CharField(max_length=64)
    agent_id = models.CharField(
        max_length=64, blank=True, help_text="Set: run this assistant with the message as prompt and notify its answer"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCHEDULED)
    last_fired_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("due_at",)
        indexes = [
            models.Index(fields=["status", "due_at"], name="hg_reminder_due"),
            models.Index(fields=["user", "status"], name="hg_reminder_user"),
        ]

    def __str__(self) -> str:
        return self.message

    def clean(self) -> None:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        from croniter import croniter

        if self.recurrence and not croniter.is_valid(self.recurrence):
            raise ValidationError({"recurrence": "Not a valid cron expression, e.g. '0 10 * * 1' (Mondays 10:00)."})
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValidationError({"timezone": "Not a valid timezone, e.g. Asia/Kolkata."}) from None


class Notification(models.Model):
    """In-app notification. Other channels (email, Slack, WhatsApp) listen to `notification_created`."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hello_guy_notifications")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    url = models.CharField(max_length=500, blank=True)
    reminder = models.ForeignKey(Reminder, on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications")
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True, help_text="When the user saw it (inbox or chat)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["user", "is_read", "-created_at"], name="hg_notification_inbox")]

    def __str__(self) -> str:
        return self.title
