from django.contrib import admin
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


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ['brand_name', 'tagline', 'contact_email', 'phone_number', 'updated_at']

    def has_add_permission(self, request):
        # Disallow creating multiple site settings instances
        return not SiteSettings.objects.exists()


@admin.register(Solution)
class SolutionAdmin(admin.ModelAdmin):
    list_display = ['num', 'title', 'tag', 'icon', 'badge', 'order', 'is_active']
    list_filter = ['is_active', 'is_light']
    search_fields = ['title', 'tag', 'description']
    prepopulated_fields = {'slug': ('title',)}
    list_editable = ['order', 'is_active']


class ProjectSectionInline(admin.StackedInline):
    model = ProjectSection
    extra = 1


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'status', 'featured', 'order', 'is_active', 'created_at']
    list_filter = ['category', 'featured', 'status', 'is_active']
    search_fields = ['title', 'subtitle', 'description', 'technologies']
    prepopulated_fields = {'slug': ('title',)}
    list_editable = ['featured', 'order', 'is_active']
    inlines = [ProjectSectionInline]


@admin.register(TechSkill)
class TechSkillAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'proficiency_order', 'is_active']
    list_filter = ['category', 'is_active']
    search_fields = ['name']
    list_editable = ['proficiency_order', 'is_active']


@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = ['name', 'role', 'rating', 'order', 'is_active', 'created_at']
    list_filter = ['rating', 'is_active']
    search_fields = ['name', 'role', 'text']
    list_editable = ['order', 'is_active']


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    list_display = ['question', 'category', 'order', 'is_active']
    list_filter = ['category', 'is_active']
    search_fields = ['question', 'answer']
    list_editable = ['order', 'is_active']


@admin.register(YouTubeVideo)
class YouTubeVideoAdmin(admin.ModelAdmin):
    list_display = ['title', 'video_id', 'badge', 'order', 'is_active']
    list_filter = ['is_active', 'badge']
    search_fields = ['title', 'video_id', 'description']
    list_editable = ['order', 'is_active']


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'is_published', 'published_at', 'views_count', 'likes_count']
    list_filter = ['category', 'is_published']
    search_fields = ['title', 'content', 'slug']
    prepopulated_fields = {'slug': ('title',)}
    list_editable = ['is_published']


@admin.register(BlogComment)
class BlogCommentAdmin(admin.ModelAdmin):
    list_display = ['author_name', 'post', 'is_approved', 'created_at']
    list_filter = ['is_approved', 'created_at']
    search_fields = ['author_name', 'text', 'post__title']
    list_editable = ['is_approved']


@admin.register(BlogLike)
class BlogLikeAdmin(admin.ModelAdmin):
    list_display = ['post', 'author_hash', 'created_at']
    search_fields = ['author_hash', 'post__title']


@admin.register(InquirySubmission)
class InquirySubmissionAdmin(admin.ModelAdmin):
    list_display = ['inquiry_id', 'name', 'email', 'phone', 'inquiry_type', 'is_replied', 'created_at']
    list_filter = ['inquiry_type', 'is_replied', 'created_at']
    search_fields = ['name', 'email', 'phone', 'subject', 'message', 'inquiry_id']
    readonly_fields = ['inquiry_id', 'created_at', 'updated_at']
    list_editable = ['is_replied']


@admin.register(WorkflowStep)
class WorkflowStepAdmin(admin.ModelAdmin):
    list_display = ['step_num', 'title', 'tag', 'icon', 'order', 'is_active']
    list_filter = ['is_active']
    search_fields = ['title', 'tag', 'description']
    list_editable = ['order', 'is_active']


@admin.register(AboutProfile)
class AboutProfileAdmin(admin.ModelAdmin):
    list_display = ['studio_title', 'founder_name', 'founder_role', 'updated_at']

    def has_add_permission(self, request):
        return not AboutProfile.objects.exists()


@admin.register(AboutStat)
class AboutStatAdmin(admin.ModelAdmin):
    list_display = ['value', 'suffix', 'label', 'is_counter', 'is_dark', 'order', 'is_active']
    list_filter = ['is_counter', 'is_dark', 'is_active']
    search_fields = ['label', 'value']
    list_editable = ['order', 'is_active']


@admin.register(AboutBlock)
class AboutBlockAdmin(admin.ModelAdmin):
    list_display = ['title', 'badge', 'col_span', 'is_dark', 'order', 'is_active']
    list_filter = ['col_span', 'is_dark', 'is_active']
    search_fields = ['title', 'badge', 'content']
    list_editable = ['order', 'is_active']


