from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from .base import TimeStampedModel


class BlogPost(TimeStampedModel):
    """
    Developer blog articles written in Markdown (post-schema.js).
    """
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, db_index=True)
    content = models.TextField(help_text="Article body in Markdown format")
    category = models.CharField(max_length=60, default='Tutorial', db_index=True, help_text="e.g. Tutorial, Case Study, AI Update")
    cover_image = models.CharField(max_length=1000, blank=True, default='', help_text="URL or /public image path")
    link = models.URLField(max_length=1000, blank=True, default='', help_text="Optional CTA URL")
    link_text = models.CharField(max_length=60, default='Read More')
    
    # Metrics
    views_count = models.PositiveIntegerField(default=0)
    likes_count = models.PositiveIntegerField(default=0)
    
    # Publishing
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_published = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = _('Blog Post')
        verbose_name_plural = _('Blog Posts')
        ordering = ['-published_at', '-created_at']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)


class BlogComment(TimeStampedModel):
    """
    Public visitor comments left on blog articles.
    """
    post = models.ForeignKey(BlogPost, on_delete=models.CASCADE, related_name='comments', db_index=True)
    author_name = models.CharField(max_length=100, default='Guest')
    author_hash = models.CharField(max_length=64, blank=True, db_index=True, help_text="Hashed visitor fingerprint")
    text = models.TextField(max_length=1000)
    is_approved = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = _('Blog Comment')
        verbose_name_plural = _('Blog Comments')
        ordering = ['-created_at']

    def __str__(self):
        return f"Comment by {self.author_name} on {self.post.title}"


class BlogLike(TimeStampedModel):
    """
    Unique visitor likes for blog articles.
    """
    post = models.ForeignKey(BlogPost, on_delete=models.CASCADE, related_name='likes', db_index=True)
    author_hash = models.CharField(max_length=64, db_index=True)

    class Meta:
        verbose_name = _('Blog Like')
        verbose_name_plural = _('Blog Likes')
        unique_together = ('post', 'author_hash')

    def __str__(self):
        return f"Like on {self.post.title} from {self.author_hash[:8]}"

