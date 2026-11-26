from django import forms
from django.contrib import admin, messages
from django.db import transaction

from hello_guy_ai.models import (
    AgentMemory,
    Document,
    KnowledgeBase,
    LLMModel,
    Message,
    Notification,
    Reminder,
    Run,
    Thread,
    Trigger,
)
from hello_guy_ai.services import chat
from hello_guy_ai.services.rag import embedding_config


class SuperuserOnlyAdmin(admin.ModelAdmin):
    """LLM/Trigger config decides what code runs and with which keys: superusers only."""

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class LLMModelForm(forms.ModelForm):
    api_base = forms.URLField(
        required=False,
        assume_scheme="https",
        help_text="Custom API base URL (e.g. http://localhost:11434 for local Ollama).",
    )
    api_key = forms.CharField(
        required=False,
        strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Stored encrypted. Leave blank to keep the current key.",
    )
    clear_api_key = forms.BooleanField(required=False, label="Remove saved API key")

    class Meta:
        model = LLMModel
        fields = (
            "name",
            "kind",
            "model_id",
            "api_base",
            "api_key",
            "clear_api_key",
            "params",
            "min_score",
            "enabled",
            "is_default",
        )

    def save(self, commit=True):
        llm = super().save(commit=False)
        llm.api_key = self.initial.get("api_key", "")
        if self.cleaned_data.get("clear_api_key"):
            llm.api_key = ""
        elif self.cleaned_data.get("api_key"):
            llm.set_api_key(self.cleaned_data["api_key"])
        if commit:
            llm.save()
        return llm


@admin.register(LLMModel)
class LLMModelAdmin(SuperuserOnlyAdmin):
    form = LLMModelForm
    list_display = ("name", "kind", "model_id", "min_score", "has_api_key", "enabled", "is_default")
    list_filter = ("kind",)

    # Vectors of different embedding models cannot be mixed: a change rebuilds the knowledge index.
    def save_model(self, request, obj, form, change):
        before = embedding_config()[0]
        super().save_model(request, obj, form, change)
        self._reindex_if_changed(request, before)

    def delete_model(self, request, obj):
        before = embedding_config()[0]
        super().delete_model(request, obj)
        self._reindex_if_changed(request, before)

    def _reindex_if_changed(self, request, before: str) -> None:
        after = embedding_config()[0]
        if after == before:
            return
        from hello_guy_ai.tasks.actors import reindex_knowledge

        transaction.on_commit(reindex_knowledge.send)
        self.message_user(
            request,
            f"Embedding model changed ({before} -> {after}): every knowledge document is being re-indexed.",
            messages.WARNING,
        )

    @admin.display(boolean=True, description="API key")
    def has_api_key(self, obj):
        return bool(obj.api_key)


class DocumentInline(admin.TabularInline):
    model = Document
    extra = 0
    fields = ("title", "file", "status", "chunk_count", "error")
    readonly_fields = ("status", "chunk_count", "error")


@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "enabled", "created_at")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [DocumentInline]

    def save_formset(self, request, form, formset, change):
        new_files = [
            f
            for f in formset.forms
            if "file" in f.changed_data and not (formset.can_delete and formset._should_delete_form(f))
        ]
        for f in new_files:
            f.instance.uploaded_by = f.instance.uploaded_by or request.user
        super().save_formset(request, form, formset, change)
        for f in new_files:
            transaction.on_commit(lambda pk=f.instance.pk: chat.enqueue_ingest(pk))


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "knowledge_base", "content_type", "status", "chunk_count", "created_at")
    list_filter = ("knowledge_base", "status", "content_type")
    readonly_fields = ("status", "chunk_count", "error", "uploaded_by", "content_type", "object_id", "content_hash")
    exclude = ("content",)  # synced records: edit the source object, not this copy
    actions = ["reingest"]

    def save_model(self, request, obj, form, change):
        obj.uploaded_by = obj.uploaded_by or request.user
        super().save_model(request, obj, form, change)
        if {"file", "knowledge_base"} & set(form.changed_data):  # vectors carry the KB id: re-index on a move
            transaction.on_commit(lambda: chat.enqueue_ingest(obj.pk))

    @admin.action(description="Re-ingest selected documents")
    def reingest(self, request, queryset):
        for doc in queryset:
            chat.enqueue_ingest(doc.pk)


@admin.register(AgentMemory)
class AgentMemoryAdmin(SuperuserOnlyAdmin):
    list_display = ("content", "agent_key", "scope", "user", "origin", "is_active", "updated_at")
    list_filter = ("agent_key", "scope", "origin", "is_active")
    search_fields = ("content", "keywords")


@admin.register(Trigger)
class TriggerAdmin(SuperuserOnlyAdmin):
    list_display = (
        "name",
        "agent_id",
        "event",
        "content_type",
        "cron",
        "run_as",
        "auto_approve",
        "enabled",
        "last_fired_at",
    )
    list_filter = ("event", "enabled")
    readonly_fields = ("last_fired_at",)


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ("role", "content", "name", "created_at")
    readonly_fields = fields
    can_delete = False


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = ("name", "assistant_id", "source", "created_by", "updated_at")
    list_filter = ("assistant_id", "source")
    search_fields = ("name", "created_by__username")
    inlines = [MessageInline]


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    list_display = ("id", "thread", "status", "source", "model", "iterations", "feedback_rating", "created_at")
    list_filter = ("status", "source", "feedback_rating", "model")
    readonly_fields = [f.name for f in Run._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ("message", "user", "due_at", "recurrence", "timezone", "status", "last_fired_at")
    list_filter = ("status",)
    search_fields = ("message", "user__username")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "is_read", "read_at", "created_at")
    list_filter = ("is_read",)
    search_fields = ("title", "body", "user__username")
