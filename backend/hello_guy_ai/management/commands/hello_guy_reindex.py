"""Re-ingest knowledge documents (after changing EMBEDDING_MODEL / MILVUS_COLLECTION, or upgrading)."""

from django.core.management.base import BaseCommand

from hello_guy_ai.models import Document
from hello_guy_ai.services import enqueue_ingest


class Command(BaseCommand):
    help = "Queue every (or one knowledge base's) document for re-ingestion"

    def add_arguments(self, parser):
        parser.add_argument("--kb", help="Knowledge base slug")

    def handle(self, *args, kb: str | None = None, **options):
        docs = Document.objects.all()
        if kb:
            docs = docs.filter(knowledge_base__slug=kb)
        count = 0
        for pk in docs.values_list("pk", flat=True):
            enqueue_ingest(pk)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Queued {count} document(s)"))
