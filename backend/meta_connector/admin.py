from django.contrib import admin
from meta_connector.forms import PlatformAccountForm
from django.utils.html import format_html
from meta_connector.models import PlatformAccount, Contact, Message, WebhookLog, MetaConfig, AutoDMRule, AutoDMLog


@admin.register(MetaConfig)
class MetaConfigAdmin(admin.ModelAdmin):
    list_display = ('app_name', 'graph_api_version', 'verify_token', 'updated_at')
    readonly_fields = ('created_at', 'updated_at')

    def has_add_permission(self, request):
        # Singleton: allow only one record
        return not MetaConfig.objects.exists()


@admin.register(PlatformAccount)
class PlatformAccountAdmin(admin.ModelAdmin):
    form = PlatformAccountForm
    list_display = ('id', 'display_name', 'platform_badge', 'phone_number_id', 'instagram_id', 'is_active', 'created_at')
    list_filter = ('platform', 'is_active', 'created_at')
    search_fields = ('display_name', 'phone_number_id', 'instagram_id')
    readonly_fields = ('access_token_encrypted', 'created_at', 'updated_at')

    def platform_badge(self, obj):
        color = "#25D366" if obj.platform == 'whatsapp' else "#E1306C"
        return format_html(
            '<span style="background:{}; color:#fff; padding:2px 8px; border-radius:4px; font-weight:600;">{}</span>',
            color,
            obj.get_platform_display()
        )
    platform_badge.short_description = "Platform"


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('id', 'avatar_preview', 'name', 'phone', 'email', 'username', 'platform_badge', 'last_message_at', 'is_blocked', 'first_seen')
    list_filter = ('platform_account__platform', 'is_blocked', 'first_seen')
    search_fields = ('name', 'phone', 'email', 'username', 'platform_user_id')
    readonly_fields = ('first_seen', 'last_message_at', 'avatar_preview_large')

    def avatar_preview(self, obj):
        if obj.profile_pic_url:
            return format_html(
                '<img src="{}" style="width:32px; height:32px; border-radius:50%; object-fit:cover;" />',
                obj.profile_pic_url
            )
        initials = obj.get_avatar_initials()
        return format_html(
            '<div style="width:32px; height:32px; border-radius:50%; background:#e2e8f0; color:#475569; display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:bold;">{}</div>',
            initials
        )
    avatar_preview.short_description = "Avatar"

    def avatar_preview_large(self, obj):
        if obj.profile_pic_url:
            return format_html(
                '<img src="{}" style="width:120px; height:120px; border-radius:12px; object-fit:cover; box-shadow:0 2px 4px rgba(0,0,0,0.1);" />',
                obj.profile_pic_url
            )
        return "No profile picture"
    avatar_preview_large.short_description = "Profile Picture Preview"

    def platform_badge(self, obj):
        color = "#25D366" if obj.platform_account.platform == 'whatsapp' else "#E1306C"
        return format_html(
            '<span style="background:{}; color:#fff; padding:2px 6px; border-radius:3px;">{}</span>',
            color,
            obj.platform_account.get_platform_display()
        )
    platform_badge.short_description = "Platform"


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'platform_badge',
        'contact_link',
        'direction_badge',
        'message_type',
        'short_text',
        'status_badge',
        'created_at'
    )
    list_filter = (
        'platform_account__platform',
        'direction',
        'message_type',
        'status',
        'is_read',
        'created_at'
    )
    search_fields = (
        'message_text',
        'contact__name',
        'contact__phone',
        'contact__username',
        'message_id'
    )
    readonly_fields = ('raw_payload', 'created_at', 'updated_at', 'message_id')

    def platform_badge(self, obj):
        color = "#25D366" if obj.platform_account.platform == 'whatsapp' else "#E1306C"
        return format_html(
            '<span style="background:{}; color:#fff; padding:2px 6px; border-radius:3px;">{}</span>',
            color,
            obj.platform_account.platform.upper()
        )
    platform_badge.short_description = "Platform"

    def contact_link(self, obj):
        return obj.contact.name or obj.contact.phone or obj.contact.username or obj.contact.platform_user_id
    contact_link.short_description = "Contact"

    def direction_badge(self, obj):
        color = "#0ea5e9" if obj.direction == 'incoming' else "#8b5cf6"
        return format_html(
            '<span style="background:{}; color:#fff; padding:2px 6px; border-radius:3px;">{}</span>',
            color,
            obj.direction.upper()
        )
    direction_badge.short_description = "Direction"

    def status_badge(self, obj):
        colors = {
            'sent': '#3b82f6',
            'delivered': '#10b981',
            'read': '#059669',
            'failed': '#ef4444',
        }
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{}; color:#fff; padding:2px 6px; border-radius:3px;">{}</span>',
            color,
            obj.status.upper()
        )
    status_badge.short_description = "Status"

    def short_text(self, obj):
        if obj.message_text:
            return (obj.message_text[:40] + '...') if len(obj.message_text) > 40 else obj.message_text
        return f"[{obj.message_type.upper()}]"
    short_text.short_description = "Message"


@admin.register(WebhookLog)
class WebhookLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'platform', 'processed_badge', 'created_at', 'short_error')
    list_filter = ('platform', 'processed', 'created_at')
    search_fields = ('error_message',)
    readonly_fields = ('payload', 'headers', 'created_at')

    def processed_badge(self, obj):
        color = "#10b981" if obj.processed else "#ef4444"
        text = "PROCESSED" if obj.processed else "PENDING/ERROR"
        return format_html(
            '<span style="background:{}; color:#fff; padding:2px 6px; border-radius:3px;">{}</span>',
            color,
            text
        )
    processed_badge.short_description = "State"

    def short_error(self, obj):
        if not obj.error_message:
            return "-"
        return obj.error_message[:50] + "..." if len(obj.error_message) > 50 else obj.error_message
    short_error.short_description = "Error"


@admin.register(AutoDMRule)
class AutoDMRuleAdmin(admin.ModelAdmin):
    list_display = ('platform_account', 'media_id', 'is_enabled', 'keywords', 'updated_at')
    list_filter = ('is_enabled', 'platform_account')


@admin.register(AutoDMLog)
class AutoDMLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'contact', 'media_id', 'status', 'comment_text', 'detail')
    list_filter = ('status', 'platform_account', 'created_at')
    search_fields = ('comment_text', 'contact__username', 'media_id', 'comment_id')
    readonly_fields = [f.name for f in AutoDMLog._meta.fields]
