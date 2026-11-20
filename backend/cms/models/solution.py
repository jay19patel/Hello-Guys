from django.db import models
from django.utils.translation import gettext_lazy as _
from .base import TimeStampedModel


class Solution(TimeStampedModel):
    """
    Featured service or core architecture highlight shown on Homepage solutions section.
    """
    num = models.CharField(max_length=10, default='01', help_text="e.g. 01, 02")
    tag = models.CharField(max_length=120, help_text="e.g. Fintech & Algorithmic Trading")
    title = models.CharField(max_length=200)
    description = models.TextField()
    slug = models.SlugField(max_length=200, unique=True)
    icon = models.CharField(max_length=50, default='Rocket', help_text="Lucide icon name, e.g. Rocket, Code2, Layers")
    is_light = models.BooleanField(default=False, help_text="Visual light card styling")
    image = models.CharField(max_length=500, blank=True, default='', help_text="Image URL or public path")
    tech_stack = models.JSONField(default=list, help_text="List of technology names, e.g. ['Python', 'Celery']")
    badge = models.CharField(max_length=80, blank=True, default='Live System')
    action_text = models.CharField(max_length=100, default='Explore Case Study')
    order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = _('Solution')
        verbose_name_plural = _('Solutions')
        ordering = ['order', 'created_at']

    def __str__(self):
        return f"{self.num} - {self.title}"

