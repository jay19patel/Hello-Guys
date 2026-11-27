from rest_framework import serializers
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
    WorkflowStep,
    AboutProfile,
    AboutStat,
    AboutBlock,
)


class SiteSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSettings
        exclude = ['id', 'created_at', 'updated_at']


class SolutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Solution
        fields = [
            'id', 'num', 'tag', 'title', 'description', 'slug', 'icon',
            'is_light', 'image', 'tech_stack', 'badge', 'action_text', 'order'
        ]


class ProjectSectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectSection
        fields = ['id', 'section_type', 'title', 'content', 'headers', 'rows', 'order']


class ProjectListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = [
            'id', 'slug', 'title', 'subtitle', 'description', 'category',
            'featured', 'image', 'technologies', 'live_url', 'github_url',
            'start_date', 'end_date', 'status', 'client', 'order'
        ]


class ProjectDetailSerializer(serializers.ModelSerializer):
    sections = ProjectSectionSerializer(many=True, read_only=True)

    class Meta:
        model = Project
        fields = [
            'id', 'slug', 'title', 'subtitle', 'description', 'category',
            'featured', 'image', 'technologies', 'live_url', 'github_url',
            'start_date', 'end_date', 'status', 'client', 'overview',
            'challenge', 'solution', 'sections', 'created_at'
        ]


class TechSkillSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(source='get_category_display', read_only=True)

    class Meta:
        model = TechSkill
        fields = ['id', 'name', 'category', 'category_display', 'icon_url', 'proficiency_order']


class TestimonialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Testimonial
        fields = ['id', 'name', 'role', 'text', 'avatar', 'rating', 'order']


class FAQSerializer(serializers.ModelSerializer):
    class Meta:
        model = FAQ
        fields = ['id', 'question', 'answer', 'category', 'order']


class YouTubeVideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = YouTubeVideo
        fields = ['id', 'video_id', 'title', 'description', 'badge', 'order']


class BlogPostListSerializer(serializers.ModelSerializer):
    date = serializers.DateTimeField(source='published_at', format='%Y-%m-%d', read_only=True)
    image = serializers.CharField(source='cover_image', read_only=True)

    class Meta:
        model = BlogPost
        fields = [
            'id', 'slug', 'title', 'category', 'date', 'image',
            'link', 'link_text', 'views_count', 'likes_count'
        ]


class BlogPostDetailSerializer(serializers.ModelSerializer):
    date = serializers.DateTimeField(source='published_at', format='%Y-%m-%d', read_only=True)
    image = serializers.CharField(source='cover_image', read_only=True)

    class Meta:
        model = BlogPost
        fields = [
            'id', 'slug', 'title', 'content', 'category', 'date', 'image',
            'link', 'link_text', 'views_count', 'likes_count', 'created_at'
        ]


class BlogCommentSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source='pk', read_only=True)
    author = serializers.CharField(source='author_name', read_only=True)
    date = serializers.DateTimeField(source='created_at', read_only=True)

    class Meta:
        model = BlogComment
        fields = ['id', 'author', 'text', 'date']


class WorkflowStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowStep
        fields = [
            'id', 'step_num', 'title', 'tag', 'description',
            'icon', 'phase_milestone', 'order'
        ]


class AboutProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = AboutProfile
        exclude = ['id', 'created_at', 'updated_at']


class AboutStatSerializer(serializers.ModelSerializer):
    class Meta:
        model = AboutStat
        fields = ['id', 'value', 'suffix', 'label', 'is_counter', 'is_dark', 'order']


class AboutBlockSerializer(serializers.ModelSerializer):
    class Meta:
        model = AboutBlock
        fields = ['id', 'badge', 'title', 'content', 'icon', 'col_span', 'is_dark', 'order']


