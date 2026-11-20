from django.db import models
from django.utils.translation import gettext_lazy as _
from .base import TimeStampedModel


class FAQ(TimeStampedModel):
    """
    Frequently Asked Questions (faqs.json).
    """
    question = models.CharField(max_length=300)
    answer = models.TextField()
    category = models.CharField(max_length=100, blank=True, default='General', db_index=True)
    order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = _('FAQ')
        verbose_name_plural = _('FAQs')
        ordering = ['order', 'id']

    def __str__(self):
        return self.question

