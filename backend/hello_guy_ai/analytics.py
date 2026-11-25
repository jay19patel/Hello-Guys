"""Analytics & system health aggregation for hello_guy_ai."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import redis
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from redis.exceptions import RedisError

from hello_guy_ai.conf import ai_settings
from hello_guy_ai.models import (
    Document,
    KnowledgeBase,
    LLMModel,
    Message,
    Notification,
    Reminder,
    Run,
    Thread,
    Trigger,
)


PERIOD_DAYS: dict[str, int | None] = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "all": None,
}


def _check_redis() -> bool:
    """Ping Redis to verify live queue & SSE stream connectivity."""
    try:
        client = redis.Redis.from_url(ai_settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
        try:
            return bool(client.ping())
        finally:
            client.close()
    except RedisError:
        return False


def _format_duration(run: Run) -> str:
    if not run.started_at or not run.finished_at:
        return "—"
    secs = max(0.0, (run.finished_at - run.started_at).total_seconds())
    return f"{secs:.1f}s" if secs < 60 else f"{secs / 60:.1f}m"


def build_ai_analytics(period: str = "30d") -> dict[str, Any]:
    """Collect hello_guy_ai health, usage KPIs, model/assistant breakdowns, RAG & automation stats."""
    if period not in PERIOD_DAYS:
        period = "30d"
    days = PERIOD_DAYS[period]
    now = timezone.now()
    since = now - timedelta(days=days) if days is not None else None

    runs_qs = Run.objects.all()
    threads_qs = Thread.objects.all()
    messages_qs = Message.objects.all()
    if since is not None:
        runs_qs = runs_qs.filter(created_at__gte=since)
        threads_qs = threads_qs.filter(created_at__gte=since)
        messages_qs = messages_qs.filter(created_at__gte=since)

    run_totals = runs_qs.aggregate(
        total=Count("id"),
        succeeded=Count("id", filter=Q(status=Run.Status.SUCCEEDED)),
        failed=Count("id", filter=Q(status=Run.Status.FAILED)),
        active=Count("id", filter=Q(status__in=Run.LIVE)),
        paused=Count("id", filter=Q(status=Run.Status.PAUSED)),
        prompt_tokens=Sum("prompt_tokens"),
        completion_tokens=Sum("completion_tokens"),
        cost_usd=Sum("cost_usd"),
        fb_up=Count("id", filter=Q(feedback_rating="up")),
        fb_down=Count("id", filter=Q(feedback_rating="down")),
    )

    total_runs = run_totals["total"] or 0
    succeeded = run_totals["succeeded"] or 0
    failed = run_totals["failed"] or 0
    finished_count = succeeded + failed
    success_rate = round((succeeded / finished_count) * 100, 1) if finished_count else None

    prompt_tokens = run_totals["prompt_tokens"] or 0
    completion_tokens = run_totals["completion_tokens"] or 0
    total_tokens = prompt_tokens + completion_tokens
    total_cost = run_totals["cost_usd"] or Decimal("0")

    fb_up = run_totals["fb_up"] or 0
    fb_down = run_totals["fb_down"] or 0
    fb_total = fb_up + fb_down
    fb_rate = round((fb_up / fb_total) * 100) if fb_total else None

    status_counts = {
        row["status"]: row["count"]
        for row in runs_qs.values("status").annotate(count=Count("id"))
    }
    status_rows = [
        {
            "label": label,
            "value": value,
            "count": status_counts.get(value, 0),
            "pct": round((status_counts.get(value, 0) / total_runs) * 100) if total_runs else 0,
        }
        for value, label in Run.Status.choices
    ]

    # Daily trend (last 14 days within filter)
    trend_days = min(days, 14) if days else 14
    trend_start = timezone.localdate() - timedelta(days=trend_days - 1)
    daily_raw = {
        row["day"]: row
        for row in Run.objects.filter(created_at__date__gte=trend_start)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(
            total=Count("id"),
            succeeded=Count("id", filter=Q(status=Run.Status.SUCCEEDED)),
            failed=Count("id", filter=Q(status=Run.Status.FAILED)),
        )
    }
    daily_trend = []
    max_daily = max((r["total"] for r in daily_raw.values()), default=1) or 1
    for offset in range(trend_days):
        d = trend_start + timedelta(days=offset)
        row = daily_raw.get(d, {"total": 0, "succeeded": 0, "failed": 0})
        daily_trend.append({
            "label": d.strftime("%d %b"),
            "total": row["total"],
            "succeeded": row["succeeded"],
            "failed": row["failed"],
            "height_pct": max(4, round((row["total"] / max_daily) * 100)) if row["total"] else 0,
        })

    model_rows = list(
        runs_qs.exclude(model="")
        .values("model")
        .annotate(
            runs=Count("id"),
            failed=Count("id", filter=Q(status=Run.Status.FAILED)),
            tokens=Sum("prompt_tokens") + Sum("completion_tokens"),
            cost=Sum("cost_usd"),
        )
        .order_by("-runs")[:8]
    )

    assistant_rows = list(
        threads_qs.values("assistant_id")
        .annotate(
            threads=Count("id", distinct=True),
            runs=Count("runs", distinct=True),
        )
        .order_by("-threads")[:8]
    )

    doc_counts = {
        row["status"]: row["count"]
        for row in Document.objects.values("status").annotate(count=Count("id"))
    }
    total_chunks = Document.objects.aggregate(chunks=Sum("chunk_count"))["chunks"] or 0

    recent_runs = [
        {
            "id": r.id,
            "status": r.status,
            "assistant_id": r.thread.assistant_id,
            "thread_name": r.thread.name or str(r.thread_id)[:8],
            "model": r.model or "—",
            "tokens": (r.prompt_tokens or 0) + (r.completion_tokens or 0),
            "cost": r.cost_usd,
            "duration": _format_duration(r),
            "feedback": r.feedback_rating,
            "error": (r.error or "")[:140],
            "created_at": r.created_at,
        }
        for r in runs_qs.select_related("thread").order_by("-created_at")[:15]
    ]

    return {
        "period": period,
        "health": {
            "redis_ok": _check_redis(),
            "chat_model": LLMModel.default(LLMModel.Kind.CHAT),
            "embed_model": LLMModel.default(LLMModel.Kind.EMBEDDING),
            "active_runs": run_totals["active"] or 0,
            "paused_runs": run_totals["paused"] or 0,
        },
        "kpis": {
            "total_runs": total_runs,
            "succeeded": succeeded,
            "failed": failed,
            "success_rate": success_rate,
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_cost": total_cost,
            "threads": threads_qs.count(),
            "needs_human": Thread.objects.filter(needs_human=True).count(),
            "human_mode": Thread.objects.filter(human_mode=True).count(),
            "messages": messages_qs.count(),
            "fb_up": fb_up,
            "fb_down": fb_down,
            "fb_rate": fb_rate,
        },
        "status_rows": status_rows,
        "daily_trend": daily_trend,
        "model_rows": model_rows,
        "assistant_rows": assistant_rows,
        "rag": {
            "kb_count": KnowledgeBase.objects.filter(enabled=True).count(),
            "docs_ready": doc_counts.get(Document.Status.READY, 0),
            "docs_pending": doc_counts.get(Document.Status.PENDING, 0) + doc_counts.get(Document.Status.PROCESSING, 0),
            "docs_failed": doc_counts.get(Document.Status.FAILED, 0),
            "total_chunks": total_chunks,
        },
        "automation": {
            "triggers_enabled": Trigger.objects.filter(enabled=True).count(),
            "triggers_total": Trigger.objects.count(),
            "reminders_scheduled": Reminder.objects.filter(status=Reminder.Status.SCHEDULED).count(),
            "reminders_done": Reminder.objects.filter(status=Reminder.Status.DONE).count(),
            "unread_notifications": Notification.objects.filter(is_read=False).count(),
        },
        "recent_runs": recent_runs,
    }
