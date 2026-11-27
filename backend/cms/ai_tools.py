"""Read and content tools for the website CMS (autodiscovered by hello_guy_ai)."""

from typing import Any, Literal

from django.contrib.auth import get_permission_codename
from django.db.models import Model, Q
from django.utils.text import slugify

from cms.models import FAQ, BlogPost, Project, Solution, TechSkill, Testimonial, YouTubeVideo
from crm.models import Opportunity
from hello_guy_ai import ToolError, current_context, tool

MAX_LIMIT = 50


def _require(action: str, model: type[Model]) -> None:
    user = current_context().user
    if not user.has_perm(f"{model._meta.app_label}.{get_permission_codename(action, model._meta)}"):
        raise ToolError(f"You don't have permission to {action} {model._meta.verbose_name_plural}.")


def _require_view(model: type[Model]) -> None:
    _require("view", model)


def _visible(qs):
    """Non-staff users (e.g. website visitors) only ever see published / active rows."""
    if current_context().user.is_staff:
        return qs
    fields = {f.name for f in qs.model._meta.fields}
    if "is_published" in fields:
        return qs.filter(is_published=True)
    return qs.filter(is_active=True) if "is_active" in fields else qs


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
    }
    for model in (BlogPost, Project, FAQ, Testimonial, Solution, TechSkill, YouTubeVideo):
        if user.has_perm(f"cms.{get_permission_codename('view', model._meta)}"):
            qs = _visible(model.objects.all())
            summary[str(model._meta.verbose_name_plural)] = counts[model](qs) if model in counts else qs.count()
    if user.has_perm("crm.view_opportunity"):
        summary["inquiries"] = {
            "total": Opportunity.objects.count(),
            "unreplied": Opportunity.objects.filter(status=Opportunity.Status.NEW).count(),
        }
    if not summary:
        raise ToolError("You don't have permission to view website content.")
    return summary


@tool
def search_blog_posts(query: str = "", category: str = "", published_only: bool = False, limit: int = 10) -> list[dict]:
    """List blog posts, newest first. query matches title or content; category e.g. Tutorial, Case Study.
    Returns title, slug, category, status, dates, views and likes (no body; use get_blog_post for that)."""
    _require_view(BlogPost)
    qs = _visible(BlogPost.objects.order_by("-published_at", "-created_at"))
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
    post = _visible(BlogPost.objects.filter(slug=slug)).first()
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
    qs = _visible(Project.objects.order_by("order", "-created_at"))
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
    qs = _visible(FAQ.objects.order_by("order"))
    if query:
        qs = qs.filter(Q(question__icontains=query) | Q(answer__icontains=query))
    return list(qs.values("question", "answer", "category", "is_active")[: _limit(limit)])


@tool
def top_blog_posts(metric: Literal["views", "likes"] = "views", limit: int = 5) -> list[dict]:
    """Rank blog posts by views or likes, highest first. Use for "most popular" / "best performing" questions."""
    _require_view(BlogPost)
    field = "views_count" if metric == "views" else "likes_count"
    return list(
        _visible(BlogPost.objects.order_by(f"-{field}")).values("title", "slug", "category", "is_published", "views_count", "likes_count")[
            : _limit(limit)
        ]
    )


def _confirm_draft(args: dict) -> str:
    preview = args.get("content", "")[:600]
    return f"Create a DRAFT blog post (not published)\nTitle: {args['title']}\nCategory: {args.get('category')}\n\n{preview}"


@tool(requires_confirmation=True, confirm_prompt=_confirm_draft)
def create_blog_draft(title: str, content: str, category: str = "Tutorial") -> dict:
    """Save a new blog post as an unpublished DRAFT. content is the full article in Markdown.
    Needs the user's approval. The user publishes it later from the admin."""
    _require("add", BlogPost)
    if not title.strip() or not content.strip():
        raise ToolError("title and content are required.")
    base = slugify(title)[:200] or "draft"
    slug, n = base, 2
    while BlogPost.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    post = BlogPost.objects.create(
        title=title.strip()[:200], slug=slug, content=content, category=category.strip()[:60] or "Tutorial", is_published=False
    )
    return {"slug": post.slug, "title": post.title, "is_published": False, "admin_url": f"/admin/cms/blogpost/{post.pk}/change/"}


@tool
def list_tech_skills() -> dict[str, list[str]]:
    """Technologies and skills the studio works with, grouped by category. Use it for "do you know X?"
    and "which technologies do you use?" questions."""
    _require_view(TechSkill)
    grouped: dict[str, list[str]] = {}
    for name, category in TechSkill.objects.filter(is_active=True).values_list("name", "category"):
        grouped.setdefault(category, []).append(name)
    return grouped


@tool
def list_services() -> list[dict]:
    """List the services / solutions the studio offers, with tech stack. Use for "what do you do / offer" questions."""
    _require_view(Solution)
    return list(_visible(Solution.objects.order_by("order")).values("title", "tag", "description", "tech_stack", "badge", "slug"))
