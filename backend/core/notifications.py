"""Simple, reusable global notification API for any app in the project.

Usage from any app (CRM, Meta, AI, CMS, Voice, etc.):

    from core.notifications import notify, notify_staff

    notify(
        user=request.user,
        title="Meeting rescheduled",
        body="Client accepted 4:00 PM slot.",
        url="/crm/meetings/12/",
        category="meeting",
        level="success",
    )

    notify_staff(
        title="New WhatsApp inquiry",
        body="Acme Corp requested a quote.",
        url="/crm/opportunities/5/",
        category="crm",
    )
"""

from collections.abc import Iterable
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction
from django.dispatch import Signal
from django.utils import timezone

from core.models import GlobalNotification
from core.safety import safe_url

# Fired after commit with `notification` (a GlobalNotification) or `notifications` (list).
global_notification_created = Signal()


def notify(
    user: Any,
    title: str,
    *,
    body: str = "",
    url: str = "",
    category: str = "system",
    level: str = GlobalNotification.Level.INFO,
    metadata: dict[str, Any] | None = None,
) -> GlobalNotification:
    """Push a single notification to a user. Safe to call inside or outside transactions."""
    if level not in GlobalNotification.Level.values:
        level = GlobalNotification.Level.INFO
    notif = GlobalNotification.objects.create(
        user=user,
        title=(title or "Notification")[:200],
        body=(body or "").strip(),
        url=safe_url(url)[:500],
        category=(category or "system")[:40],
        level=level,
        metadata=metadata or {},
    )
    transaction.on_commit(lambda: global_notification_created.send(sender=GlobalNotification, notification=notif))
    return notif


def notify_many(
    users: Iterable[Any],
    title: str,
    *,
    body: str = "",
    url: str = "",
    category: str = "system",
    level: str = GlobalNotification.Level.INFO,
    metadata: dict[str, Any] | None = None,
) -> list[GlobalNotification]:
    """Push the same notification to multiple users in one bulk insert."""
    if level not in GlobalNotification.Level.values:
        level = GlobalNotification.Level.INFO
    user_list = [u for u in users if getattr(u, "is_active", True)]
    if not user_list:
        return []
    items = [
        GlobalNotification(
            user=u,
            title=(title or "Notification")[:200],
            body=(body or "").strip(),
            url=safe_url(url)[:500],
            category=(category or "system")[:40],
            level=level,
            metadata=metadata or {},
        )
        for u in user_list
    ]
    created = GlobalNotification.objects.bulk_create(items)
    transaction.on_commit(lambda: global_notification_created.send(sender=GlobalNotification, notifications=created))
    return created


def notify_staff(
    title: str,
    *,
    body: str = "",
    url: str = "",
    category: str = "system",
    level: str = GlobalNotification.Level.INFO,
    metadata: dict[str, Any] | None = None,
) -> list[GlobalNotification]:
    """Push a notification to all active staff users."""
    User = get_user_model()
    staff = User.objects.filter(is_active=True, is_staff=True)
    return notify_many(
        staff,
        title,
        body=body,
        url=url,
        category=category,
        level=level,
        metadata=metadata,
    )


def notifications_for(user: Any, *, unread_only: bool = False, category: str | None = None, limit: int = 30):
    """Query notifications for a user, newest first."""
    qs = GlobalNotification.objects.filter(user=user)
    if unread_only:
        qs = qs.filter(is_read=False)
    if category:
        qs = qs.filter(category=category)
    return qs[: max(1, min(limit, 100))]


def unread_count(user: Any) -> int:
    """Count unread global notifications for a user."""
    if not getattr(user, "is_authenticated", False):
        return 0
    return GlobalNotification.objects.filter(user=user, is_read=False).count()


def mark_read(user: Any, ids: list[int] | None = None) -> int:
    """Mark a user's notifications as read (all unread when ids is None). Returns count updated."""
    qs = GlobalNotification.objects.filter(user=user, is_read=False)
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    return qs.update(is_read=True, read_at=timezone.now())
