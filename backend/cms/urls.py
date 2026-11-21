from django.urls import path
from cms.views import (
    SiteConfigAPIView,
    HomeOverviewAPIView,
    SolutionListAPIView,
    ProjectListAPIView,
    ProjectDetailAPIView,
    TechSkillListAPIView,
    WorkflowStepListAPIView,
    AboutContentAPIView,
    TestimonialListAPIView,
    FAQListAPIView,
    YouTubeVideoListAPIView,
    BlogPostListAPIView,
    BlogPostDetailAPIView,
    BlogPostLikeAPIView,
    BlogPostEngagementAPIView,
    InquiryCreateAPIView,
)

app_name = 'cms'

urlpatterns = [
    # Site settings & Homepage overview
    path('site-config/', SiteConfigAPIView.as_view(), name='site-config'),
    path('home-overview/', HomeOverviewAPIView.as_view(), name='home-overview'),

    # Solutions / Core Highlights
    path('solutions/', SolutionListAPIView.as_view(), name='solution-list'),

    # Projects / Case Studies
    path('projects/', ProjectListAPIView.as_view(), name='project-list'),
    path('projects/<slug:slug>/', ProjectDetailAPIView.as_view(), name='project-detail'),

    # Tech Stack & Skills
    path('skills/', TechSkillListAPIView.as_view(), name='skill-list'),

    # Startup & AI Execution Flow
    path('workflow-steps/', WorkflowStepListAPIView.as_view(), name='workflow-step-list'),

    # About Page Dynamic Content
    path('about/', AboutContentAPIView.as_view(), name='about-content'),

    # Testimonials & FAQs
    path('testimonials/', TestimonialListAPIView.as_view(), name='testimonial-list'),
    path('faqs/', FAQListAPIView.as_view(), name='faq-list'),

    # YouTube Videos
    path('videos/', YouTubeVideoListAPIView.as_view(), name='video-list'),

    # Blog / Articles
    path('blog/', BlogPostListAPIView.as_view(), name='blog-list'),
    path('blog/<slug:slug>/', BlogPostDetailAPIView.as_view(), name='blog-detail'),
    path('blog/<slug:slug>/like/', BlogPostLikeAPIView.as_view(), name='blog-like'),
    path('blog/<slug:slug>/engagement/', BlogPostEngagementAPIView.as_view(), name='blog-engagement'),

    # Inquiries / Contact Form Leads
    path('inquiries/', InquiryCreateAPIView.as_view(), name='inquiry-create'),
]

