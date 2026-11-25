from django.contrib import admin

from hello_guy_meet.models import MeetMinutes, MeetRoom, MeetSettings


@admin.register(MeetSettings)
class MeetSettingsAdmin(admin.ModelAdmin):
    list_display = ("default_bot_name", "ws_base_url", "llm_model", "stt_provider", "tts_provider", "updated_at")


@admin.register(MeetRoom)
class MeetRoomAdmin(admin.ModelAdmin):
    list_display = ("title", "room_code", "status", "ai_enabled", "host", "created_at")
    list_filter = ("status", "ai_enabled", "created_at")
    search_fields = ("title", "room_code")
    readonly_fields = ("id", "room_code", "created_at", "updated_at")


@admin.register(MeetMinutes)
class MeetMinutesAdmin(admin.ModelAdmin):
    list_display = ("title", "room", "created_by", "created_at")
    list_filter = ("created_at",)
    search_fields = ("title", "room__room_code", "summary")
    readonly_fields = ("id", "created_at", "updated_at")
