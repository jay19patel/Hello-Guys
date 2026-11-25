import base64
import hashlib
import os
import secrets
import string
import uuid

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

DEFAULT_ICE_SERVERS = [{"urls": "stun:stun.l.google.com:19302"}]


def _fernet() -> Fernet:
    """Derive a deterministic Fernet key from FIELD_ENCRYPTION_KEY or Django SECRET_KEY."""
    raw_key = os.environ.get("FIELD_ENCRYPTION_KEY", "").strip()
    if raw_key:
        try:
            return Fernet(raw_key.encode("utf-8"))
        except Exception:
            pass
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    """Encrypt an API key for database storage."""
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    return _fernet().encrypt(cleaned.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Decrypt a stored API key; returns empty string if invalid or unset."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""


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
        GROQ = "groq", "Groq Whisper (Free Tier)"
        DEEPGRAM = "deepgram", "Deepgram Nova"
        BROWSER = "browser", "Browser Web Speech (Free Fallback)"

    class TTSProvider(models.TextChoices):
        EDGE_TTS = "edge_tts", "Edge TTS (Free, Natural Voice)"
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
    llm_model = models.CharField(
        max_length=100,
        default="groq/llama-3.3-70b-versatile",
        help_text="e.g. mistral/mistral-small-latest, groq/llama-3.3-70b-versatile, gpt-4o-mini",
    )
    stt_provider = models.CharField(max_length=20, choices=STTProvider.choices, default=STTProvider.GROQ)
    tts_provider = models.CharField(max_length=20, choices=TTSProvider.choices, default=TTSProvider.EDGE_TTS)
    edge_voice = models.CharField(max_length=120, default="en-IN-NeerjaNeural", help_text="Edge TTS voice (e.g. en-IN-NeerjaNeural, hi-IN-SwaraNeural, en-US-AriaNeural).")
    cartesia_voice_id = models.CharField(max_length=120, default="79a125e8-cd45-4c13-8a67-188112f4dd22")
    groq_api_key_enc = models.TextField(blank=True, default="")
    mistral_api_key_enc = models.TextField(blank=True, default="")
    openai_api_key_enc = models.TextField(blank=True, default="")
    deepgram_api_key_enc = models.TextField(blank=True, default="")
    cartesia_api_key_enc = models.TextField(blank=True, default="")
    livekit_url = models.CharField(
        max_length=255,
        default="ws://localhost:7880",
        help_text="LiveKit Server WebSocket URL (e.g. ws://localhost:7880 or wss://livekit.yourdomain.com).",
    )
    livekit_api_key = models.CharField(max_length=120, default="devkey")
    livekit_api_secret_enc = models.TextField(blank=True, default="")
    ai_auto_join_default = models.BooleanField(default=True, help_text="Enable AI Voice Assistant in new rooms by default.")
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
                cartesia_voice_id=os.environ.get("CARTESIA_VOICE_ID", "79a125e8-cd45-4c13-8a67-188112f4dd22"),
                ice_servers=list(DEFAULT_ICE_SERVERS),
            )
        elif not obj.ice_servers:
            obj.ice_servers = list(DEFAULT_ICE_SERVERS)
            obj.save(update_fields=["ice_servers"])
        return obj

    def get_api_key(self, env_name: str) -> str:
        """Return decrypted key from MeetSettings if saved, otherwise fall back to environment variable."""
        field_map = {
            "GROQ_API_KEY": self.groq_api_key_enc,
            "MISTRAL_API_KEY": self.mistral_api_key_enc,
            "OPENAI_API_KEY": self.openai_api_key_enc,
            "DEEPGRAM_API_KEY": self.deepgram_api_key_enc,
            "CARTESIA_API_KEY": self.cartesia_api_key_enc,
        }
        val = decrypt_secret(field_map.get(env_name, ""))
        return val or os.environ.get(env_name, "").strip()

    def get_livekit_credentials(self) -> tuple[str, str, str]:
        """Return (livekit_url, api_key, api_secret) with dev defaults."""
        url = (self.livekit_url or "").strip() or os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
        key = (self.livekit_api_key or "").strip() or os.environ.get("LIVEKIT_API_KEY", "devkey")
        secret = decrypt_secret(self.livekit_api_secret_enc) or os.environ.get("LIVEKIT_API_SECRET", "secret")
        return url, key, secret


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
    ai_enabled = models.BooleanField(default=True)
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
