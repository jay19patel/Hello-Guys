from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from .account import PlatformAccount


class Contact(models.Model):
    """
    Unified Contact entity across WhatsApp, Instagram, and Facebook channels.
    """
    platform_account = models.ForeignKey(
        PlatformAccount,
        on_delete=models.CASCADE,
        related_name='contacts'
    )
    platform_user_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="User phone with country code for WhatsApp, or IGSID for Instagram"
    )
    name = models.CharField(max_length=255, blank=True, default='')
    username = models.CharField(max_length=255, blank=True, default='', help_text="Instagram handle")
    phone = models.CharField(max_length=50, blank=True, default='', help_text="Phone number for WhatsApp")
    email = models.EmailField(max_length=255, blank=True, default='', help_text="Captured or shared email address")
    profile_pic_url = models.TextField(blank=True, default='', help_text="Direct CDN link to profile avatar")
    raw_profile_data = models.JSONField(default=dict, blank=True, help_text="Full profile payload fetched from Meta API")
    first_seen = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_blocked = models.BooleanField(default=False, db_index=True)
    ai_thread = models.ForeignKey(
        'hello_guy_ai.Thread', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        help_text="This contact's AI conversation (keeps the chat history between messages)"
    )

    class Meta:
        verbose_name = _('Contact')
        verbose_name_plural = _('Contacts')
        unique_together = ('platform_account', 'platform_user_id')
        ordering = ['-last_message_at', '-first_seen']

    def __str__(self):
        label = self.name or self.username or self.phone or self.platform_user_id
        return f"{label} ({self.platform_account.get_platform_display()})"

    @property
    def platform(self) -> str:
        return self.platform_account.platform

    def update_last_message_time(self):
        self.last_message_at = timezone.now()
        self.save(update_fields=['last_message_at'])

    def get_avatar_initials(self) -> str:
        """Returns 1-2 uppercase letters for avatar fallback."""
        source = self.name or self.username or self.phone or self.platform_user_id or "CU"
        parts = source.strip().split()
        if len(parts) >= 2:
            return f"{parts[0][0]}{parts[1][0]}".upper()
        return source[:2].upper()

