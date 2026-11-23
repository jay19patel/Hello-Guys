from django.conf import settings
from django.db import models


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
    source_run = models.ForeignKey("hello_guy_ai.Run", on_delete=models.SET_NULL, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name_plural = "agent memories"
        indexes = [models.Index(fields=["agent_key", "is_active", "scope"], name="hg_memory_lookup")]

    def __str__(self) -> str:
        return self.content[:80]
