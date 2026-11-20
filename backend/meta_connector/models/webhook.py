from django.db import models
from django.utils.translation import gettext_lazy as _


class WebhookLog(models.Model):
    """
    Audit log for every incoming webhook payload from Meta for debugging and replay.
    """
    platform = models.CharField(max_length=50, blank=True, default='', db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    headers = models.JSONField(default=dict, blank=True)
    processed = models.BooleanField(default=False, db_index=True)
    error_message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _('Webhook Log')
        verbose_name_plural = _('Webhook Logs')
        ordering = ['-created_at']

    def __str__(self):
        status = "Processed" if self.processed else "Pending/Failed"
        return f"WebhookLog #{self.id} [{self.platform or 'unknown'}] - {status} ({self.created_at:%Y-%m-%d %H:%M:%S})"
