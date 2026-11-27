"""Signal bridges connecting core GlobalNotification with other installed apps."""

from django.dispatch import receiver

from core import notifications
from hello_guy_ai.models import Notification as AINotification
from hello_guy_ai.services.reminders import notification_created


@receiver(notification_created, sender=AINotification, dispatch_uid="core_bridge_ai_notification")
def bridge_ai_notification(sender, notification: AINotification, **kwargs) -> None:
    """Mirror AI reminder/assistant alerts into the Global Notification Bell."""
    notifications.notify(
        user=notification.user,
        title=notification.title,
        body=notification.body,
        url=notification.url or "/ai/chat/",
        category="ai",
        level="info",
        metadata={"ai_notification_id": notification.pk},
    )
