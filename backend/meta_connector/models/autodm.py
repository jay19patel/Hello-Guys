from django.db import models
from django.utils.translation import gettext_lazy as _
from .account import PlatformAccount
from .contact import Contact


class AutoDMRule(models.Model):
    """
    Comment-to-DM rule. media_id='' is the account-wide default used for posts without their own rule.
    """
    platform_account = models.ForeignKey(PlatformAccount, on_delete=models.CASCADE, related_name='auto_dm_rules')
    media_id = models.CharField(max_length=100, blank=True, default='', help_text="Instagram media ID; blank = default for all posts")
    is_enabled = models.BooleanField(default=True, verbose_name="Auto-DM on")
    message = models.TextField(max_length=1000, help_text="DM sent privately to everyone who comments")
    keywords = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Optional, comma-separated (e.g. price, link). Blank = DM on every comment."
    )
    public_reply = models.CharField(
        max_length=300, blank=True, default='',
        help_text="Optional public reply under the comment, e.g. 'Check your DM 📩'"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Auto-DM Rule')
        verbose_name_plural = _('Auto-DM Rules')
        unique_together = ('platform_account', 'media_id')

    def __str__(self):
        return f"{self.platform_account.display_name} - {self.media_id or 'Default'}"

    def matches(self, text: str) -> bool:
        words = [k.strip().lower() for k in self.keywords.split(',') if k.strip()]
        return not words or any(w in (text or '').lower() for w in words)


class AutoDMLog(models.Model):
    """One row per incoming comment that an auto-DM rule looked at: what was sent, or why not."""
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_SKIPPED = 'skipped'
    STATUS_CHOICES = ((STATUS_SENT, 'Sent'), (STATUS_FAILED, 'Failed'), (STATUS_SKIPPED, 'Skipped'))

    platform_account = models.ForeignKey(PlatformAccount, on_delete=models.CASCADE, related_name='auto_dm_logs')
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='auto_dm_logs')
    media_id = models.CharField(max_length=100, db_index=True)
    comment_id = models.CharField(max_length=100, unique=True)
    comment_text = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, db_index=True)
    detail = models.TextField(blank=True, default='', help_text="Skip reason or Meta error")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _('Auto-DM Log')
        verbose_name_plural = _('Auto-DM Logs')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.comment_id} → {self.status}"
