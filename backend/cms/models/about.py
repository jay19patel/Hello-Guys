from django.db import models
from django.utils.translation import gettext_lazy as _
from .base import TimeStampedModel


class AboutProfile(TimeStampedModel):
    """
    Singleton model for About page hero, studio branding, founder bio, mission & vision.
    """
    # Hero Title section (app/about/page.jsx)
    hero_badge = models.CharField(max_length=100, default='01 - About Us')
    hero_title = models.CharField(max_length=255, default='About the Studio & Founder')
    hero_subtitle = models.TextField(
        default='Driven by engineering excellence, built with clarity, and crafted for long-term digital impact.'
    )

    # Studio Header
    studio_badge = models.CharField(max_length=100, default='NJ Tech Studio')
    studio_title = models.CharField(max_length=255, default='Building Smart Digital Solutions')
    studio_subtitle = models.CharField(
        max_length=255,
        default='With clean code, creativity, and a joyful spirit.'
    )

    # Studio Intro
    intro_text = models.TextField(
        default='We design and develop smart, scalable digital solutions for individuals, startups and growing businesses. '
                'From modern web platforms to automation systems and AI-enhanced tools — '
                'our work focuses on clarity, performance and smooth user experience.'
    )
    intro_quote = models.CharField(
        max_length=255,
        default='Code with clarity, build with purpose, deliver with joy.'
    )

    # Founder Card
    founder_name = models.CharField(max_length=100, default='Jay Patel')
    founder_role = models.CharField(max_length=120, default='Founder & Lead Engineer')
    founder_avatar = models.CharField(
        max_length=500,
        default='/photo.png',
        help_text="Path or URL to founder portrait"
    )
    founder_bio = models.TextField(
        default="Hi, I'm Jay Patel — a Python-first Software Developer with 2 years of experience building fast, "
                "secure and scalable systems. I love combining technology and creativity to craft solutions that feel "
                "modern, smooth and impactful. Whether it's backend APIs, full-stack apps, automation or AI/ML — "
                "I build with clarity, quality and purpose."
    )

    # Mission & Vision
    mission_title = models.CharField(max_length=100, default='Mission')
    mission_text = models.TextField(
        default='To build clean, scalable tech that solves real problems. '
                'Helping businesses automate workflows, develop fast applications, '
                'integrate AI/ML where it matters, and create smooth digital experiences.'
    )
    vision_title = models.CharField(max_length=100, default='Vision')
    vision_text = models.TextField(
        default='To grow a joyful, impactful tech studio that builds modern solutions, '
                'inspires developers, and brings creativity into engineering. '
                'A place where code, content and collaboration meet — and ideas from small towns reach the world.'
    )

    class Meta:
        verbose_name = _('About Studio Profile')
        verbose_name_plural = _('About Studio Profile')

    def __str__(self):
        return f"About Studio: {self.studio_title}"

    @classmethod
    def get_solo(cls) -> 'AboutProfile':
        obj, _ = cls.objects.get_or_create(id=1)
        return obj


class AboutStat(TimeStampedModel):
    """
    Key metrics & achievements showcased in the Stats row of the About page.
    """
    value = models.CharField(max_length=50, default='2', help_text="Numeric value or text (e.g. '2', '15', 'Full-Stack')")
    suffix = models.CharField(max_length=50, blank=True, default='+ Years', help_text="e.g. '+ Years', '+ Apps', '%'")
    label = models.CharField(max_length=100, default='Experience', help_text="Metric label, e.g. 'Experience', 'Clean Code'")
    is_counter = models.BooleanField(
        default=True,
        help_text="If True, renders animated counter; if False, renders static text"
    )
    is_dark = models.BooleanField(
        default=True,
        help_text="Dark theme card (bg-zinc-950) vs light theme card (bg-white)"
    )
    order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = _('About Metric / Stat')
        verbose_name_plural = _('About Metrics & Stats')
        ordering = ['order', 'id']

    def __str__(self):
        return f"{self.value}{self.suffix} - {self.label}"


class AboutBlock(TimeStampedModel):
    """
    Extensible Bento blocks allowing adding custom sections, milestones, principles or stories in future.
    """
    COL_SPAN_CHOICES = (
        ('6', _('Half Width (col-span-6)')),
        ('12', _('Full Width (col-span-12)')),
    )

    badge = models.CharField(max_length=80, blank=True, default='', help_text="Category tag, e.g. 'Values', 'Culture'")
    title = models.CharField(max_length=200, help_text="Block title")
    content = models.TextField(help_text="Rich content or narrative")
    icon = models.CharField(max_length=50, blank=True, default='', help_text="Optional Lucide icon name")
    col_span = models.CharField(max_length=10, choices=COL_SPAN_CHOICES, default='6')
    is_dark = models.BooleanField(default=False, help_text="Dark theme card vs Light theme card")
    order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = _('About Custom Block')
        verbose_name_plural = _('About Custom Blocks')
        ordering = ['order', 'created_at']

    def __str__(self):
        return self.title

