from django.db import models
from django.utils.translation import gettext_lazy as _
from .base import TimeStampedModel


class YouTubeVideo(TimeStampedModel):
    """
    YouTube tutorials and project showcases (youtube.json).
    """
    video_id = models.CharField(max_length=50, unique=True, help_text="YouTube 11-char video ID, e.g. tiBeLLv5GJo")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    badge = models.CharField(max_length=80, blank=True, default='TUTORIAL', help_text="e.g. FASTAPI & DB, AI AGENTS")
    order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = _('YouTube Video')
        verbose_name_plural = _('YouTube Videos')
        ordering = ['order', '-created_at']

    def __str__(self):
        return self.title

