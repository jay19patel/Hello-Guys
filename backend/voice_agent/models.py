import re
import uuid

from django.conf import settings
from django.db import models

MEET_CODE_RE = re.compile(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$", re.IGNORECASE)


def normalize_meeting_url(raw: str) -> str:
    """Convert a Google Meet code like 'abc-defg-hij' into 'https://meet.google.com/abc-defg-hij'."""
    value = (raw or "").strip()
    if not value:
        return ""
    if MEET_CODE_RE.match(value):
        return f"https://meet.google.com/{value.lower()}"
    if value.startswith("meet.google.com/"):
        return f"https://{value}"
    return value


class VoiceBotSession(models.Model):
    """Real-time Pipecat AI voice bot session for Google Meet or Phone/WebSocket calls."""

    class Mode(models.TextChoices):
        GOOGLE_MEET = "google_meet", "Google Meet"
        PHONE_CALL = "phone_call", "Phone / WebSocket Call"

    class Provider(models.TextChoices):
        SELF_HOSTED = "self_hosted", "Self-Hosted (Playwright)"
        MEETINGBAAS = "meetingbaas", "MeetingBaas"
        RECALL = "recall", "Recall.ai"
        EXOTEL = "exotel", "Exotel"
        TWILIO = "twilio", "Twilio"

    class Status(models.TextChoices):
        QUEUED = "queued", "Joining / Queued"
        IN_MEETING = "in_meeting", "Active in Meeting"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(
        "crm.Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voice_sessions",
    )
    opportunity = models.ForeignKey(
        "crm.Opportunity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voice_sessions",
    )
    meeting = models.ForeignKey(
        "crm.Meeting",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voice_sessions",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voice_sessions",
    )

    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.GOOGLE_MEET)
    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.SELF_HOSTED)
    meeting_url = models.CharField(max_length=500, blank=True, default="")
    phone_number = models.CharField(max_length=50, blank=True, default="")
    bot_name = models.CharField(max_length=120, default="Hello Guys AI Assistant")
    system_prompt = models.TextField(blank=True, default="")

    external_bot_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    transcript = models.JSONField(default=list, blank=True)
    call_summary = models.TextField(blank=True, default="")
    drive_transcript_url = models.URLField(blank=True, default="")
    mom_emailed_to = models.JSONField(default=list, blank=True)
    error_message = models.TextField(blank=True, default="")
    connection_logs = models.JSONField(default=list, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "AI Voice Bot Session"
        verbose_name_plural = "AI Voice Bot Sessions"

    def __str__(self) -> str:
        target = self.meeting_url or self.phone_number or str(self.id)[:8]
        return f"{self.bot_name} ({self.get_status_display()}) · {target}"

    def append_log(self, message: str) -> None:
        logs = list(self.connection_logs or [])
        logs.append(message)
        self.connection_logs = logs[-50:]
