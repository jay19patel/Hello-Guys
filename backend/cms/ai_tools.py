"""Read tools for the AI assistant over the website CMS (autodiscovered by hello_guy_ai).

One explicit tool per question, like django-ai-assistant's @method_tool: the LLM gets the
answer in ONE call instead of the generic list -> describe -> read chain (fewer LLM round
trips = fewer rate-limit hits). Every tool checks the requesting user's Django view perm.
"""

from typing import Any, Literal

from django.contrib.auth import get_permission_codename
from django.db.models import Model, Q
from django.utils import timezone
from django.utils.text import slugify

from django.core.validators import validate_email

from cms.models import FAQ, BlogPost, InquirySubmission, Project, Solution, TechSkill, Testimonial, YouTubeVideo
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
        InquirySubmission: lambda qs: {"total": qs.count(), "unreplied": qs.filter(is_replied=False).count()},
    }
    for model in (BlogPost, Project, FAQ, Testimonial, Solution, TechSkill, YouTubeVideo, InquirySubmission):
        if user.has_perm(f"cms.{get_permission_codename('view', model._meta)}"):
            qs = _visible(model.objects.all())
            summary[str(model._meta.verbose_name_plural)] = counts[model](qs) if model in counts else qs.count()
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


@tool
def get_inquiry(inquiry_id: str) -> dict:
    """Get one visitor inquiry with its full message and reply history, by inquiry_id (e.g. sub_74658245fdf5)."""
    _require_view(InquirySubmission)
    row = InquirySubmission.objects.filter(inquiry_id=inquiry_id).values().first()
    if row is None:
        raise ToolError(f"No inquiry {inquiry_id!r}. Use list_inquiries to find the id.")
    return row


def _confirm_replied(args: dict) -> str:
    note = f"\nNote: {args['note']}" if args.get("note") else ""
    return f"Mark inquiry {args['inquiry_id']} as replied.{note}"


@tool(requires_confirmation=True, confirm_prompt=_confirm_replied)
def mark_inquiry_replied(inquiry_id: str, note: str = "") -> dict:
    """Mark a visitor inquiry as replied and log an optional note. Needs the user's approval."""
    _require("change", InquirySubmission)
    inquiry = InquirySubmission.objects.filter(inquiry_id=inquiry_id).first()
    if inquiry is None:
        raise ToolError(f"No inquiry {inquiry_id!r}.")
    inquiry.is_replied = True
    inquiry.replies = [
        *(inquiry.replies or []),
        {"type": "marked_replied", "note": note[:1000], "by": current_context().user.get_username(), "at": timezone.now().isoformat()},
    ]
    inquiry.save(update_fields=["is_replied", "replies", "updated_at"])
    return {"inquiry_id": inquiry_id, "is_replied": True}


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


@tool
def create_inquiry(name: str, email: str, message: str, phone: str = "", subject: str = "") -> dict:
    """Submit the visitor's project inquiry / contact request so the team can reply by email.
    Only call it after the visitor has given their name, a valid email and what they need,
    and has confirmed the details. Returns the inquiry id to share with the visitor."""
    from cms.views import check_maintenance  # same switch as the website's contact form

    _require("add", InquirySubmission)
    ctx = current_context()
    from hello_guy_ai.models import Message

    if ctx.run_id and Message.objects.filter(run_id=ctx.run_id, name="create_inquiry").exists():
        raise ToolError("An inquiry was already submitted in this reply. Do not submit it again.")
    if check_maintenance("inquiries") is not None:
        raise ToolError("Inquiries are temporarily closed. Ask the visitor to try again later.")
    name, email, message = name.strip(), email.strip(), message.strip()
    if len(name) < 2:
        raise ToolError("Ask the visitor for their name.")
    try:
        validate_email(email)
    except Exception:
        raise ToolError("That email address looks invalid. Ask the visitor to check it.") from None
    if len(message) < 5:
        raise ToolError("Ask the visitor to describe what they need.")
    inquiry = InquirySubmission.objects.create(
        name=name[:150],
        email=email,
        phone=phone.strip()[:50] or "Not provided",
        subject=subject.strip()[:255] or "Website chat inquiry",
        message=message[:5000],
        inquiry_type="AI Chat Assistant",
    )
    return {"inquiry_id": inquiry.inquiry_id, "status": "received"}
