import os
import secrets
import string
import uuid

from django.conf import settings
from django.db import models

DEFAULT_ICE_SERVERS = [{"urls": "stun:stun.l.google.com:19302"}]


def generate_room_code() -> str:
    """Generate a clean, readable room code like 'abc-defg-hij'."""
    alpha = string.ascii_lowercase
    p1 = "".join(secrets.choice(alpha) for _ in range(3))
    p2 = "".join(secrets.choice(alpha) for _ in range(4))
    p3 = "".join(secrets.choice(alpha) for _ in range(3))
    return f"{p1}-{p2}-{p3}"


class MeetSettings(models.Model):
    """Singleton configuration for Hello Guy Meet, WebRTC ICE servers, and Pipecat AI."""

    class STTProvider(models.TextChoices):
        DEEPGRAM = "deepgram", "Deepgram Nova"
        BROWSER = "browser", "Browser Web Speech (Free Fallback)"

    class TTSProvider(models.TextChoices):
        CARTESIA = "cartesia", "Cartesia Sonic"
        OPENAI = "openai", "OpenAI TTS"

    default_bot_name = models.CharField(max_length=120, default="Hello Guys AI Assistant")
    ws_base_url = models.CharField(
        max_length=255,
        default="ws://localhost:8001",
        help_text="WebSocket server URL for WebRTC signaling & Pipecat audio (e.g. ws://localhost:8001 or wss://meet-ws.yourdomain.com).",
    )
    ice_servers = models.JSONField(
        default=list,
        blank=True,
        help_text="WebRTC STUN/TURN servers JSON array.",
    )
    llm_model = models.CharField(max_length=100, default="gpt-4o-mini")
    stt_provider = models.CharField(max_length=20, choices=STTProvider.choices, default=STTProvider.DEEPGRAM)
    tts_provider = models.CharField(max_length=20, choices=TTSProvider.choices, default=TTSProvider.CARTESIA)
    cartesia_voice_id = models.CharField(max_length=120, default="79a125e8-cd45-4c13-8a67-188112f4dd22")
    ai_auto_join_default = models.BooleanField(default=False, help_text="Enable AI Voice Assistant in new rooms by default.")
    allow_interruptions = models.BooleanField(default=True, help_text="Allow speakers to interrupt the AI mid-sentence (Silero VAD).")
    default_system_prompt = models.TextField(
        blank=True,
        default="You are a helpful, concise AI meeting assistant. Answer questions clearly, take notes on action items, and keep responses brief for live voice calls.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Meet Settings"
        verbose_name_plural = "Meet Settings"

    def __str__(self) -> str:
        return "Hello Guy Meet Configuration"

    @classmethod
    def get_solo(cls) -> "MeetSettings":
        obj = cls.objects.order_by("pk").first()
        if obj is None:
            obj = cls.objects.create(
                pk=1,
                ws_base_url=os.environ.get("PIPECAT_WS_BASE_URL", "ws://localhost:8001"),
                ice_servers=list(DEFAULT_ICE_SERVERS),
            )
        elif not obj.ice_servers:
            obj.ice_servers = list(DEFAULT_ICE_SERVERS)
            obj.save(update_fields=["ice_servers"])
        return obj


class MeetRoom(models.Model):
    """Independent WebRTC meeting room with optional Pipecat AI Voice Assistant."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        LIVE = "live", "Live"
        ENDED = "ended", "Ended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room_code = models.CharField(max_length=32, unique=True, default=generate_room_code, db_index=True)
    title = models.CharField(max_length=200)
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hosted_meet_rooms",
    )
    scheduled_start = models.DateTimeField(null=True, blank=True, db_index=True)
    scheduled_end = models.DateTimeField(null=True, blank=True)
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    ai_enabled = models.BooleanField(default=False)
    bot_name = models.CharField(max_length=120, default="Hello Guys AI Assistant")
    system_prompt = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED, db_index=True)
    chat_messages = models.JSONField(default=list, blank=True)
    transcript = models.JSONField(default=list, blank=True)
    call_summary = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Meet Room"
        verbose_name_plural = "Meet Rooms"

    def __str__(self) -> str:
        return f"{self.title} ({self.room_code})"


class MeetMinutes(models.Model):
    """Dedicated database table storing Minutes of Meeting (MoM), action items, and transcript."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.OneToOneField(
        MeetRoom,
        on_delete=models.CASCADE,
        related_name="minutes",
    )
    title = models.CharField(max_length=200)
    summary = models.TextField(blank=True, default="")
    action_items = models.JSONField(default=list, blank=True)
    participants = models.JSONField(default=list, blank=True)
    transcript = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_meet_minutes",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Minutes of Meeting"
        verbose_name_plural = "Minutes of Meeting"

    def __str__(self) -> str:
        return f"MoM: {self.title} ({self.room.room_code})"
