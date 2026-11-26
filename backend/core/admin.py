from django.contrib import admin

from core.models import FollowUpChannel, GlobalFollowUp, GlobalNotification


@admin.register(GlobalNotification)
class GlobalNotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "category", "level", "is_read", "created_at")
    list_filter = ("category", "level", "is_read", "created_at")
    search_fields = ("title", "body", "user__username", "user__email")
    readonly_fields = ("read_at", "created_at")


@admin.register(FollowUpChannel)
class FollowUpChannelAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "icon", "color", "is_active")
    list_editable = ("is_active",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(GlobalFollowUp)
class GlobalFollowUpAdmin(admin.ModelAdmin):
    list_display = ("title", "channel", "priority", "status", "due_at", "assigned_to", "related_label")
    list_filter = ("status", "priority", "channel", "due_at")
    search_fields = ("title", "note", "related_label")
    list_select_related = ("channel", "assigned_to", "created_by")
