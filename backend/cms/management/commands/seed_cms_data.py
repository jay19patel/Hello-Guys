import json
import logging
from pathlib import Path
from typing import Any
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from cms.models import (
    SiteSettings,
    Solution,
    Project,
    ProjectSection,
    TechSkill,
    Testimonial,
    FAQ,
    YouTubeVideo,
    BlogPost,
    InquirySubmission,
    WorkflowStep,
    AboutProfile,
    AboutStat,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Seeds initial CMS data from cloned njtechstudio frontend files"

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write("Starting CMS data seeding...")

        # 1. Site Settings singleton
        settings_obj = SiteSettings.get_solo()
        settings_obj.brand_name = "NJTechStudio"
        settings_obj.tagline = "Custom Web + AI Automation Studio"
        settings_obj.bio = "Full-Stack & AI Systems Engineer shipping fast, reliable production software."
        settings_obj.contact_email = "justjayy19@gmail.com"
        settings_obj.github_url = "https://github.com/jay19patel"
        settings_obj.linkedin_url = "https://www.linkedin.com/in/jay-patel-843818169/"
        settings_obj.youtube_url = "https://www.youtube.com/@njtechstudio"
        settings_obj.twitter_url = "https://x.com/jaypatel_dev"
        settings_obj.save()
        self.stdout.write("  [OK] SiteSettings singleton initialized.")

        source_dir = Path("/Users/jaypatel/Desktop/Development/njtechstudio.in/njtechstudio")
        if not source_dir.exists():
            source_dir = Path("/tmp/njtechstudio")

        # 2. Solutions
        solutions_file = source_dir / "app" / "data" / "solutions.json"
        if solutions_file.exists():
            with open(solutions_file, "r", encoding="utf-8") as f:
                sol_data = json.load(f)
            for idx, item in enumerate(sol_data):
                slug = item.get("slug") or slugify(item.get("title", ""))
                Solution.objects.update_or_create(
                    slug=slug,
                    defaults={
                        "num": item.get("num", f"0{idx+1}"),
                        "tag": item.get("tag", ""),
                        "title": item.get("title", ""),
                        "description": item.get("description", ""),
                        "icon": item.get("icon", "Rocket"),
                        "is_light": item.get("isLight", False),
                        "image": item.get("image", ""),
                        "tech_stack": item.get("techStack", []),
                        "badge": item.get("badge", "Live System"),
                        "action_text": item.get("actionText", "Explore Case Study"),
                        "order": idx,
                    }
                )
            self.stdout.write(f"  [OK] {len(sol_data)} Solutions ingested.")

        # 3. Tech Stack / Skills (tools.json)
        tools_file = source_dir / "app" / "data" / "tools.json"
        if tools_file.exists():
            with open(tools_file, "r", encoding="utf-8") as f:
                tools_data = json.load(f)
            count = 0
            # tools_data has categories as keys, e.g. "backend", "frontend", etc.
            category_mapping = {
                "backend": TechSkill.CATEGORY_BACKEND,
                "frontend": TechSkill.CATEGORY_FRONTEND,
                "database": TechSkill.CATEGORY_DATABASE,
                "cloud": TechSkill.CATEGORY_CLOUD,
                "ai_ml": TechSkill.CATEGORY_AI_ML,
                "tools": TechSkill.CATEGORY_TOOLS,
                "other-tools": TechSkill.CATEGORY_TOOLS,
            }
            if isinstance(tools_data, dict):
                for cat_key, items in tools_data.items():
                    target_cat = category_mapping.get(cat_key.lower(), TechSkill.CATEGORY_BACKEND)
                    for order_idx, skill_item in enumerate(items):
                        name = skill_item.get("name") if isinstance(skill_item, dict) else str(skill_item)
                        icon = skill_item.get("icon", "") if isinstance(skill_item, dict) else ""
                        TechSkill.objects.update_or_create(
                            name=name,
                            category=target_cat,
                            defaults={
                                "icon_url": icon,
                                "proficiency_order": order_idx,
                            }
                        )
                        count += 1
            self.stdout.write(f"  [OK] {count} TechSkills ingested.")

        # 4. Testimonials (testimonials.json)
        testimonials_file = source_dir / "app" / "data" / "testimonials.json"
        if testimonials_file.exists():
            with open(testimonials_file, "r", encoding="utf-8") as f:
                test_data = json.load(f)
            for idx, item in enumerate(test_data):
                Testimonial.objects.update_or_create(
                    name=item.get("name", "Client"),
                    role=item.get("role", "Collaborator"),
                    defaults={
                        "text": item.get("text", ""),
                        "avatar": item.get("avatar", ""),
                        "rating": item.get("rating", 5),
                        "order": idx,
                    }
                )
            self.stdout.write(f"  [OK] {len(test_data)} Testimonials ingested.")

        # 5. FAQs (faqs.json)
        faqs_file = source_dir / "app" / "data" / "faqs.json"
        if faqs_file.exists():
            with open(faqs_file, "r", encoding="utf-8") as f:
                faqs_data = json.load(f)
            for idx, item in enumerate(faqs_data):
                FAQ.objects.update_or_create(
                    question=item.get("question", ""),
                    defaults={
                        "answer": item.get("answer", ""),
                        "category": item.get("category", "General"),
                        "order": idx,
                    }
                )
            self.stdout.write(f"  [OK] {len(faqs_data)} FAQs ingested.")

        # 6. YouTube Videos (youtube.json)
        youtube_file = source_dir / "app" / "data" / "youtube.json"
        if youtube_file.exists():
            with open(youtube_file, "r", encoding="utf-8") as f:
                yt_data = json.load(f)
            for idx, item in enumerate(yt_data):
                YouTubeVideo.objects.update_or_create(
                    video_id=item.get("id", f"yt_{idx}"),
                    defaults={
                        "title": item.get("title", ""),
                        "description": item.get("description", ""),
                        "badge": item.get("badge", "TUTORIAL"),
                        "order": idx,
                    }
                )
            self.stdout.write(f"  [OK] {len(yt_data)} YouTube videos ingested.")

        # 7. Projects (public/projects.json)
        projects_file = source_dir / "public" / "projects.json"
        if projects_file.exists():
            with open(projects_file, "r", encoding="utf-8") as f:
                proj_data = json.load(f)
            for idx, item in enumerate(proj_data):
                slug = item.get("slug") or slugify(item.get("title", f"proj-{idx}"))
                proj_obj, _ = Project.objects.update_or_create(
                    slug=slug,
                    defaults={
                        "title": item.get("title", "Project"),
                        "subtitle": item.get("subtitle", ""),
                        "description": item.get("description", ""),
                        "category": item.get("category", "Web Application"),
                        "featured": item.get("featured", False),
                        "image": item.get("image", ""),
                        "technologies": item.get("technologies", []),
                        "live_url": item.get("live_url", ""),
                        "github_url": item.get("github_url", ""),
                        "status": item.get("status", "Live"),
                        "client": item.get("client", "NJTechStudio"),
                        "overview": item.get("overview", ""),
                        "challenge": item.get("challenge", ""),
                        "solution": item.get("solution", ""),
                        "order": idx,
                    }
                )
                # Ingest sections if present
                sections = item.get("sections", [])
                for s_idx, sec in enumerate(sections):
                    ProjectSection.objects.update_or_create(
                        project=proj_obj,
                        heading=sec.get("heading", f"Section {s_idx+1}"),
                        defaults={
                            "section_type": sec.get("type", "text"),
                            "items": sec.get("items", []),
                            "order": s_idx,
                        }
                    )
            self.stdout.write(f"  [OK] {len(proj_data)} Projects ingested.")

        # 8. Seed sample Inquiry if none exist
        if not InquirySubmission.objects.exists():
            InquirySubmission.objects.create(
                name="Siddharth Rao",
                email="siddharth.rao@example.com",
                phone="+91 98234 56789",
                subject="Fintech Dashboard & WhatsApp Integration",
                message="Hi Jay, I checked your Trade Buddy Broker project and would like to build a custom real-time trading notifications bot for our retail clients via WhatsApp API.",
                inquiry_type=InquirySubmission.TYPE_MODAL,
                is_replied=False,
            )
            InquirySubmission.objects.create(
                name="Emily Watson",
                email="emily@techventures.io",
                phone="+1 (415) 890-1234",
                subject="AI Lead Qualifier Web App",
                message="We are looking for a full-stack engineer to build our client portal with Meta Connector and automated DMs. Let's schedule a call this week.",
                inquiry_type=InquirySubmission.TYPE_CONTACT,
                is_replied=True,
                replies=[{
                    "timestamp": "2026-11-20 14:30:00 UTC",
                    "author": "Jay Patel",
                    "note": "Sent calendar link for discovery call on Thursday 4 PM IST."
                }],
            )
            self.stdout.write("  [OK] Sample Inquiries created.")

        # 9. Seed Blog Posts
        posts_to_seed = [
            {
                "title": "Architecting an Event-Driven Crypto Bot with Celery & Redis",
                "slug": "event-driven-crypto-trading-bot-celery",
                "category": "Case Study",
                "cover_image": "/blogermenia-home.png",
                "link": "https://github.com/jay19patel",
                "link_text": "Explore GitHub Repository",
                "published_at": "2026-11-15 10:00:00+00:00",
                "is_published": True,
                "views_count": 142,
                "likes_count": 38,
                "content": """## The Challenge: Sub-250ms Order Execution
Building high-frequency and automated execution tools in dynamic financial markets requires extreme resilience. Network latency, exchange rate-limits, and socket drops are guaranteed to happen.

When building the **Trade Buddy Broker** engine, our objective was clear:
- Process webhook triggers and signal alerts in sub-250ms.
- Ensure zero duplicate orders with strict idempotency locks.
- Stream trade status updates via real-time WebSockets and WhatsApp.

```python
import redis
from celery import Celery

app = Celery('trading_bot', broker='redis://127.0.0.1:6379/0')
redis_client = redis.Redis(host='127.0.0.1', port=6379, db=1)

@app.task(bind=True, max_retries=3, default_retry_delay=2)
def execute_signal(self, symbol: str, action: str, quantity: float, signal_id: str):
    lock_key = f"lock:trade:{signal_id}"
    # Atomic acquire with 15s TTL to prevent duplicate execution
    if not redis_client.set(lock_key, "1", nx=True, ex=15):
        return {"status": "duplicate_skipped", "signal_id": signal_id}
    
    # Place order logic
    return {"status": "executed", "symbol": symbol, "action": action}
```

## Key Architectural Decisions
1. **Separation of Gateway and Execution**: Ingestion APIs run via lightweight ASGI (FastAPI), immediately publishing validated signals into Redis queues.
2. **Worker Concurrency**: Celery gevent/prefork workers pull tasks with strict idempotency keys (`set nx=True`).
3. **Dead Letter Queues**: Failed executions trigger automatic fallbacks and alert notifications via Meta API.

> "Clean architecture is not about writing more code; it is about building systems that gracefully withstand production failures."

## Key Takeaways
- Always decouple alert ingestion from order execution.
- Use atomic Redis locks to guard against duplicate webhooks.
- Keep audit logs for every state transition in PostgreSQL."""
            },
            {
                "title": "Building Production Vector Search with Django and Qdrant",
                "slug": "building-vector-search-django-qdrant",
                "category": "Tutorial",
                "cover_image": "/blogermenia-search.png",
                "link": "https://www.youtube.com/@njtechstudio",
                "link_text": "Watch Video Tutorial",
                "published_at": "2026-11-10 12:30:00+00:00",
                "is_published": True,
                "views_count": 215,
                "likes_count": 52,
                "content": """## Why Traditional Full-Text Search Falls Short
Traditional keyword matching with SQL `LIKE` or PostgreSQL trigrams fails when users query with natural language, typos, or conceptual synonyms.

Vector embeddings transform text chunks into high-dimensional numerical vectors where semantically similar phrases cluster together in geometric space.

```python
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct
from sentence_transformers import SentenceTransformer

embedder = SentenceTransformer('all-MiniLM-L6-v2')
client = QdrantClient(host='127.0.0.1', port=6333)

def index_article(article_id: int, title: str, text: str):
    vector = embedder.encode(f"{title}: {text}").tolist()
    client.upsert(
        collection_name="articles",
        points=[
            PointStruct(
                id=article_id,
                vector=vector,
                payload={"title": title, "preview": text[:200]}
            )
        ]
    )
```

## Implementing Hybrid Search in Django
In **BlogerMenia**, we combined dense vector embeddings with sparse keyword search (BM25) using reciprocal rank fusion (RRF).

### Performance Optimization Tips:
- Batch embeddings generation using background worker tasks.
- Cache popular search query vectors in Redis to reduce embedding latency.
- Normalize vectors for cosine distance calculations.

## Key Takeaways
- Qdrant provides sub-10ms similarity search over millions of documents.
- Combining lexical search and semantic search delivers the highest retrieval precision."""
            },
            {
                "title": "Zero-Downtime Frappe & ERPNext Custom App Deployments on GCP",
                "slug": "frappe-erpnext-custom-app-deployment-gcp",
                "category": "DevOps",
                "cover_image": "/blogermenia-detail.png",
                "link": "https://www.njtechstudio.in/contact",
                "link_text": "Contact For ERP Consultation",
                "published_at": "2026-11-04 09:15:00+00:00",
                "is_published": True,
                "views_count": 180,
                "likes_count": 44,
                "content": """## Custom ERPs Without the Complexity
Managing manufacturing and sales workflows requires robust business logic. Frappe framework is one of the most productive Python-based application frameworks in existence.

However, deploying custom Frappe apps with database schema migrations without interrupting active staff can be tricky.

## Our Blue-Green Bench Setup
We orchestrate Frappe using Docker Compose and Nginx reverse proxies:
1. **MariaDB Persistence**: Centralized managed database instance with point-in-time recovery.
2. **Multi-Worker Bench**: Separate workers for short queues, long queues, and scheduler ticks.
3. **Automated Backup Hooks**: Automated snapshots before executing `bench migrate`.

```bash
#!/usr/bin/env bash
set -e

echo "Starting zero-downtime bench update..."
docker compose pull
docker compose run --rm backend bench --site njtechstudio.in migrate
docker compose up -d --no-deps backend worker-default worker-short
echo "Deployment completed successfully!"
```

## Results
- Management approvals sped up by 30%.
- Automated daily backups to GCP Cloud Storage buckets.
- High availability with near-instant failover."""
            }
        ]
        for post_data in posts_to_seed:
            slug = post_data.pop("slug")
            BlogPost.objects.update_or_create(slug=slug, defaults=post_data)
        self.stdout.write("  [OK] 3 Blog Posts ensured.")

        # 10. Seed Startup & AI Execution Flow (Workflow Steps)
        workflow_steps_to_seed = [
            {
                "step_num": "01",
                "title": "Blueprint & System Architecture",
                "tag": "Discovery & Design",
                "description": "We transform your raw startup concept into a robust technical blueprint. We design normalized schemas, clean REST/gRPC API contracts, and choose the optimal tech stack (Next.js, Python, FastAPI).",
                "icon": "GitBranch",
                "phase_milestone": "Phase 01 Milestone",
                "order": 1,
                "is_active": True,
            },
            {
                "step_num": "02",
                "title": "AI-Accelerated Development",
                "tag": "High Velocity Build",
                "description": "We build rapidly with modular code and autonomous AI agent workflows. Intelligent data ingestion, vector search, and clean, responsive interfaces that turn early prototypes into production-grade systems.",
                "icon": "Sparkles",
                "phase_milestone": "Phase 02 Milestone",
                "order": 2,
                "is_active": True,
            },
            {
                "step_num": "03",
                "title": "Automated QA & Hardening",
                "tag": "Zero Regressions",
                "description": "Every commit runs through automated test suites, bundle analyzers, and latency audits. We optimize database queries to guarantee sub-50ms API response times before anything goes live.",
                "icon": "ShieldCheck",
                "phase_milestone": "Phase 03 Milestone",
                "order": 3,
                "is_active": True,
            },
            {
                "step_num": "04",
                "title": "Production Scale & Deployment",
                "tag": "Multi-Zone Launch",
                "description": "We deploy containerized microservices across cloud infrastructure (GCP/Vercel) with CI/CD automation, monitoring, and automated failover for continuous 99.9% uptime.",
                "icon": "Rocket",
                "phase_milestone": "Phase 04 Milestone",
                "order": 4,
                "is_active": True,
            },
        ]
        for step in workflow_steps_to_seed:
            num = step.pop("step_num")
            WorkflowStep.objects.update_or_create(step_num=num, defaults=step)
        self.stdout.write("  [OK] 4 Workflow Steps (Execution Flow) ensured.")

        # 11. Seed About Profile & Stats
        AboutProfile.get_solo()
        self.stdout.write("  [OK] About Studio Profile singleton initialized.")

        about_stats_to_seed = [
            {
                "value": "2",
                "suffix": "+ Years",
                "label": "Experience",
                "is_counter": True,
                "is_dark": True,
                "order": 1,
            },
            {
                "value": "15",
                "suffix": "+ Apps",
                "label": "Built & Scaled",
                "is_counter": True,
                "is_dark": False,
                "order": 2,
            },
            {
                "value": "100",
                "suffix": "%",
                "label": "Clean Code",
                "is_counter": True,
                "is_dark": True,
                "order": 3,
            },
            {
                "value": "Full-Stack",
                "suffix": "",
                "label": "Systems & APIs",
                "is_counter": False,
                "is_dark": False,
                "order": 4,
            },
        ]
        for stat in about_stats_to_seed:
            lbl = stat.pop("label")
            AboutStat.objects.update_or_create(label=lbl, defaults=stat)
        self.stdout.write("  [OK] 4 About Stats ensured.")

        self.stdout.write(self.style.SUCCESS("CMS data seeding finished successfully!"))

