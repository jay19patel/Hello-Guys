from django.db import models
from django.utils.translation import gettext_lazy as _
from .base import TimeStampedModel


class Project(TimeStampedModel):
    """
    Portfolio project / case study (from projects.json).
    """
    slug = models.SlugField(max_length=200, unique=True, db_index=True)
    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=300, blank=True, default='')
    description = models.TextField()
    category = models.CharField(max_length=100, default='Web Application', db_index=True)
    featured = models.BooleanField(default=False, db_index=True)
    image = models.CharField(max_length=500, blank=True, default='')
    technologies = models.JSONField(default=list, help_text="List of technologies, e.g. ['Django', 'PostgreSQL']")
    
    # External Links
    live_url = models.URLField(max_length=500, blank=True, default='')
    github_url = models.URLField(max_length=500, blank=True, default='')
    
    # Meta info
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=50, default='Live')
    client = models.CharField(max_length=100, blank=True, default='Personal')
    
    # Case study content
    overview = models.TextField(blank=True, default='')
    challenge = models.TextField(blank=True, default='')
    solution = models.TextField(blank=True, default='')
    
    order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = _('Project')
        verbose_name_plural = _('Projects')
        ordering = ['order', '-created_at']

    def __str__(self):
        return self.title


class ProjectSection(TimeStampedModel):
    """
    Structured section within a case study (text list or tech stack table).
    """
    TYPE_TEXT = 'text'
    TYPE_TABLE = 'table'
    TYPE_CHOICES = (
        (TYPE_TEXT, _('Text / Bullet List')),
        (TYPE_TABLE, _('Data Table')),
    )

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='sections')
    section_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_TEXT)
    title = models.CharField(max_length=200)
    content = models.TextField(blank=True, default='', help_text="Text content or markdown bullets")
    headers = models.JSONField(default=list, blank=True, help_text="Table column headers, e.g. ['Component', 'Technology']")
    rows = models.JSONField(default=list, blank=True, help_text="Table rows: list of lists, e.g. [['Backend', 'Django']]")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _('Project Section')
        verbose_name_plural = _('Project Sections')
        ordering = ['order', 'id']

    def __str__(self):
        return f"{self.project.title} - {self.title}"

