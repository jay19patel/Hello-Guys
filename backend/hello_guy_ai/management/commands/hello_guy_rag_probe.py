"""See what retrieval would return for real questions, with scores, to calibrate the embedding model's
"Min similarity" (admin > LLM models). Embeds in memory from Document text: it never opens Milvus, so it is
safe while the worker runs. Costs one embedding call per batch of 32 texts.

python manage.py hello_guy_rag_probe "do you build chatbots?" "hi"
python manage.py hello_guy_rag_probe --kb company-public --file questions.txt

questions.txt: one question per line; "question => expected title" also checks the top hit (PASS/FAIL).
"""

import math

from django.core.management.base import BaseCommand, CommandError

from hello_guy_ai.conf import ai_settings
from hello_guy_ai.models import Document
from hello_guy_ai.services.rag import TextChunker, embed_texts, embedding_config, embedding_min_score, load_text


class Command(BaseCommand):
    help = "Show retrieval matches and scores for questions (calibrates the similarity threshold)"

    def add_arguments(self, parser):
        parser.add_argument("questions", nargs="*")
        parser.add_argument("--file", help="One question per line, optionally 'question => expected title'")
        parser.add_argument("--kb", help="Knowledge base slug (default: all)")
        parser.add_argument("--top", type=int, default=3)

    def handle(self, *args, questions, file=None, kb=None, top=3, **options):
        cases = [(q, "") for q in questions]
        if file:
            with open(file, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip() and not line.startswith("#"):
                        question, _, expected = line.partition("=>")
                        cases.append((question.strip(), expected.strip()))
        if not cases:
            raise CommandError("Give questions as arguments or --file")

        chunks = self._chunks(kb)
        if not chunks:
            raise CommandError("No documents with text to probe")
        vectors = embed_texts([text for _, text in chunks] + [q for q, _ in cases])
        doc_vectors, question_vectors = vectors[: len(chunks)], vectors[len(chunks) :]
        threshold = embedding_min_score()
        self.stdout.write(f"Model: {embedding_config()[0]} | min similarity: {threshold} | {len(chunks)} chunks\n")

        failures = 0
        for (question, expected), qv in zip(cases, question_vectors, strict=True):
            ranked = sorted(((_cosine(qv, dv), title) for (title, _), dv in zip(chunks, doc_vectors)), reverse=True)
            self.stdout.write(self.style.MIGRATE_HEADING(question))
            for score, title in ranked[:top]:
                used = threshold is None or score >= threshold
                self.stdout.write(f"  {score:.3f} {'USED' if used else '----'}  {title}")
            if expected:
                passed = expected.lower() in ranked[0][1].lower()
                failures += not passed
                self.stdout.write(self.style.SUCCESS("  PASS") if passed else self.style.ERROR(f"  FAIL: wanted {expected!r}"))
        if failures:
            raise CommandError(f"{failures} expected match(es) failed")

    def _chunks(self, kb: str | None) -> list[tuple[str, str]]:
        chunker = TextChunker(ai_settings.CHUNK_SIZE, ai_settings.CHUNK_OVERLAP)
        docs = Document.objects.select_related("knowledge_base")
        if kb:
            docs = docs.filter(knowledge_base__slug=kb)
        out = []
        for doc in docs:
            text = doc.content or (load_text(doc.file.path) if doc.file else "")
            out.extend((doc.title, chunk) for chunk in chunker.split(text))
        return out


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1)
