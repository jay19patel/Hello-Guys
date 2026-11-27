from django.db import models
from django.utils.translation import gettext_lazy as _
from .base import TimeStampedModel


class Testimonial(TimeStampedModel):
    """
    Client and collaborator recommendations (testimonials.json).
    """
    __test__ = False  # prevent pytest from mistaking "Test*" model for a test class
    name = models.CharField(max_length=150, help_text="e.g. Videoly Client, Sayaji Infotech")
    role = models.CharField(max_length=150, help_text="e.g. Operations Manager, CEO, CTO")
    text = models.TextField(help_text="Feedback / review content")
    avatar = models.CharField(max_length=500, blank=True, default='', help_text="Avatar image path or URL")
    rating = models.PositiveSmallIntegerField(default=5, help_text="1 to 5 stars")
    order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = _('Testimonial')
        verbose_name_plural = _('Testimonials')
        ordering = ['order', '-created_at']

    def __str__(self):
        return f"{self.name} - {self.role}"

