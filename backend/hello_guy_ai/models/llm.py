from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from hello_guy_ai.crypto import decrypt, encrypt


class LLMModel(models.Model):
    """A LiteLLM model config: a chat model, or the embedding model used for knowledge-base vectors."""

    class Kind(models.TextChoices):
        CHAT = "chat", "Chat"
        EMBEDDING = "embedding", "Embedding (knowledge base)"

    name = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.CHAT)
    model_id = models.CharField(max_length=200, help_text="LiteLLM id, e.g. ollama_chat/llama3.1:8b, gpt-4o-mini")
    api_base = models.URLField(blank=True)
    # Fernet-encrypted (hello_guy_ai.crypto); set via the admin form, never shown back.
    api_key = models.TextField(blank=True)
    params = models.JSONField(default=dict, blank=True, help_text="Extra litellm.completion kwargs")
    min_score = models.FloatField(
        "Min similarity",
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text="Embedding only: chunks scoring below this are not used. Each model has its own scale "
        "(mistral-embed ~0.70); measure with: manage.py hello_guy_rag_probe. Blank: the assistant's rag_min_score.",
    )
    enabled = models.BooleanField(default=True)
    is_default = models.BooleanField(
        "Default model",
        default=False,
        help_text="Chat: used by every assistant that does not pick a model. "
        "Embedding: builds all knowledge-base vectors (changing it re-indexes every document).",
    )

    class Meta:
        verbose_name = "LLM model"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        super().save(*args, **kwargs)
        if self.is_default:  # one default per kind
            LLMModel.objects.filter(is_default=True, kind=self.kind).exclude(pk=self.pk).update(is_default=False)

    @classmethod
    def default(cls, kind: str = Kind.CHAT) -> "LLMModel | None":
        return cls.objects.filter(is_default=True, enabled=True, kind=kind).first()

    def connection_kwargs(self) -> dict:
        """api_base / api_key / extra params for litellm, shared by chat and embedding calls."""
        kwargs = dict(self.params or {})
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.get_api_key()
        return kwargs

    def get_api_key(self) -> str:
        return decrypt(self.api_key)

    def set_api_key(self, raw: str) -> None:
        self.api_key = encrypt(raw)
