"""
CMS Admin Configuration
=======================
All CMS content is managed exclusively through Django Admin.
Frontend consumes read-only APIs; CRUD happens here.
"""

from django.contrib import admin
from django.utils.html import format_html
from cms.models import (
    SiteSettings,
    Solution,
    Project,
    ProjectSection,
    TechSkill,
    Testimonial,
    FAQ,
    YouTubeVideo,
    BlogPost,
    BlogComment,
    BlogLike,
    InquirySubmission,
    WorkflowStep,
    AboutProfile,
    AboutStat,
    AboutBlock,
)


# ==============================================================================
# Admin Site Branding
# ==============================================================================

admin.site.site_header = "NJTechStudio CMS"
admin.site.site_title = "CMS Admin"
admin.site.index_title = "Content Management"


# ==============================================================================
# 1. Site Settings (Singleton)
# ==============================================================================

@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Global website settings — only one instance allowed."""

    fieldsets = (
        ("Brand Identity", {
            'fields': ('brand_name', 'tagline', 'bio', 'avatar_url'),
        }),
        ("Contact Information", {
            'fields': ('contact_email', 'phone_number', 'location'),
        }),
        ("Social Links", {
            'fields': ('github_url', 'linkedin_url', 'youtube_url', 'twitter_url'),
            'classes': ('collapse',),
        }),
        ("Resources", {
            'fields': ('resume_url', 'audio_track_url'),
            'classes': ('collapse',),
        }),
        ("SEO Defaults", {
            'fields': ('meta_title', 'meta_description'),
        }),
        ("Maintenance Mode", {
            'fields': (
                'maintenance_mode',
                'maintenance_title',
                'maintenance_message',
                'maintenance_estimated_uptime',
            ),
            'classes': ('collapse',),
            'description': "When enabled, the entire public website shows a maintenance page.",
        }),
        ("API Feature Switches", {
            'fields': (
                'api_projects_enabled',
                'api_blog_enabled',
                'api_testimonials_enabled',
                'api_faqs_enabled',
                'api_videos_enabled',
                'api_skills_enabled',
                'api_workflow_enabled',
                'api_about_enabled',
                'api_inquiries_enabled',
            ),
            'description': "Toggle individual API endpoints on or off without redeploying.",
        }),
    )

    list_display = ('brand_name', 'tagline', 'contact_email', 'maintenance_mode', 'updated_at')
    readonly_fields = ('created_at', 'updated_at')

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# ==============================================================================
# 2. Solutions / Architecture Highlights
# ==============================================================================

@admin.register(Solution)
class SolutionAdmin(admin.ModelAdmin):
    """Featured services and architecture highlights on the Homepage."""

    fieldsets = (
        (None, {
            'fields': ('num', 'tag', 'title', 'slug', 'description'),
        }),
        ("Visual & UI", {
            'fields': ('icon', 'image', 'is_light', 'badge', 'action_text'),
        }),
        ("Tech Stack (JSON)", {
            'fields': ('tech_stack',),
            'classes': ('collapse',),
        }),
        ("Display", {
            'fields': ('order', 'is_active'),
        }),
    )

    list_display = ('num', 'title', 'tag', 'icon', 'badge', 'order', 'is_active')
    list_display_links = ('title',)
    list_filter = ('is_active', 'is_light')
    list_editable = ('order', 'is_active')
    search_fields = ('title', 'tag', 'description')
    prepopulated_fields = {'slug': ('title',)}
    ordering = ('order', 'created_at')


# ==============================================================================
# 3. Projects / Case Studies
# ==============================================================================

class ProjectSectionInline(admin.StackedInline):
    """In-depth case study sections (text blocks or data tables)."""
    model = ProjectSection
    extra = 0
    fieldsets = (
        (None, {
            'fields': ('title', 'section_type', 'content', 'order'),
        }),
        ("Table Data (only for 'table' type)", {
            'fields': ('headers', 'rows'),
            'classes': ('collapse',),
        }),
    )


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    """Portfolio projects and case studies."""

    fieldsets = (
        (None, {
            'fields': ('title', 'slug', 'subtitle', 'category', 'description'),
        }),
        ("Media & Links", {
            'fields': ('image', 'live_url', 'github_url'),
        }),
        ("Case Study Content", {
            'fields': ('overview', 'challenge', 'solution'),
            'classes': ('collapse',),
        }),
        ("Metadata", {
            'fields': ('technologies', 'status', 'client', 'start_date', 'end_date'),
        }),
        ("Display", {
            'fields': ('featured', 'order', 'is_active'),
        }),
    )

    list_display = ('title', 'category', 'status', 'featured', 'order', 'is_active', 'created_at')
    list_display_links = ('title',)
    list_filter = ('category', 'featured', 'status', 'is_active')
    list_editable = ('featured', 'order', 'is_active')
    search_fields = ('title', 'subtitle', 'description')
    prepopulated_fields = {'slug': ('title',)}
    inlines = (ProjectSectionInline,)
    date_hierarchy = 'created_at'
    ordering = ('order', '-created_at')


# ==============================================================================
# 4. Tech Skills & Tools
# ==============================================================================

@admin.register(TechSkill)
class TechSkillAdmin(admin.ModelAdmin):
    """Developer skills and tools grouped by category (About page)."""

    list_display = ('name', 'category', 'icon_preview', 'proficiency_order', 'is_active')
    list_display_links = ('name',)
    list_filter = ('category', 'is_active')
    list_editable = ('proficiency_order', 'is_active')
    search_fields = ('name',)
    ordering = ('category', 'proficiency_order', 'name')

    @admin.display(description="Icon")
    def icon_preview(self, obj):
        if obj.icon_url:
            return format_html('<img src="{}" width="20" height="20" style="vertical-align:middle;" />', obj.icon_url)
        return "—"


# ==============================================================================
# 5. Testimonials / Reviews
# ==============================================================================

@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):
    """Client and collaborator testimonials."""

    list_display = ('name', 'role', 'rating_stars', 'order', 'is_active', 'created_at')
    list_display_links = ('name',)
    list_filter = ('rating', 'is_active')
    list_editable = ('order', 'is_active')
    search_fields = ('name', 'role', 'text')
    ordering = ('order', '-created_at')

    @admin.display(description="Rating")
    def rating_stars(self, obj):
        return "★" * obj.rating + "☆" * (5 - obj.rating)


# ==============================================================================
# 6. FAQs
# ==============================================================================

@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    """Frequently Asked Questions shown on the Homepage."""

    list_display = ('short_question', 'category', 'order', 'is_active')
    list_display_links = ('short_question',)
    list_filter = ('category', 'is_active')
    list_editable = ('order', 'is_active')
    search_fields = ('question', 'answer')
    ordering = ('order', 'id')

    @admin.display(description="Question")
    def short_question(self, obj):
        return obj.question[:80] + "…" if len(obj.question) > 80 else obj.question


# ==============================================================================
# 7. YouTube Videos
# ==============================================================================

@admin.register(YouTubeVideo)
class YouTubeVideoAdmin(admin.ModelAdmin):
    """YouTube tutorials and showcase videos."""

    list_display = ('title', 'video_id', 'badge', 'order', 'is_active')
    list_display_links = ('title',)
    list_filter = ('is_active', 'badge')
    list_editable = ('order', 'is_active')
    search_fields = ('title', 'video_id', 'description')
    ordering = ('order', '-created_at')


# ==============================================================================
# 8. Blog Posts & Engagement
# ==============================================================================

class BlogCommentInline(admin.TabularInline):
    """Blog comments displayed inline on the blog post edit page."""
    model = BlogComment
    extra = 0
    fields = ('author_name', 'text', 'is_approved', 'created_at')
    readonly_fields = ('author_name', 'author_hash', 'text', 'created_at')


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    """Developer blog articles with Markdown content."""

    fieldsets = (
        (None, {
            'fields': ('title', 'slug', 'category', 'is_published'),
        }),
        ("Content", {
            'fields': ('content', 'cover_image'),
        }),
        ("Call to Action", {
            'fields': ('link', 'link_text'),
            'classes': ('collapse',),
        }),
        ("Metrics (read-only)", {
            'fields': ('views_count', 'likes_count', 'published_at'),
        }),
    )

    list_display = ('title', 'category', 'is_published', 'views_count', 'likes_count', 'published_at')
    list_display_links = ('title',)
    list_filter = ('category', 'is_published')
    list_editable = ('is_published',)
    search_fields = ('title', 'content', 'slug')
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ('views_count', 'likes_count')
    inlines = (BlogCommentInline,)
    date_hierarchy = 'created_at'
    ordering = ('-published_at', '-created_at')


@admin.register(BlogComment)
class BlogCommentAdmin(admin.ModelAdmin):
    """Moderate visitor comments on blog articles."""

    list_display = ('author_name', 'post', 'short_text', 'is_approved', 'created_at')
    list_display_links = ('author_name',)
    list_filter = ('is_approved', 'created_at')
    list_editable = ('is_approved',)
    search_fields = ('author_name', 'text', 'post__title')
    readonly_fields = ('author_hash', 'created_at')
    ordering = ('-created_at',)

    @admin.display(description="Comment")
    def short_text(self, obj):
        return obj.text[:60] + "…" if len(obj.text) > 60 else obj.text


@admin.register(BlogLike)
class BlogLikeAdmin(admin.ModelAdmin):
    """Blog post likes (read-only audit log)."""

    list_display = ('post', 'author_hash_short', 'created_at')
    search_fields = ('author_hash', 'post__title')
    readonly_fields = ('post', 'author_hash', 'created_at')
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False

    @admin.display(description="Visitor")
    def author_hash_short(self, obj):
        return obj.author_hash[:12] + "…"


# ==============================================================================
# 9. Inquiry Submissions / Leads CRM
# ==============================================================================

@admin.register(InquirySubmission)
class InquirySubmissionAdmin(admin.ModelAdmin):
    """Contact form submissions and project inquiry leads."""

    fieldsets = (
        ("Lead Information", {
            'fields': ('inquiry_id', 'name', 'email', 'phone', 'inquiry_type'),
        }),
        ("Inquiry Details", {
            'fields': ('subject', 'message'),
        }),
        ("Status & Follow-up", {
            'fields': ('is_replied', 'replies'),
            'description': "Mark as replied and add follow-up notes in JSON format.",
        }),
        ("Timestamps", {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    list_display = ('inquiry_id', 'name', 'email', 'phone', 'inquiry_type', 'is_replied', 'created_at')
    list_display_links = ('inquiry_id', 'name')
    list_filter = ('inquiry_type', 'is_replied', 'created_at')
    list_editable = ('is_replied',)
    search_fields = ('name', 'email', 'phone', 'subject', 'message', 'inquiry_id')
    readonly_fields = ('inquiry_id', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)


# ==============================================================================
# 10. Workflow Steps (Startup & AI Execution Flow)
# ==============================================================================

@admin.register(WorkflowStep)
class WorkflowStepAdmin(admin.ModelAdmin):
    """Startup & AI Execution Flow steps on the Homepage."""

    list_display = ('step_num', 'title', 'tag', 'icon', 'order', 'is_active')
    list_display_links = ('title',)
    list_filter = ('is_active',)
    list_editable = ('order', 'is_active')
    search_fields = ('title', 'tag', 'description')
    ordering = ('order', 'step_num')


# ==============================================================================
# 11. About Page Content
# ==============================================================================

@admin.register(AboutProfile)
class AboutProfileAdmin(admin.ModelAdmin):
    """About page profile — singleton (hero, founder, mission, vision)."""

    fieldsets = (
        ("Hero Section", {
            'fields': ('hero_badge', 'hero_title', 'hero_subtitle'),
        }),
        ("Studio Header", {
            'fields': ('studio_badge', 'studio_title', 'studio_subtitle'),
        }),
        ("Studio Introduction", {
            'fields': ('intro_text', 'intro_quote'),
        }),
        ("Founder Card", {
            'fields': ('founder_name', 'founder_role', 'founder_avatar', 'founder_bio'),
        }),
        ("Mission & Vision", {
            'fields': ('mission_title', 'mission_text', 'vision_title', 'vision_text'),
        }),
    )

    list_display = ('studio_title', 'founder_name', 'founder_role', 'updated_at')
    readonly_fields = ('created_at', 'updated_at')

    def has_add_permission(self, request):
        return not AboutProfile.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AboutStat)
class AboutStatAdmin(admin.ModelAdmin):
    """Key metrics and achievement stats on the About page."""

    list_display = ('stat_display', 'label', 'is_counter', 'is_dark', 'order', 'is_active')
    list_display_links = ('stat_display',)
    list_filter = ('is_counter', 'is_dark', 'is_active')
    list_editable = ('order', 'is_active')
    search_fields = ('label', 'value')
    ordering = ('order', 'id')

    @admin.display(description="Stat")
    def stat_display(self, obj):
        return f"{obj.value}{obj.suffix}"


@admin.register(AboutBlock)
class AboutBlockAdmin(admin.ModelAdmin):
    """Extensible bento-style content blocks on the About page."""

    list_display = ('title', 'badge', 'col_span', 'is_dark', 'order', 'is_active')
    list_display_links = ('title',)
    list_filter = ('col_span', 'is_dark', 'is_active')
    list_editable = ('order', 'is_active')
    search_fields = ('title', 'badge', 'content')
    ordering = ('order', 'id')
