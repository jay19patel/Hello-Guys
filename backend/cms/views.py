import hashlib
from datetime import timedelta
from typing import Any
from django.utils import timezone
from django.db.models import F, Q
from rest_framework import generics, permissions, status
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.models import (
    SiteSettings,
    Solution,
    Project,
    TechSkill,
    Testimonial,
    FAQ,
    YouTubeVideo,
    BlogPost,
    BlogComment,
    BlogLike,
    WorkflowStep,
    AboutProfile,
    AboutStat,
    AboutBlock,
)
from cms.serializers import (
    SiteSettingsSerializer,
    SolutionSerializer,
    ProjectListSerializer,
    ProjectDetailSerializer,
    TechSkillSerializer,
    TestimonialSerializer,
    FAQSerializer,
    YouTubeVideoSerializer,
    BlogPostListSerializer,
    BlogPostDetailSerializer,
    BlogCommentSerializer,
    WorkflowStepSerializer,
    AboutProfileSerializer,
    AboutStatSerializer,
    AboutBlockSerializer,
)


def make_cached_response(data: Any, request, max_age: int = 120, s_maxage: int = 300) -> Response:
    """
    Wraps payload in an HTTP-cached DRF Response with ETag and 304 Not Modified validation.
    """
    rendered = JSONRenderer().render(data)
    etag = f'W/"{hashlib.md5(rendered).hexdigest()}"'
    client_etag = request.headers.get('If-None-Match') or request.META.get('HTTP_IF_NONE_MATCH')

    cache_control = f"public, max-age={max_age}, s-maxage={s_maxage}, stale-while-revalidate=600"

    if client_etag and client_etag.strip() == etag.strip():
        res = Response(status=status.HTTP_304_NOT_MODIFIED)
        res['ETag'] = etag
        res['Cache-Control'] = cache_control
        res['Vary'] = 'Accept, Origin'
        return res

    res = Response(data, status=status.HTTP_200_OK)
    res['ETag'] = etag
    res['Cache-Control'] = cache_control
    res['Vary'] = 'Accept, Origin'
    return res


def check_maintenance(endpoint_name: str | None = None) -> Response | None:
    """
    Checks if global maintenance mode is enabled, or if a specific API is disabled.
    Returns HTTP 503 Service Unavailable if blocked, otherwise None.
    """
    site = SiteSettings.get_solo()
    if site.maintenance_mode:
        res = Response(
            {
                'error': 'maintenance_mode',
                'maintenance': True,
                'title': site.maintenance_title,
                'message': site.maintenance_message,
                'estimated_uptime': site.maintenance_estimated_uptime,
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE
        )
        res['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
        return res

    api_map = {
        'projects': site.api_projects_enabled,
        'blog': site.api_blog_enabled,
        'testimonials': site.api_testimonials_enabled,
        'faqs': site.api_faqs_enabled,
        'videos': site.api_videos_enabled,
        'skills': site.api_skills_enabled,
        'workflow': site.api_workflow_enabled,
        'about': site.api_about_enabled,
        'inquiries': site.api_inquiries_enabled,
    }
    if endpoint_name and not api_map.get(endpoint_name, True):
        res = Response(
            {
                'error': 'api_disabled',
                'endpoint': endpoint_name,
                'message': f"{endpoint_name.title()} API is temporarily disabled for scheduled maintenance.",
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE
        )
        res['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
        return res
    return None


class SiteConfigAPIView(generics.RetrieveAPIView):
    """
    Returns global website branding, profile, contact, audio, and SEO configuration.
    Endpoint: GET /api/cms/site-config/
    """
    serializer_class = SiteSettingsSerializer
    permission_classes = [permissions.AllowAny]

    def get_object(self):
        return SiteSettings.get_solo()

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return make_cached_response(serializer.data, request)


class HomeOverviewAPIView(APIView):
    """
    Aggregated single-call endpoint for the Next.js Homepage.
    Reduces latency by bundling hero solutions, featured projects, tools, testimonials & FAQs.
    Endpoint: GET /api/cms/home-overview/
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request, *args, **kwargs):
        blocked = check_maintenance()
        if blocked:
            return blocked

        site_settings = SiteSettings.get_solo()
        solutions = Solution.objects.filter(is_active=True).order_by('order', 'created_at')
        projects = Project.objects.filter(is_active=True, featured=True).order_by('order', '-created_at')[:6]
        skills = TechSkill.objects.filter(is_active=True).order_by('category', 'proficiency_order')
        testimonials = Testimonial.objects.filter(is_active=True).order_by('order', '-created_at')
        faqs = FAQ.objects.filter(is_active=True).order_by('order', 'id')
        videos = YouTubeVideo.objects.filter(is_active=True).order_by('order', '-created_at')[:3]
        workflow_steps = WorkflowStep.objects.filter(is_active=True).order_by('order', 'step_num')

        # Group tools by category
        grouped_tools = {}
        for s in skills:
            grouped_tools.setdefault(s.category, []).append({
                'name': s.name,
                'icon': s.icon_url,
            })

        return make_cached_response({
            'site': SiteSettingsSerializer(site_settings).data,
            'solutions': SolutionSerializer(solutions, many=True).data,
            'featured_projects': ProjectListSerializer(projects, many=True).data,
            'workflow_steps': WorkflowStepSerializer(workflow_steps, many=True).data,
            'tools': grouped_tools,
            'testimonials': TestimonialSerializer(testimonials, many=True).data,
            'faqs': FAQSerializer(faqs, many=True).data,
            'videos': YouTubeVideoSerializer(videos, many=True).data,
        }, request)


class SolutionListAPIView(generics.ListAPIView):
    """
    List all active services / architecture solutions.
    Endpoint: GET /api/cms/solutions/
    """
    serializer_class = SolutionSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None
    queryset = Solution.objects.filter(is_active=True).order_by('order', 'created_at')

    def list(self, request, *args, **kwargs):
        blocked = check_maintenance('projects')
        if blocked:
            return blocked
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return make_cached_response(serializer.data, request)


class ProjectListAPIView(generics.ListAPIView):
    """
    List portfolio projects with optional ?category= and ?search= filtering.
    Endpoint: GET /api/cms/projects/
    """
    serializer_class = ProjectListSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def list(self, request, *args, **kwargs):
        blocked = check_maintenance('projects')
        if blocked:
            return blocked
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return make_cached_response(serializer.data, request)

    def get_queryset(self):
        qs = Project.objects.filter(is_active=True).order_by('order', '-created_at')
        category = self.request.query_params.get('category', '').strip()
        search = self.request.query_params.get('search', '').strip()
        featured = self.request.query_params.get('featured', '').strip().lower()

        if category and category.lower() != 'all':
            qs = qs.filter(category__iexact=category)
        if search:
            qs = qs.filter(
                Q(title__icontains=search) |
                Q(subtitle__icontains=search) |
                Q(description__icontains=search)
            )
        if featured in ('1', 'true', 'yes'):
            qs = qs.filter(featured=True)
        return qs


class ProjectDetailAPIView(generics.RetrieveAPIView):
    """
    Detailed project case study by slug with full overview, challenge, solution & structured sections.
    Endpoint: GET /api/cms/projects/<slug>/
    """
    serializer_class = ProjectDetailSerializer
    permission_classes = [permissions.AllowAny]
    queryset = Project.objects.filter(is_active=True).prefetch_related('sections')
    lookup_field = 'slug'

    def retrieve(self, request, *args, **kwargs):
        blocked = check_maintenance('projects')
        if blocked:
            return blocked
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return make_cached_response(serializer.data, request)


class TechSkillListAPIView(generics.ListAPIView):
    """
    List developer tech stack skills & tools. If ?grouped=1, returns dictionary grouped by category.
    Endpoint: GET /api/cms/skills/
    """
    serializer_class = TechSkillSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def get_queryset(self):
        qs = TechSkill.objects.filter(is_active=True).order_by('category', 'proficiency_order')
        category = self.request.query_params.get('category', '').strip()
        if category:
            qs = qs.filter(category=category)
        return qs

    def list(self, request, *args, **kwargs):
        blocked = check_maintenance('skills')
        if blocked:
            return blocked
        grouped = request.query_params.get('grouped', '').strip().lower() in ('1', 'true', 'yes')
        if not grouped:
            queryset = self.filter_queryset(self.get_queryset())
            serializer = self.get_serializer(queryset, many=True)
            return make_cached_response(serializer.data, request)

        skills = self.get_queryset()
        data = {}
        for skill in skills:
            data.setdefault(skill.category, []).append({
                'name': skill.name,
                'icon': skill.icon_url,
            })
        return make_cached_response(data, request)


class WorkflowStepListAPIView(generics.ListAPIView):
    """
    List Startup & AI Execution Flow steps shown on Homepage.
    Endpoint: GET /api/cms/workflow-steps/
    """
    serializer_class = WorkflowStepSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None
    queryset = WorkflowStep.objects.filter(is_active=True).order_by('order', 'step_num')

    def list(self, request, *args, **kwargs):
        blocked = check_maintenance('workflow')
        if blocked:
            return blocked
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return make_cached_response(serializer.data, request)


class AboutContentAPIView(APIView):
    """
    Aggregated dynamic content for the About Page (Hero, Studio Profile, Stats, Blocks, Mission, Vision).
    Endpoint: GET /api/cms/about/
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request, *args, **kwargs):
        blocked = check_maintenance('about')
        if blocked:
            return blocked

        profile = AboutProfile.get_solo()
        stats = AboutStat.objects.filter(is_active=True).order_by('order', 'id')
        blocks = AboutBlock.objects.filter(is_active=True).order_by('order', 'created_at')
        skills = TechSkill.objects.filter(is_active=True).order_by('category', 'proficiency_order')

        grouped_tools = {}
        for s in skills:
            grouped_tools.setdefault(s.category, []).append({
                'name': s.name,
                'icon': s.icon_url,
            })

        return make_cached_response({
            'profile': AboutProfileSerializer(profile).data,
            'stats': AboutStatSerializer(stats, many=True).data,
            'blocks': AboutBlockSerializer(blocks, many=True).data,
            'skills': grouped_tools,
        }, request)


class TestimonialListAPIView(generics.ListAPIView):
    """
    List testimonials and client reviews.
    Endpoint: GET /api/cms/testimonials/
    """
    serializer_class = TestimonialSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None
    queryset = Testimonial.objects.filter(is_active=True).order_by('order', '-created_at')

    def list(self, request, *args, **kwargs):
        blocked = check_maintenance('testimonials')
        if blocked:
            return blocked
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return make_cached_response(serializer.data, request)


class FAQListAPIView(generics.ListAPIView):
    """
    List FAQs with optional category filter (?category=...).
    Endpoint: GET /api/cms/faqs/
    """
    serializer_class = FAQSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def list(self, request, *args, **kwargs):
        blocked = check_maintenance('faqs')
        if blocked:
            return blocked
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return make_cached_response(serializer.data, request)

    def get_queryset(self):
        qs = FAQ.objects.filter(is_active=True).order_by('order', 'id')
        category = self.request.query_params.get('category', '').strip()
        if category and category.lower() != 'all':
            qs = qs.filter(category__iexact=category)
        return qs


class YouTubeVideoListAPIView(generics.ListAPIView):
    """
    List YouTube video tutorials.
    Endpoint: GET /api/cms/videos/
    """
    serializer_class = YouTubeVideoSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None
    queryset = YouTubeVideo.objects.filter(is_active=True).order_by('order', '-created_at')

    def list(self, request, *args, **kwargs):
        blocked = check_maintenance('videos')
        if blocked:
            return blocked
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return make_cached_response(serializer.data, request)


class BlogPostListAPIView(generics.ListAPIView):
    """
    List published blog posts with search and category filtering.
    Endpoint: GET /api/cms/blog/
    """
    serializer_class = BlogPostListSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def list(self, request, *args, **kwargs):
        blocked = check_maintenance('blog')
        if blocked:
            return blocked
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return make_cached_response(serializer.data, request)

    def get_queryset(self):
        qs = BlogPost.objects.filter(is_published=True).order_by('-published_at', '-created_at')
        category = self.request.query_params.get('category', '').strip()
        search = self.request.query_params.get('search', '').strip()

        if category and category.lower() != 'all':
            qs = qs.filter(category__iexact=category)
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(content__icontains=search))
        return qs


class BlogPostDetailAPIView(generics.RetrieveAPIView):
    """
    Retrieve single blog article with full Markdown content. Increments views_count automatically.
    Endpoint: GET /api/cms/blog/<slug>/
    """
    serializer_class = BlogPostDetailSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = 'slug'

    def get_queryset(self):
        return BlogPost.objects.filter(is_published=True)

    def retrieve(self, request, *args, **kwargs):
        blocked = check_maintenance('blog')
        if blocked:
            return blocked
        instance = self.get_object()
        # Atomic view count increment
        BlogPost.objects.filter(pk=instance.pk).update(views_count=F('views_count') + 1)
        instance.refresh_from_db(fields=['views_count'])
        serializer = self.get_serializer(instance)
        return Response(serializer.data)


class BlogPostLikeAPIView(APIView):
    """
    Like a blog post (increments likes_count).
    Endpoint: POST /api/cms/blog/<slug>/like/
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request, slug, *args, **kwargs):
        blocked = check_maintenance('blog')
        if blocked:
            return blocked

        try:
            post = BlogPost.objects.get(slug=slug, is_published=True)
            BlogPost.objects.filter(pk=post.pk).update(likes_count=F('likes_count') + 1)
            post.refresh_from_db(fields=['likes_count'])
            return Response({'status': 'ok', 'likes_count': post.likes_count})
        except BlogPost.DoesNotExist:
            return Response({'error': 'Article not found.'}, status=status.HTTP_404_NOT_FOUND)


def get_visitor_info(request) -> tuple[str, str]:
    """
    Extract salted SHA256 hash of visitor IP for rate-limiting and anonymous like/comment tracking.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', '127.0.0.1')

    salt = "njtechstudio_cms_engagement_salt"
    h = hashlib.sha256(f"{salt}:{ip}".encode('utf-8')).hexdigest()
    visitor_name = f"Guest-{h[:6].upper()}"
    return h, visitor_name


class BlogPostEngagementAPIView(APIView):
    """
    Handles persistent likes and comments for blog posts in the Django database.
    Endpoints:
      GET  /api/cms/blog/<slug>/engagement/
      POST /api/cms/blog/<slug>/engagement/
    """
    permission_classes = [permissions.AllowAny]

    def get_post(self, slug: str) -> BlogPost | None:
        return BlogPost.objects.filter(slug=slug, is_published=True).first() or \
               BlogPost.objects.filter(pk=int(slug) if slug.isdigit() else -1, is_published=True).first()

    def get(self, request, slug, *args, **kwargs):
        blocked = check_maintenance('blog')
        if blocked:
            return blocked

        post = self.get_post(slug)
        if not post:
            return Response({'error': 'Article not found.'}, status=status.HTTP_404_NOT_FOUND)

        visitor_hash, visitor_name = get_visitor_info(request)
        likes_count = BlogLike.objects.filter(post=post).count()
        liked = BlogLike.objects.filter(post=post, author_hash=visitor_hash).exists()
        comments = BlogComment.objects.filter(post=post, is_approved=True).order_by('created_at')

        return Response({
            'likes': likes_count,
            'liked': liked,
            'comments': BlogCommentSerializer(comments, many=True).data,
            'me': visitor_name,
        })

    def post(self, request, slug, *args, **kwargs):
        blocked = check_maintenance('blog')
        if blocked:
            return blocked

        post = self.get_post(slug)
        if not post:
            return Response({'error': 'Article not found.'}, status=status.HTTP_404_NOT_FOUND)

        visitor_hash, visitor_name = get_visitor_info(request)
        action = request.data.get('action')

        if action == 'like':
            existing = BlogLike.objects.filter(post=post, author_hash=visitor_hash).first()
            if existing:
                existing.delete()
                liked = False
            else:
                BlogLike.objects.create(post=post, author_hash=visitor_hash)
                liked = True

            total_likes = BlogLike.objects.filter(post=post).count()
            BlogPost.objects.filter(pk=post.pk).update(likes_count=total_likes)
            comments = BlogComment.objects.filter(post=post, is_approved=True).order_by('created_at')

            return Response({
                'likes': total_likes,
                'liked': liked,
                'comments': BlogCommentSerializer(comments, many=True).data,
                'me': visitor_name,
            })

        if action == 'comment':
            text = (request.data.get('text') or '').strip()
            if not text or len(text) > 1000:
                return Response(
                    {'error': 'Comment must be between 1 and 1000 characters.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # 15s cooldown check per visitor hash
            cooldown_time = timezone.now() - timedelta(seconds=15)
            recent_comment = BlogComment.objects.filter(
                post=post,
                author_hash=visitor_hash,
                created_at__gte=cooldown_time
            ).exists()
            if recent_comment:
                return Response(
                    {'error': 'Please wait a few seconds before commenting again.'},
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )

            author = request.data.get('author') or visitor_name
            BlogComment.objects.create(
                post=post,
                author_name=author,
                author_hash=visitor_hash,
                text=text,
                is_approved=True,
            )

            total_likes = BlogLike.objects.filter(post=post).count()
            liked = BlogLike.objects.filter(post=post, author_hash=visitor_hash).exists()
            comments = BlogComment.objects.filter(post=post, is_approved=True).order_by('created_at')

            return Response({
                'likes': total_likes,
                'liked': liked,
                'comments': BlogCommentSerializer(comments, many=True).data,
                'me': visitor_name,
            })

        return Response({'error': 'Invalid action.'}, status=status.HTTP_400_BAD_REQUEST)
