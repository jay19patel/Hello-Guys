from django.conf import settings
from django.db import models
from django.utils import timezone

from core.safety import safe_url


class GlobalNotification(models.Model):
    """Platform-wide in-app notification usable by any Django app (CRM, Meta, AI, CMS, etc.)."""

    class Level(models.TextChoices):
        INFO = "info", "Info"
        SUCCESS = "success", "Success"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="global_notifications",
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    url = models.CharField(max_length=500, blank=True)
    category = models.CharField(max_length=40, default="system", db_index=True)
    level = models.CharField(max_length=16, choices=Level.choices, default=Level.INFO)
    metadata = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user", "is_read", "-created_at"], name="core_notif_inbox"),
        ]

    def __str__(self) -> str:
        return f"[{self.category}] {self.title}"


class FollowUpChannel(models.Model):
    """Customizable follow-up channel (e.g. WhatsApp, Call, Email, Payment, Task, or user-created)."""

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    icon = models.CharField(max_length=60, default="fa-solid fa-bolt")
    color = models.CharField(max_length=20, default="indigo")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_followup_channels",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class FollowUp(models.Model):
    """Unified follow-up usable for any CRM customer, opportunity, meeting, payment, or general task."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    title = models.CharField(max_length=200)
    note = models.TextField(blank=True, max_length=2000)
    channel = models.ForeignKey(
        FollowUpChannel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="follow_ups",
    )
    due_at = models.DateTimeField(db_index=True)
    priority = models.CharField(max_length=12, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="follow_ups",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_follow_ups",
    )
    related_label = models.CharField(max_length=150, blank=True)
    related_url = models.CharField(max_length=500, blank=True)
    customer = models.ForeignKey(
        "crm.Customer",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="follow_ups",
    )
    opportunity = models.ForeignKey(
        "crm.Opportunity",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="follow_ups",
    )
    entity_type = models.CharField(max_length=40, blank=True, db_index=True)
    entity_id = models.CharField(max_length=64, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("due_at",)
        indexes = [
            models.Index(fields=["status", "due_at"], name="core_followup_status_due"),
            models.Index(fields=["assigned_to", "status", "due_at"], name="core_followup_assignee"),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({timezone.localtime(self.due_at):%d %b %I:%M %p})"

    @property
    def channel_name(self) -> str:
        return self.channel.name if self.channel else "General"

    def get_channel_display(self) -> str:
        return self.channel_name

    @property
    def safe_related_url(self) -> str:
        return safe_url(self.related_url)

    @property
    def is_overdue(self) -> bool:
        return self.status == self.Status.PENDING and self.due_at < timezone.now()
