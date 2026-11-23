from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, RegexValidator
from django.db import models
from django.db.models import Q

SLUG_VALIDATOR = RegexValidator(r"^[a-zA-Z0-9_-]{1,64}$", "Use 1-64 letters, digits, _ or -.")


def validate_document_size(file) -> None:
    from hello_guy_ai.conf import ai_settings

    if file.size > ai_settings.MAX_DOCUMENT_MB * 1024 * 1024:
        raise ValidationError(f"File is larger than {ai_settings.MAX_DOCUMENT_MB} MB.")


class KnowledgeBase(models.Model):
    slug = models.CharField(max_length=64, unique=True, validators=[SLUG_VALIDATOR])
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, help_text="Shown to the LLM so it knows when to search")
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


    def __str__(self) -> str:
        return self.name


class Document(models.Model):
    """A file, or a synced database record (see hello_guy_ai.knowledge), in a knowledge base.

    Chunks + vectors live in Milvus (disposable, rebuildable).
    """

    class Status(models.TextChoices):
        PENDING = "pending"
        PROCESSING = "processing"
        READY = "ready"
        FAILED = "failed"

    knowledge_base = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=255)
    file = models.FileField(
        upload_to="hello_guy_ai/docs/%Y/%m/",
        blank=True,
        validators=[FileExtensionValidator(["txt", "md", "pdf"]), validate_document_size],
    )
    # Synced records: the source object and the text built from it (no file).
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    content = models.TextField(blank=True)
    content_hash = models.CharField(max_length=64, blank=True, help_text="Skips re-embedding unchanged text")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    chunk_count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id"],
                condition=Q(content_type__isnull=False),
                name="hg_document_one_per_object",
            )
        ]

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        if not self.file and not self.content_type_id:
            raise ValidationError({"file": "Upload a file."})
