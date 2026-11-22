"""Read tools for the AI assistant over the website CMS (autodiscovered by hello_guy_ai).

One explicit tool per question, like django-ai-assistant's @method_tool: the LLM gets the
answer in ONE call instead of the generic list -> describe -> read chain (fewer LLM round
trips = fewer rate-limit hits). Every tool checks the requesting user's Django view perm.
"""

from typing import Any

from django.contrib.auth import get_permission_codename
from django.db.models import Model, Q

from cms.models import FAQ, BlogPost, InquirySubmission, Project, Solution, TechSkill, Testimonial, YouTubeVideo
from hello_guy_ai.runtime import ToolError, current_context, tool

MAX_LIMIT = 50


def _require_view(model: type[Model]) -> None:
    user = current_context().user
    if not user.has_perm(f"{model._meta.app_label}.{get_permission_codename('view', model._meta)}"):
        raise ToolError(f"You don't have permission to view {model._meta.verbose_name_plural}.")


def _limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


@tool
def site_content_summary() -> dict[str, Any]:
    """Count everything on the website: blog posts (published/drafts), projects, FAQs, testimonials,
    solutions, skills, videos and inquiries (total/unreplied). Use for any "how many ..." question."""
    user = current_context().user
    summary: dict[str, Any] = {}
    counts = {
        BlogPost: lambda qs: {"published": qs.filter(is_published=True).count(), "drafts": qs.filter(is_published=False).count()},
        InquirySubmission: lambda qs: {"total": qs.count(), "unreplied": qs.filter(is_replied=False).count()},
    }
    for model in (BlogPost, Project, FAQ, Testimonial, Solution, TechSkill, YouTubeVideo, InquirySubmission):
        if user.has_perm(f"cms.{get_permission_codename('view', model._meta)}"):
            qs = model.objects.all()
            summary[str(model._meta.verbose_name_plural)] = counts[model](qs) if model in counts else qs.count()
    if not summary:
        raise ToolError("You don't have permission to view website content.")
    return summary


@tool
def search_blog_posts(query: str = "", category: str = "", published_only: bool = False, limit: int = 10) -> list[dict]:
    """List blog posts, newest first. query matches title or content; category e.g. Tutorial, Case Study.
    Returns title, slug, category, status, dates, views and likes (no body; use get_blog_post for that)."""
    _require_view(BlogPost)
    qs = BlogPost.objects.order_by("-published_at", "-created_at")
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(content__icontains=query))
    if category:
        qs = qs.filter(category__iexact=category)
    if published_only:
        qs = qs.filter(is_published=True)
    return list(
        qs.values(
            "title", "slug", "category", "is_published", "published_at", "created_at", "views_count", "likes_count"
        )[: _limit(limit)]
    )


@tool
def get_blog_post(slug: str) -> dict:
    """Get one blog post with its full Markdown content and comment count, by slug."""
    _require_view(BlogPost)
    post = BlogPost.objects.filter(slug=slug).first()
    if post is None:
        raise ToolError(f"No blog post with slug {slug!r}. Use search_blog_posts to find the slug.")
    return {
        "title": post.title,
        "slug": post.slug,
        "category": post.category,
        "is_published": post.is_published,
        "published_at": post.published_at,
        "views": post.views_count,
        "likes": post.likes_count,
        "comments": post.comments.count(),
        "content": post.content,
    }


@tool
def search_projects(query: str = "", featured_only: bool = False, limit: int = 10) -> list[dict]:
    """List portfolio projects in display order. query matches title, subtitle, description or client."""
    _require_view(Project)
    qs = Project.objects.order_by("order", "-created_at")
    if query:
        qs = qs.filter(
            Q(title__icontains=query) | Q(subtitle__icontains=query) | Q(description__icontains=query) | Q(client__icontains=query)
        )
    if featured_only:
        qs = qs.filter(featured=True)
    return list(
        qs.values(
            "title", "slug", "subtitle", "category", "client", "status", "technologies", "featured", "is_active", "live_url"
        )[: _limit(limit)]
    )


@tool
def search_faqs(query: str = "", limit: int = 20) -> list[dict]:
    """List FAQs (question and answer) in display order. query matches question or answer."""
    _require_view(FAQ)
    qs = FAQ.objects.order_by("order")
    if query:
        qs = qs.filter(Q(question__icontains=query) | Q(answer__icontains=query))
    return list(qs.values("question", "answer", "category", "is_active")[: _limit(limit)])


@tool
def list_inquiries(unreplied_only: bool = False, query: str = "", limit: int = 10) -> list[dict]:
    """List contact/project inquiries from website visitors, newest first. query matches name, email,
    subject or message. Contains personal data: only share it with the user who asked."""
    _require_view(InquirySubmission)
    qs = InquirySubmission.objects.order_by("-created_at")
    if unreplied_only:
        qs = qs.filter(is_replied=False)
    if query:
        qs = qs.filter(
            Q(name__icontains=query) | Q(email__icontains=query) | Q(subject__icontains=query) | Q(message__icontains=query)
        )
    return list(
        qs.values(
            "inquiry_id", "name", "email", "phone", "subject", "message", "inquiry_type", "is_replied", "created_at"
        )[: _limit(limit)]
    )
