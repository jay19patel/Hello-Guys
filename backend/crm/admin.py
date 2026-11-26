from django.contrib import admin

from crm.models import Activity, Customer, GoogleAccount, GoogleFile, Meeting, MeetingTranscript, Opportunity, Service


class ActivityInline(admin.TabularInline):
    model = Activity
    extra = 0
    fields = ("kind", "body", "created_by", "created_at")
    readonly_fields = ("created_by", "created_at")


class OpportunityInline(admin.TabularInline):
    model = Opportunity
    extra = 0
    fields = ("title", "service", "status", "assigned_to", "created_at")
    readonly_fields = ("created_at",)
    show_change_link = True


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_editable = ("is_active",)
    search_fields = ("name",)


@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    list_display = ("reference", "title", "customer", "service", "source", "status", "assigned_to", "created_at")
    list_filter = ("status", "source", "service", "created_at")
    search_fields = ("reference", "title", "details", "customer__name", "customer__email")
    list_select_related = ("customer", "service", "assigned_to")
    readonly_fields = ("reference", "thread", "created_at", "updated_at")
    autocomplete_fields = ("customer",)
    inlines = [ActivityInline]
    date_hierarchy = "created_at"


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "email", "phone", "instagram_username", "source", "owner", "created_at")
    list_filter = ("source",)
    search_fields = ("name", "company", "email", "phone", "instagram_username")
    list_select_related = ("owner",)
    filter_horizontal = ("contacts",)
    readonly_fields = ("drive_folder_id", "drive_folder_url", "created_at", "updated_at")
    inlines = [OpportunityInline, ActivityInline]


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ("title", "start_at", "customer", "opportunity", "status", "organizer", "meet_url")
    list_filter = ("status", "start_at")
    search_fields = ("title", "description", "customer__name")
    list_select_related = ("customer", "opportunity", "organizer")
    readonly_fields = ("google_event_id", "google_event_url", "meet_url", "sync_error", "last_synced_at")


@admin.register(MeetingTranscript)
class MeetingTranscriptAdmin(admin.ModelAdmin):
    list_display = ("title", "meeting", "source", "language", "created_by", "created_at")
    list_filter = ("source", "language", "created_at")
    search_fields = ("title", "raw_text", "summary", "meeting__title")
    list_select_related = ("meeting", "created_by")
    readonly_fields = ("google_doc_id", "google_doc_url", "created_at", "updated_at")


@admin.register(GoogleFile)
class GoogleFileAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "customer", "created_at")
    list_select_related = ("customer",)
    readonly_fields = ("google_id", "url", "shared_with", "created_by", "created_at")


@admin.register(GoogleAccount)
class GoogleAccountAdmin(admin.ModelAdmin):
    """Read-only here: connect, disconnect and change credentials in CRM > Settings (secrets are never shown)."""
    list_display = ("email", "connected_at", "calendar_id")
    exclude = ("client_secret_encrypted", "refresh_token_encrypted")
    readonly_fields = ("client_id", "email", "scopes", "calendar_id", "root_folder_id", "connected_at", "last_error")

    def has_add_permission(self, request):
        return False
