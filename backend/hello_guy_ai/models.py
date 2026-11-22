import uuid

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.validators import FileExtensionValidator, RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from meta_connector.utils import decrypt_token, encrypt_token

SLUG_VALIDATOR = RegexValidator(r"^[a-zA-Z0-9_-]{1,64}$", "Use 1-64 letters, digits, _ or -.")


class LLMModel(models.Model):
    """A LiteLLM model config (Flow: Flow Provider + Flow Model)."""

    name = models.CharField(max_length=64, unique=True)
    model_id = models.CharField(max_length=200, help_text="LiteLLM id, e.g. ollama_chat/llama3.1:8b, gpt-4o-mini")
    api_base = models.URLField(blank=True)
    # Fernet-encrypted (meta_connector.utils); set via the admin form, never shown back.
    api_key = models.TextField(blank=True)
    params = models.JSONField(default=dict, blank=True, help_text="Extra litellm.completion kwargs")
    enabled = models.BooleanField(default=True)
    is_default = models.BooleanField(
        "Default model", default=False, help_text="Used by every assistant and agent that does not pick a model."
    )

    class Meta:
        verbose_name = "LLM model"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        super().save(*args, **kwargs)
        if self.is_default:  # only one default
            LLMModel.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)

    @classmethod
    def default(cls) -> "LLMModel | None":
        return cls.objects.filter(is_default=True, enabled=True).first()

    def get_api_key(self) -> str:
        return decrypt_token(self.api_key)

    def set_api_key(self, raw: str) -> None:
        self.api_key = encrypt_token(raw)


class KnowledgeBase(models.Model):
    slug = models.CharField(max_length=64, unique=True, validators=[SLUG_VALIDATOR])
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, help_text="Shown to the LLM so it knows when to search")
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


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
    stream_offset = models.CharField(max_length=32, blank=True, help_text="Redis stream id a resume starts after")
    iterations = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
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


class Document(models.Model):
    """File in a knowledge base. Chunks + vectors live in Milvus (disposable, rebuildable)."""

    class Status(models.TextChoices):
        PENDING = "pending"
        PROCESSING = "processing"
        READY = "ready"
        FAILED = "failed"

    knowledge_base = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=255)
    file = models.FileField(
        upload_to="hello_guy_ai/docs/%Y/%m/", validators=[FileExtensionValidator(["txt", "md", "pdf"])]
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    chunk_count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.title


class AgentMemory(models.Model):
    """Durable fact an agent saved; injected into its system prompt (Flow: Flow Agent Memory)."""

    class Scope(models.TextChoices):
        AGENT = "agent", "Shared (all users)"
        USER = "user", "Personal"

    class Origin(models.TextChoices):
        AGENT = "agent"
        FEEDBACK = "feedback"

    agent_key = models.CharField(max_length=64)
    scope = models.CharField(max_length=8, choices=Scope.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    content = models.TextField()
    keywords = models.CharField(max_length=500, blank=True)
    origin = models.CharField(max_length=16, choices=Origin.choices, default=Origin.AGENT)
    source_run = models.ForeignKey(Run, on_delete=models.SET_NULL, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name_plural = "agent memories"
        indexes = [models.Index(fields=["agent_key", "is_active", "scope"], name="hg_memory_lookup")]

    def __str__(self) -> str:
        return self.content[:80]


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
        from hello_guy_ai.triggers import validate_trigger

        validate_trigger(self)
