from django.contrib import admin

from voice_agent.models import VoiceBotSession


@admin.register(VoiceBotSession)
class VoiceBotSessionAdmin(admin.ModelAdmin):
    list_display = ("bot_name", "mode", "provider", "status", "customer", "created_at")
    list_filter = ("status", "mode", "provider", "created_at")
    search_fields = ("bot_name", "meeting_url", "external_bot_id", "customer__name")
    readonly_fields = ("id", "created_at", "updated_at")
