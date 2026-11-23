import logging

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from hello_guy_ai.conf import ai_settings
from hello_guy_ai.models import Document, Notification
from hello_guy_ai.services.reminders import notification_created

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=Document)
def cleanup_document(sender, instance: Document, **kwargs) -> None:
    def _cleanup() -> None:
        from hello_guy_ai.tasks.actors import delete_document_vectors

        delete_document_vectors.send(instance.pk)
        instance.file.delete(save=False)

    transaction.on_commit(_cleanup)


@receiver(notification_created, sender=Notification)
def email_notification(sender, notification: Notification, **kwargs) -> None:
    if ai_settings.NOTIFY_EMAIL and notification.user.email:
        from hello_guy_ai.tasks.actors import send_notification_email

        try:
            send_notification_email.send(notification.pk)
        except Exception:  # broker down: the in-app notification already exists
            logger.exception("Could not enqueue email for notification %s", notification.pk)
