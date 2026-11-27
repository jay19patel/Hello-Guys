import logging

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from hello_guy_ai.models import Document

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=Document)
def cleanup_document(sender, instance: Document, **kwargs) -> None:
    def _cleanup() -> None:
        from hello_guy_ai.tasks.actors import delete_document_vectors

        delete_document_vectors.send(instance.pk)
        instance.file.delete(save=False)

    transaction.on_commit(_cleanup)
