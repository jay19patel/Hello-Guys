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

