from django.db import models
from django.utils.translation import gettext_lazy as _
from .base import TimeStampedModel


class TechSkill(TimeStampedModel):
    """
    Skills & developer tools grouped by category (tools.json).
    """
    CATEGORY_BACKEND = 'backend'
    CATEGORY_FRONTEND = 'frontend'
    CATEGORY_DATABASE = 'database'
    CATEGORY_CLOUD = 'cloud'
    CATEGORY_AI_ML = 'ai_ml'
    CATEGORY_TOOLS = 'tools'

    CATEGORY_CHOICES = (
        (CATEGORY_BACKEND, _('Backend & Architecture')),
        (CATEGORY_FRONTEND, _('Frontend & UI')),
        (CATEGORY_DATABASE, _('Databases & Search')),
        (CATEGORY_CLOUD, _('Cloud & DevOps')),
        (CATEGORY_AI_ML, _('AI & Machine Learning')),
        (CATEGORY_TOOLS, _('Developer Tools')),
    )

    name = models.CharField(max_length=100)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default=CATEGORY_BACKEND, db_index=True)
    icon_url = models.CharField(max_length=500, blank=True, default='', help_text="SVG/PNG icon path or URL")
    proficiency_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = _('Tech Skill / Tool')
        verbose_name_plural = _('Tech Skills & Tools')
        ordering = ['category', 'proficiency_order', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"

