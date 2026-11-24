from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils.translation import gettext_lazy as _
from .account import PlatformAccount
from .contact import Contact


class Message(models.Model):
    """
    Unified record of incoming and outgoing messages across WhatsApp and Instagram channels.
    """
    DIRECTION_INCOMING = 'incoming'
    DIRECTION_OUTGOING = 'outgoing'
    DIRECTION_CHOICES = (
        (DIRECTION_INCOMING, _('Incoming')),
        (DIRECTION_OUTGOING, _('Outgoing')),
    )

    TYPE_TEXT = 'text'
    TYPE_IMAGE = 'image'
    TYPE_VIDEO = 'video'
    TYPE_AUDIO = 'audio'
    TYPE_DOCUMENT = 'document'
    TYPE_STICKER = 'sticker'
    TYPE_LOCATION = 'location'
    TYPE_REACTION = 'reaction'
    TYPE_TEMPLATE = 'template'
    TYPE_COMMENT = 'comment'
    TYPE_UNKNOWN = 'unknown'

    MESSAGE_TYPE_CHOICES = (
        (TYPE_TEXT, _('Text')),
        (TYPE_IMAGE, _('Image')),
        (TYPE_VIDEO, _('Video')),
        (TYPE_AUDIO, _('Audio')),
        (TYPE_DOCUMENT, _('Document')),
        (TYPE_STICKER, _('Sticker')),
        (TYPE_LOCATION, _('Location')),
        (TYPE_REACTION, _('Reaction')),
        (TYPE_TEMPLATE, _('Template')),
        (TYPE_COMMENT, _('Comment')),
        (TYPE_UNKNOWN, _('Unknown')),
    )

    STATUS_SENT = 'sent'
    STATUS_DELIVERED = 'delivered'
    STATUS_READ = 'read'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = (
        (STATUS_SENT, _('Sent')),
        (STATUS_DELIVERED, _('Delivered')),
        (STATUS_READ, _('Read')),
        (STATUS_FAILED, _('Failed')),
    )

    platform_account = models.ForeignKey(
        PlatformAccount,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    message_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Meta wamid (WhatsApp) or mid (Instagram)"
    )
    direction = models.CharField(
        max_length=10,
        choices=DIRECTION_CHOICES,
        default=DIRECTION_INCOMING,
        db_index=True
    )
    message_type = models.CharField(
        max_length=20,
        choices=MESSAGE_TYPE_CHOICES,
        default=TYPE_TEXT,
        db_index=True
    )
    message_text = models.TextField(blank=True, default='')
    media_id = models.CharField(max_length=255, blank=True, default='')
    media_url = models.TextField(blank=True, default='')
    media_mime_type = models.CharField(max_length=100, blank=True, default='')
    caption = models.TextField(blank=True, default='')
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='sent_messages',
        help_text="Team member who sent this reply; empty for incoming messages and auto-DMs"
    )
    is_replied = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DELIVERED,
        db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Message')
        verbose_name_plural = _('Messages')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['platform_account', 'contact', 'created_at']),
            models.Index(fields=['direction', 'status']),
            models.Index(fields=['message_type']),
        ]

    @classmethod
    def create_once(cls, **fields) -> "Message | None":
        """Create the message, or None when Meta delivered it twice (concurrently). A duplicate never aborts the
        rest of a webhook payload: the savepoint rolls back only this row."""
        try:
            with transaction.atomic():
                return cls.objects.create(**fields)
        except IntegrityError:
            return None

    def __str__(self):
        text_snippet = (self.message_text[:30] + '...') if len(self.message_text) > 30 else (self.message_text or f"[{self.message_type}]")
        return f"[{self.direction.upper()}] {self.contact} - {text_snippet}"


class PendingAIReply(models.Model):
    """AI-drafted reply waiting for human approval before sending to the contact."""

    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = (
        (STATUS_PENDING, _('Pending approval')),
        (STATUS_APPROVED, _('Approved & sent')),
        (STATUS_REJECTED, _('Rejected')),
        (STATUS_FAILED, _('Send failed')),
    )

    platform_account = models.ForeignKey(
        PlatformAccount,
        on_delete=models.CASCADE,
        related_name='pending_ai_replies',
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name='pending_ai_replies',
    )
    trigger_message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='triggered_approvals',
    )
    draft_text = models.TextField()
    edited_text = models.TextField(blank=True, default='')
    reason = models.CharField(
        max_length=255,
        blank=True,
        default='Account requires human approval before sending AI replies',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_ai_replies',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    sent_message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approval_records',
    )
    error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self) -> str:
        return f"Approval #{self.pk} ({self.status}) -> {self.contact}"
