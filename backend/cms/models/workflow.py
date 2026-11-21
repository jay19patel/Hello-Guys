from django.db import models
from django.utils.translation import gettext_lazy as _
from .base import TimeStampedModel


class WorkflowStep(TimeStampedModel):
    """
    Startup & AI Execution Flow step shown on Homepage CodingFeaturesSection.
    """
    step_num = models.CharField(max_length=10, default='01', help_text="e.g. 01, 02")
    title = models.CharField(max_length=200, help_text="e.g. Blueprint & System Architecture")
    tag = models.CharField(max_length=120, help_text="e.g. Discovery & Design")
    description = models.TextField(help_text="Detailed step narrative")
    icon = models.CharField(
        max_length=60,
        default='GitBranch',
        help_text="Lucide icon name (e.g. GitBranch, Sparkles, ShieldCheck, Rocket, Cpu, Layers)"
    )
    phase_milestone = models.CharField(
        max_length=120,
        blank=True,
        default='',
        help_text="Footer milestone label, e.g. 'Phase 01 Milestone'"
    )
    order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = _('Workflow Step')
        verbose_name_plural = _('Workflow Steps (Execution Flow)')
        ordering = ['order', 'step_num']

    def __str__(self):
        return f"{self.step_num} - {self.title}"

