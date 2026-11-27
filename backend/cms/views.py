from datetime import timedelta
from typing import Any
import hashlib
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.db.models import F, Q
from rest_framework import generics, permissions, status
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle, ScopedRateThrottle
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
    etag = f'W/"{hashlib.sha256(rendered).hexdigest()[:32]}"'
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


def _grouped_skills(skills_qs) -> dict[str, list[dict[str, str]]]:
    """Group active TechSkill rows by category for API responses."""
    grouped: dict[str, list[dict[str, str]]] = {}
    for s in skills_qs:
        grouped.setdefault(s.category, []).append({'name': s.name, 'icon': s.icon_url})
    return grouped


class CachedCMSListView(generics.ListAPIView):
    """Public unpaginated CMS list endpoint with maintenance gating and ETag caching."""
    permission_classes = [permissions.AllowAny]
    pagination_class = None
    maintenance_key: str | None = None

    def list(self, request, *args, **kwargs):
        if blocked := check_maintenance(self.maintenance_key):
            return blocked
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return make_cached_response(serializer.data, request)


class CachedCMSRetrieveView(generics.RetrieveAPIView):
    """Public CMS detail endpoint with maintenance gating and ETag caching."""
    permission_classes = [permissions.AllowAny]
    maintenance_key: str | None = None

    def retrieve(self, request, *args, **kwargs):
        if blocked := check_maintenance(self.maintenance_key):
            return blocked
        serializer = self.get_serializer(self.get_object())
        return make_cached_response(serializer.data, request)


class SiteConfigAPIView(CachedCMSRetrieveView):
    """GET /api/cms/site-config/ - global website branding, profile, contact, and SEO config."""
    serializer_class = SiteSettingsSerializer

    def get_object(self):
        return SiteSettings.get_solo()


class HomeOverviewAPIView(APIView):
    """GET /api/cms/home-overview/ - bundled homepage data."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, *args, **kwargs):
        if blocked := check_maintenance():
            return blocked

        site_settings = SiteSettings.get_solo()
        solutions = Solution.objects.filter(is_active=True).order_by('order', 'created_at')
        projects = Project.objects.filter(is_active=True, featured=True).order_by('order', '-created_at')[:6]
        skills = TechSkill.objects.filter(is_active=True).order_by('category', 'proficiency_order')
        testimonials = Testimonial.objects.filter(is_active=True).order_by('order', '-created_at')
        faqs = FAQ.objects.filter(is_active=True).order_by('order', 'id')
        videos = YouTubeVideo.objects.filter(is_active=True).order_by('order', '-created_at')[:3]
        workflow_steps = WorkflowStep.objects.filter(is_active=True).order_by('order', 'step_num')

        return make_cached_response({
            'site': SiteSettingsSerializer(site_settings).data,
            'solutions': SolutionSerializer(solutions, many=True).data,
            'featured_projects': ProjectListSerializer(projects, many=True).data,
            'workflow_steps': WorkflowStepSerializer(workflow_steps, many=True).data,
            'tools': _grouped_skills(skills),
            'testimonials': TestimonialSerializer(testimonials, many=True).data,
            'faqs': FAQSerializer(faqs, many=True).data,
            'videos': YouTubeVideoSerializer(videos, many=True).data,
        }, request)


class SolutionListAPIView(CachedCMSListView):
    """GET /api/cms/solutions/ - active services / architecture solutions."""
    serializer_class = SolutionSerializer
    maintenance_key = 'projects'
    queryset = Solution.objects.filter(is_active=True).order_by('order', 'created_at')


class ProjectListAPIView(CachedCMSListView):
    """GET /api/cms/projects/ - portfolio projects with ?category=, ?search=, ?featured=."""
    serializer_class = ProjectListSerializer
    maintenance_key = 'projects'

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


class ProjectDetailAPIView(CachedCMSRetrieveView):
    """GET /api/cms/projects/<slug>/ - project case study with structured sections."""
    serializer_class = ProjectDetailSerializer
    maintenance_key = 'projects'
    queryset = Project.objects.filter(is_active=True).prefetch_related('sections')
    lookup_field = 'slug'


class TechSkillListAPIView(CachedCMSListView):
    """GET /api/cms/skills/ - tech stack skills (?grouped=1 returns dict by category)."""
    serializer_class = TechSkillSerializer
    maintenance_key = 'skills'

    def get_queryset(self):
        qs = TechSkill.objects.filter(is_active=True).order_by('category', 'proficiency_order')
        category = self.request.query_params.get('category', '').strip()
        return qs.filter(category=category) if category else qs

    def list(self, request, *args, **kwargs):
        if request.query_params.get('grouped', '').strip().lower() not in ('1', 'true', 'yes'):
            return super().list(request, *args, **kwargs)
        if blocked := check_maintenance(self.maintenance_key):
            return blocked
        return make_cached_response(_grouped_skills(self.get_queryset()), request)


class WorkflowStepListAPIView(CachedCMSListView):
    """GET /api/cms/workflow-steps/ - Startup & AI Execution Flow steps."""
    serializer_class = WorkflowStepSerializer
    maintenance_key = 'workflow'
    queryset = WorkflowStep.objects.filter(is_active=True).order_by('order', 'step_num')


class AboutContentAPIView(APIView):
    """GET /api/cms/about/ - aggregated dynamic content for the About Page."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, *args, **kwargs):
        if blocked := check_maintenance('about'):
            return blocked

        profile = AboutProfile.get_solo()
        stats = AboutStat.objects.filter(is_active=True).order_by('order', 'id')
        blocks = AboutBlock.objects.filter(is_active=True).order_by('order', 'created_at')
        skills = TechSkill.objects.filter(is_active=True).order_by('category', 'proficiency_order')

        return make_cached_response({
            'profile': AboutProfileSerializer(profile).data,
            'stats': AboutStatSerializer(stats, many=True).data,
            'blocks': AboutBlockSerializer(blocks, many=True).data,
            'skills': _grouped_skills(skills),
        }, request)


class TestimonialListAPIView(CachedCMSListView):
    """GET /api/cms/testimonials/ - client reviews."""
    serializer_class = TestimonialSerializer
    maintenance_key = 'testimonials'
    queryset = Testimonial.objects.filter(is_active=True).order_by('order', '-created_at')


class FAQListAPIView(CachedCMSListView):
    """GET /api/cms/faqs/ - FAQs with optional ?category=."""
    serializer_class = FAQSerializer
    maintenance_key = 'faqs'

    def get_queryset(self):
        qs = FAQ.objects.filter(is_active=True).order_by('order', 'id')
        category = self.request.query_params.get('category', '').strip()
        return qs.filter(category__iexact=category) if category and category.lower() != 'all' else qs


class YouTubeVideoListAPIView(CachedCMSListView):
    """GET /api/cms/videos/ - YouTube video tutorials."""
    serializer_class = YouTubeVideoSerializer
    maintenance_key = 'videos'
    queryset = YouTubeVideo.objects.filter(is_active=True).order_by('order', '-created_at')


class BlogPostListAPIView(CachedCMSListView):
    """GET /api/cms/blog/ - published blog posts with ?category= and ?search=."""
    serializer_class = BlogPostListSerializer
    maintenance_key = 'blog'

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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'engagement'

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
    # DRF's get_ident honors settings.NUM_PROXIES, so a client-sent X-Forwarded-For is not trusted blindly.
    ip = BaseThrottle().get_ident(request)
    h = salted_hmac('cms.engagement', ip, algorithm='sha256').hexdigest()  # keyed with SECRET_KEY
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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'engagement'

    def get_throttles(self):
        # Only writes are limited; page loads (GET) are cheap and frequent.
        return super().get_throttles() if self.request.method == 'POST' else []

    def get_post(self, slug: str) -> BlogPost | None:
        return BlogPost.objects.filter(slug=slug, is_published=True).first() or \
               BlogPost.objects.filter(pk=int(slug) if slug.isdigit() else -1, is_published=True).first()

    def _engagement_response(self, post: BlogPost, visitor_hash: str, visitor_name: str, *, liked: bool | None = None) -> Response:
        likes_count = BlogLike.objects.filter(post=post).count()
        if liked is None:
            liked = BlogLike.objects.filter(post=post, author_hash=visitor_hash).exists()
        comments = BlogComment.objects.filter(post=post, is_approved=True).order_by('created_at')
        return Response({
            'likes': likes_count,
            'liked': liked,
            'comments': BlogCommentSerializer(comments, many=True).data,
            'me': visitor_name,
        })

    def get(self, request, slug, *args, **kwargs):
        if blocked := check_maintenance('blog'):
            return blocked
        post = self.get_post(slug)
        if not post:
            return Response({'error': 'Article not found.'}, status=status.HTTP_404_NOT_FOUND)
        visitor_hash, visitor_name = get_visitor_info(request)
        return self._engagement_response(post, visitor_hash, visitor_name)

    def post(self, request, slug, *args, **kwargs):
        if blocked := check_maintenance('blog'):
            return blocked
        post = self.get_post(slug)
        if not post:
            return Response({'error': 'Article not found.'}, status=status.HTTP_404_NOT_FOUND)

        visitor_hash, visitor_name = get_visitor_info(request)
        action = request.data.get('action')

        if action == 'like':
            like, created = BlogLike.objects.get_or_create(post=post, author_hash=visitor_hash)  # race-safe
            if not created:
                like.delete()
            total_likes = BlogLike.objects.filter(post=post).count()
            BlogPost.objects.filter(pk=post.pk).update(likes_count=total_likes)
            return self._engagement_response(post, visitor_hash, visitor_name, liked=created)

        if action == 'comment':
            text = request.data.get('text')
            text = text.strip() if isinstance(text, str) else ''
            if not text or len(text) > 1000:
                return Response({'error': 'Comment must be between 1 and 1000 characters.'}, status=status.HTTP_400_BAD_REQUEST)

            if BlogComment.objects.filter(
                post=post, author_hash=visitor_hash, created_at__gte=timezone.now() - timedelta(seconds=15)
            ).exists():
                return Response({'error': 'Please wait a few seconds before commenting again.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

            author = request.data.get('author')
            author = author.strip() if isinstance(author, str) else ''
            if len(author) > 100:
                return Response({'error': 'Name must be at most 100 characters.'}, status=status.HTTP_400_BAD_REQUEST)
            BlogComment.objects.create(
                post=post,
                author_name=author or visitor_name,
                author_hash=visitor_hash,
                text=text,
                is_approved=True,
            )
            return self._engagement_response(post, visitor_hash, visitor_name)

        return Response({'error': 'Invalid action.'}, status=status.HTTP_400_BAD_REQUEST)
