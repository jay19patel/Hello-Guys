from django.urls import path
from cms.views_ui import (
    CMSOverviewView,
    # Inquiries
    InquiryListView,
    InquiryCreateView,
    InquiryDetailView,
    InquiryUpdateView,
    InquiryDeleteView,
    InquiryToggleRepliedView,
    # Projects
    ProjectListView,
    ProjectCreateView,
    ProjectUpdateView,
    ProjectDeleteView,
    # Solutions
    SolutionListView,
    SolutionCreateView,
    SolutionUpdateView,
    SolutionDeleteView,
    # Blogs
    BlogListView,
    BlogCreateView,
    BlogUpdateView,
    BlogDeleteView,
    BlogCommentDeleteView,
    # Settings
    SiteSettingsUpdateView,
    # Extras
    TestimonialListView,
    TestimonialCreateView,
    TestimonialUpdateView,
    TestimonialDeleteView,
    FAQListView,
    FAQCreateView,
    FAQUpdateView,
    FAQDeleteView,
    TechSkillListView,
    TechSkillCreateView,
    TechSkillUpdateView,
    TechSkillDeleteView,
)

app_name = 'cms_ui'

urlpatterns = [
    # CMS Overview
    path('', CMSOverviewView.as_view(), name='overview'),

    # Inquiries / Contacts
    path('inquiries/', InquiryListView.as_view(), name='inquiry-list'),
    path('inquiries/new/', InquiryCreateView.as_view(), name='inquiry-create'),
    path('inquiries/<int:pk>/', InquiryDetailView.as_view(), name='inquiry-detail'),
    path('inquiries/<int:pk>/edit/', InquiryUpdateView.as_view(), name='inquiry-update'),
    path('inquiries/<int:pk>/delete/', InquiryDeleteView.as_view(), name='inquiry-delete'),
    path('inquiries/<int:pk>/toggle-replied/', InquiryToggleRepliedView.as_view(), name='inquiry-toggle-replied'),

    # Projects
    path('projects/', ProjectListView.as_view(), name='project-list'),
    path('projects/new/', ProjectCreateView.as_view(), name='project-create'),
    path('projects/<int:pk>/edit/', ProjectUpdateView.as_view(), name='project-update'),
    path('projects/<int:pk>/delete/', ProjectDeleteView.as_view(), name='project-delete'),

    # Solutions
    path('solutions/', SolutionListView.as_view(), name='solution-list'),
    path('solutions/new/', SolutionCreateView.as_view(), name='solution-create'),
    path('solutions/<int:pk>/edit/', SolutionUpdateView.as_view(), name='solution-update'),
    path('solutions/<int:pk>/delete/', SolutionDeleteView.as_view(), name='solution-delete'),

    # Blogs
    path('blogs/', BlogListView.as_view(), name='blog-list'),
    path('blogs/new/', BlogCreateView.as_view(), name='blog-create'),
    path('blogs/<int:pk>/edit/', BlogUpdateView.as_view(), name='blog-update'),
    path('blogs/<int:pk>/delete/', BlogDeleteView.as_view(), name='blog-delete'),
    path('blogs/comments/<int:pk>/delete/', BlogCommentDeleteView.as_view(), name='blog-comment-delete'),

    # Site Settings
    path('settings/', SiteSettingsUpdateView.as_view(), name='site-settings'),

    # Testimonials
    path('testimonials/', TestimonialListView.as_view(), name='testimonial-list'),
    path('testimonials/new/', TestimonialCreateView.as_view(), name='testimonial-create'),
    path('testimonials/<int:pk>/edit/', TestimonialUpdateView.as_view(), name='testimonial-update'),
    path('testimonials/<int:pk>/delete/', TestimonialDeleteView.as_view(), name='testimonial-delete'),

    # FAQs
    path('faqs/', FAQListView.as_view(), name='faq-list'),
    path('faqs/new/', FAQCreateView.as_view(), name='faq-create'),
    path('faqs/<int:pk>/edit/', FAQUpdateView.as_view(), name='faq-update'),
    path('faqs/<int:pk>/delete/', FAQDeleteView.as_view(), name='faq-delete'),

    # Skills
    path('skills/', TechSkillListView.as_view(), name='skill-list'),
    path('skills/new/', TechSkillCreateView.as_view(), name='skill-create'),
    path('skills/<int:pk>/edit/', TechSkillUpdateView.as_view(), name='skill-update'),
    path('skills/<int:pk>/delete/', TechSkillDeleteView.as_view(), name='skill-delete'),
]

