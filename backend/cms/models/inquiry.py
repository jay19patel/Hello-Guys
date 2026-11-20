import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _
from .base import TimeStampedModel


def generate_inquiry_id() -> str:
    return f"sub_{uuid.uuid4().hex[:12]}"


class InquirySubmission(TimeStampedModel):
    """
    Lead / contact submissions from website modals or contact forms (submissions.json).
    """
    TYPE_MODAL = 'Project Inquiry Modal'
    TYPE_CONTACT = 'Contact Form'
    TYPE_CONSULT = 'Quick Consultation'

    inquiry_id = models.CharField(max_length=40, default=generate_inquiry_id, unique=True, db_index=True)
    name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=50, blank=True, default='Not provided')
    subject = models.CharField(max_length=255, blank=True, default='New Project Inquiry')
    message = models.TextField()
    inquiry_type = models.CharField(max_length=60, default=TYPE_MODAL, db_index=True)
    
    # Internal status & follow-up
    is_replied = models.BooleanField(default=False, db_index=True)
    replies = models.JSONField(default=list, blank=True, help_text="Audit log of replies sent to this lead")

    class Meta:
        verbose_name = _('Inquiry Submission')
        verbose_name_plural = _('Inquiry Submissions')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - {self.subject} ({self.inquiry_id})"

