"""Re-index knowledge documents.

python manage.py hello_guy_reindex          # full rebuild of the current embedding model's collection
python manage.py hello_guy_reindex --kb faq # re-ingest one knowledge base's documents
"""

from django.core.management.base import BaseCommand

from hello_guy_ai.models import Document
from hello_guy_ai.services.chat import enqueue_ingest


class Command(BaseCommand):
    help = "Rebuild the knowledge index (all documents, or one knowledge base's)"

    def add_arguments(self, parser):
        parser.add_argument("--kb", help="Knowledge base slug")

    def handle(self, *args, kb: str | None = None, **options):
        if not kb:
            from hello_guy_ai.tasks.actors import reindex_knowledge

            reindex_knowledge.send()  # the worker drops the collection and re-ingests everything
            self.stdout.write(self.style.SUCCESS("Full re-index queued"))
            return
        ids = list(Document.objects.filter(knowledge_base__slug=kb).values_list("pk", flat=True))
        for pk in ids:
            enqueue_ingest(pk)
        self.stdout.write(self.style.SUCCESS(f"Queued {len(ids)} document(s)"))
