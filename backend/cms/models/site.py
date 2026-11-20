from django.db import models
from django.utils.translation import gettext_lazy as _
from .base import TimeStampedModel


class SiteSettings(TimeStampedModel):
    """
    Global website settings and profile information (Singleton model).
    Powers header, footer, audio player, social links and SEO defaults.
    """
    brand_name = models.CharField(max_length=100, default='NJTechStudio')
    tagline = models.CharField(max_length=255, default='Custom Web + AI Automation Studio')
    bio = models.TextField(blank=True, default='Full-Stack & AI Systems Engineer shipping fast, reliable production software.')
    avatar_url = models.CharField(max_length=500, blank=True, default='/icons/jay-avatar.png')
    
    # Contact info
    contact_email = models.EmailField(default='justjayy19@gmail.com')
    phone_number = models.CharField(max_length=50, blank=True, default='+91 98765 43210')
    location = models.CharField(max_length=150, blank=True, default='Gujarat, India')
    
    # Social links
    github_url = models.URLField(blank=True, default='https://github.com/jay19patel')
    linkedin_url = models.URLField(blank=True, default='https://www.linkedin.com/in/jay-patel-843818169/')
    youtube_url = models.URLField(blank=True, default='https://www.youtube.com/@njtechstudio')
    twitter_url = models.URLField(blank=True, default='https://x.com/jaypatel_dev')
    
    # Extra resources
    resume_url = models.CharField(max_length=500, blank=True, default='/Jay_Patel_Resume.pdf')
    audio_track_url = models.CharField(max_length=500, blank=True, default='/audio/background-track.mp3', help_text="Navbar ambient track")
    
    # SEO
    meta_title = models.CharField(max_length=200, default='NJTechStudio | AI Automation & Web Development')
    meta_description = models.TextField(default='High-performance web apps, AI integrations, ERP solutions, and custom automated systems.')

    # Maintenance Mode & Offline CMS
    maintenance_mode = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("Put the entire public website into Maintenance Mode.")
    )
    maintenance_title = models.CharField(
        max_length=200,
        default='Website Under Scheduled Maintenance',
        blank=True,
        help_text=_("Title shown to visitors when maintenance mode is active.")
    )
    maintenance_message = models.TextField(
        default='We are upgrading our core engines and deploying production enhancements. All services will resume shortly.',
        blank=True,
        help_text=_("Message shown to visitors when maintenance mode is active.")
    )
    maintenance_estimated_uptime = models.CharField(
        max_length=100,
        default='Back online within 30 minutes',
        blank=True,
        help_text=_("Estimated completion time (e.g. 'Back online in 30 mins').")
    )

    # Granular API Switches (Enable/Disable individual APIs)
    api_projects_enabled = models.BooleanField(
        default=True,
        help_text=_("Enable or disable Projects / Case Studies API.")
    )
    api_blog_enabled = models.BooleanField(
        default=True,
        help_text=_("Enable or disable Blog Articles API.")
    )
    api_testimonials_enabled = models.BooleanField(
        default=True,
        help_text=_("Enable or disable Testimonials / Reviews API.")
    )
    api_faqs_enabled = models.BooleanField(
        default=True,
        help_text=_("Enable or disable FAQs API.")
    )
    api_videos_enabled = models.BooleanField(
        default=True,
        help_text=_("Enable or disable YouTube Tutorials API.")
    )
    api_skills_enabled = models.BooleanField(
        default=True,
        help_text=_("Enable or disable Tech Skills API.")
    )
    api_inquiries_enabled = models.BooleanField(
        default=True,
        help_text=_("Enable or disable Contact / Lead Form Submission API.")
    )

    class Meta:
        verbose_name = _('Site Settings')
        verbose_name_plural = _('Site Settings')

    def __str__(self):
        return f"{self.brand_name} Settings"

    @classmethod
    def get_solo(cls) -> 'SiteSettings':
        obj, _ = cls.objects.get_or_create(id=1)
        return obj

