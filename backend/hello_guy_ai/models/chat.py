import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Source(models.TextChoices):
    CHAT = "chat"
    TRIGGER = "trigger"


class Thread(models.Model):
    """One conversation between a user and one agent (Flow: Flow Session)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, blank=True)
    assistant_id = models.CharField(max_length=64, db_index=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.CHAT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hello_guy_threads")
    timezone = models.CharField(max_length=64, blank=True, help_text="User's IANA timezone, sent by the browser")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [models.Index(fields=["created_by", "-updated_at"], name="hg_thread_user_upd")]

    def __str__(self) -> str:
        return self.name or str(self.id)


class Run(models.Model):
    """One agent turn (Flow: Flow Run). Can pause for human approval and resume."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        PAUSED = "paused"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        STOPPED = "stopped"

    ACTIVE = (Status.QUEUED, Status.RUNNING, Status.PAUSED)  # block new turns on the thread
    LIVE = (Status.QUEUED, Status.RUNNING)  # owned by a worker; can go stale

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.CHAT)
    trigger = models.ForeignKey("Trigger", on_delete=models.SET_NULL, null=True, blank=True, related_name="runs")
    auto_approve = models.BooleanField(default=False)
    model = models.CharField(max_length=200, blank=True)
    config_snapshot = models.JSONField(null=True, blank=True, help_text="Agent config used (audit)")
    questions = models.JSONField(null=True, blank=True, help_text="Pending approvals while paused")
    answers = models.JSONField(null=True, blank=True, help_text="User answers for the pending approvals")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Who answered the last approval (audit)",
    )
    stream_offset = models.CharField(max_length=32, blank=True, help_text="Redis stream id a resume starts after")
    iterations = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0, help_text="0 for local/unknown models")
    feedback_rating = models.CharField(max_length=8, blank=True, choices=[("up", "Up"), ("down", "Down")])
    feedback_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_active_at = models.DateTimeField(default=timezone.now, help_text="Queued/claimed/resumed; stale check")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["thread"],
                condition=Q(status__in=["queued", "running", "paused"]),
                name="hg_one_active_run_per_thread_v2",
            )
        ]

    @property
    def is_finished(self) -> bool:
        return self.status not in self.ACTIVE

    @property
    def public_error(self) -> str:
        """What the chat shows for a failed run. The raw `error` (stack details) stays admin-only."""
        if self.error.startswith("RateLimitError"):
            return "The AI provider's rate limit was hit. Wait a few seconds and send again."
        if self.error.startswith(("APIConnectionError", "Timeout", "ServiceUnavailableError")):
            return "The AI provider could not be reached. Please try again in a moment."
        if self.error.startswith(("AuthenticationError", "PermissionDeniedError")):
            return "The AI provider rejected the API key. Check it in admin under LLM models."
        return "The assistant failed to respond. Please retry."


class Message(models.Model):
    """Chat message stored in OpenAI format (what LiteLLM speaks)."""

    class Role(models.TextChoices):
        USER = "user"
        ASSISTANT = "assistant"
        TOOL = "tool"

    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="messages")
    run = models.ForeignKey(Run, on_delete=models.SET_NULL, null=True, blank=True, related_name="messages")
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField(blank=True)
    tool_calls = models.JSONField(null=True, blank=True)
    tool_call_id = models.CharField(max_length=128, blank=True)
    name = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)

    def to_llm(self) -> dict:
        msg: dict = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.role == self.Role.TOOL:
            msg["tool_call_id"] = self.tool_call_id
            msg["name"] = self.name
        return msg
