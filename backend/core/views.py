import json
import logging
from datetime import timedelta
from django.contrib import messages
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from config.views import HubPortalView  # noqa: F401  (the single hub view, mounted at / by core.urls)
from core import reminders as global_followups, notifications as global_notifications
from core.models import FollowUpChannel, GlobalFollowUp
from core.safety import redirect_next, safe_url
from meta_connector.roles import ADMIN, AGENT, VIEWER, RoleRequiredMixin, has_role, team

logger = logging.getLogger(__name__)


def _to_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _visible_followups(user):
    """Admins see every reminder; everyone else only their own (assigned to or created by them)."""
    qs = GlobalFollowUp.objects.all()
    return qs if has_role(user, ADMIN) else qs.filter(Q(assigned_to=user) | Q(created_by=user))


class _JsonLoginRequiredMixin:
    """Return 401 JSON instead of HTML redirect when an API call is unauthenticated."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Authentication required."}, status=401)
        return super().dispatch(request, *args, **kwargs)


class NotificationListView(_JsonLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request):
        global_followups.notify_due_follow_ups(request.user)
        unread_only = request.GET.get("unread_only") in ("1", "true", "yes")
        category = request.GET.get("category") or None
        limit = _to_int(request.GET.get("limit"), 30)
        rows = global_notifications.notifications_for(
            request.user, unread_only=unread_only, category=category, limit=limit
        )
        items = [
            {
                "id": n.pk,
                "title": n.title,
                "body": n.body,
                "url": safe_url(n.url),
                "category": n.category,
                "level": n.level,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat(),
            }
            for n in rows
        ]
        now = timezone.now()
        fu_qs = GlobalFollowUp.objects.select_related("channel").filter(
            status=GlobalFollowUp.Status.PENDING
        ).filter(
            Q(assigned_to=request.user) | Q(assigned_to__isnull=True, created_by=request.user)
        ).order_by("due_at")
        followups = [
            {
                "id": fu.pk,
                "title": fu.title,
                "note": fu.note,
                "channel": fu.channel.name if fu.channel else "General",
                "channel_icon": fu.channel.icon if fu.channel else "fa-solid fa-bolt",
                "priority": fu.priority,
                "due_at": fu.due_at.isoformat(),
                "is_overdue": fu.due_at < now,
                "related_label": fu.related_label,
                "related_url": safe_url(fu.related_url, "/reminders/"),
            }
            for fu in fu_qs[:12]
        ]
        return JsonResponse({
            "items": items,
            "unread_count": global_notifications.unread_count(request.user),
            "followups": followups,
            "followups_due_count": fu_qs.filter(due_at__lte=now).count(),
        })


class NotificationUnreadCountView(_JsonLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request):
        global_followups.notify_due_follow_ups(request.user)
        now = timezone.now()
        due_fu = GlobalFollowUp.objects.filter(
            status=GlobalFollowUp.Status.PENDING, due_at__lte=now
        ).filter(
            Q(assigned_to=request.user) | Q(assigned_to__isnull=True, created_by=request.user)
        ).count()
        return JsonResponse({
            "count": global_notifications.unread_count(request.user),
            "followups_due": due_fu,
        })


class NotificationMarkReadView(_JsonLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            payload = {}
        ids = payload.get("ids")
        if ids is not None and not (isinstance(ids, list) and all(isinstance(i, int) for i in ids)):
            return JsonResponse({"error": "ids must be a list of integers."}, status=400)
        updated = global_notifications.mark_read(request.user, ids=ids)
        return JsonResponse({
            "count": updated,
            "unread_count": global_notifications.unread_count(request.user),
        })


class GlobalFollowUpListView(RoleRequiredMixin, ListView):
    """Global Follow-ups hub: any topic, any custom channel, integrated with global notifications."""

    template_name = "core/reminders.html"
    context_object_name = "follow_ups"
    paginate_by = 25
    role = VIEWER

    def get_queryset(self):
        global_followups.ensure_default_channels()
        status = self.request.GET.get("status", GlobalFollowUp.Status.PENDING)
        channel_slug = self.request.GET.get("channel", "").strip()
        q = self.request.GET.get("q", "").strip()
        now = timezone.now()

        qs = _visible_followups(self.request.user).select_related("channel", "assigned_to", "created_by")
        if status == "overdue":
            qs = qs.filter(status=GlobalFollowUp.Status.PENDING, due_at__lt=now)
        elif status in GlobalFollowUp.Status.values:
            qs = qs.filter(status=status)

        if channel_slug:
            qs = qs.filter(channel__slug=channel_slug)
        if self.request.GET.get("mine"):
            qs = qs.filter(assigned_to=self.request.user)
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(note__icontains=q) | Q(related_label__icontains=q))
        return qs.order_by("due_at" if status in ("pending", "overdue") else "-updated_at")

    def get_context_data(self, **kwargs):
        now = timezone.now()
        today_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        default_due = (now + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
        # Analytics: 14-day trend (scheduled vs completed)
        days_range = 14
        start_day = timezone.localtime(now).date() - timedelta(days=days_range - 1)
        all_fu = _visible_followups(self.request.user)
        scheduled_by_day = {
            row["created_at__date"]: row["c"]
            for row in all_fu.filter(created_at__date__gte=start_day)
            .values("created_at__date")
            .annotate(c=Count("id"))
        }
        completed_by_day = {
            row["completed_at__date"]: row["c"]
            for row in all_fu.filter(status=GlobalFollowUp.Status.DONE, completed_at__date__gte=start_day)
            .values("completed_at__date")
            .annotate(c=Count("id"))
        }
        trend_labels, trend_scheduled, trend_completed = [], [], []
        for i in range(days_range):
            d = start_day + timedelta(days=i)
            trend_labels.append(d.strftime("%d %b"))
            trend_scheduled.append(scheduled_by_day.get(d, 0))
            trend_completed.append(completed_by_day.get(d, 0))

        # Channel breakdown
        total_all = all_fu.count()
        pending_count = all_fu.filter(status=GlobalFollowUp.Status.PENDING).count()
        overdue_count = all_fu.filter(status=GlobalFollowUp.Status.PENDING, due_at__lt=now).count()
        done_count = all_fu.filter(status=GlobalFollowUp.Status.DONE).count()
        cancelled_count = all_fu.filter(status=GlobalFollowUp.Status.CANCELLED).count()
        completion_rate = round((done_count / total_all) * 100) if total_all else None

        mine = Q() if has_role(self.request.user, ADMIN) else (
            Q(follow_ups__assigned_to=self.request.user) | Q(follow_ups__created_by=self.request.user)
        )
        channel_rows = (
            FollowUpChannel.objects.filter(is_active=True)
            .annotate(
                total=Count("follow_ups", filter=mine),
                done_cnt=Count("follow_ups", filter=mine & Q(follow_ups__status=GlobalFollowUp.Status.DONE)),
                pending_cnt=Count("follow_ups", filter=mine & Q(follow_ups__status=GlobalFollowUp.Status.PENDING)),
            )
            .order_by("-total", "name")
        )
        max_ch = max((c.total for c in channel_rows), default=1) or 1
        channel_stats = [
            {
                "name": c.name,
                "icon": c.icon,
                "total": c.total,
                "done": c.done_cnt,
                "pending": c.pending_cnt,
                "pct": round((c.total / max_ch) * 100) if c.total else 0,
            }
            for c in channel_rows
            if c.total > 0
        ]

        priority_counts = {
            row["priority"]: row["c"]
            for row in all_fu.filter(status=GlobalFollowUp.Status.PENDING)
            .values("priority")
            .annotate(c=Count("id"))
        }
        priority_stats = [
            {"label": label, "value": val, "count": priority_counts.get(val, 0)}
            for val, label in GlobalFollowUp.Priority.choices
        ]

        return super().get_context_data(
            status_filter=self.request.GET.get("status", GlobalFollowUp.Status.PENDING),
            channel_filter=self.request.GET.get("channel", "").strip(),
            q=self.request.GET.get("q", "").strip(),
            channels=FollowUpChannel.objects.filter(is_active=True),
            priorities=GlobalFollowUp.Priority.choices,
            team_users=team().order_by("username"),
            default_due=timezone.localtime(default_due).strftime("%Y-%m-%dT%H:%M"),
            stats={
                "pending": pending_count,
                "overdue": overdue_count,
                "ontime": max(0, pending_count - overdue_count),
                "today": all_fu.filter(
                    status=GlobalFollowUp.Status.PENDING, due_at__gte=today_start, due_at__lt=today_end
                ).count(),
                "done": done_count,
                "cancelled": cancelled_count,
                "total": total_all,
                "completion_rate": completion_rate,
            },
            daily_trend={
                "labels": trend_labels,
                "scheduled": trend_scheduled,
                "completed": trend_completed,
            },
            channel_stats=channel_stats,
            priority_stats=priority_stats,
            **kwargs,
        )


class GlobalFollowUpCreateView(RoleRequiredMixin, View):
    http_method_names = ["post"]
    role = edit_role = AGENT

    def post(self, request):
        title = request.POST.get("title", "").strip()
        if not title:
            messages.error(request, "Give your reminder a title.")
            return redirect_next(request, "core:reminders")

        due_raw = request.POST.get("due_at", "").strip()
        try:
            due_at = timezone.datetime.fromisoformat(due_raw)
            if timezone.is_naive(due_at):
                due_at = timezone.make_aware(due_at, timezone.get_current_timezone())
        except ValueError:
            messages.error(request, "Pick a valid due date and time.")
            return redirect_next(request, "core:reminders")

        new_channel_name = request.POST.get("new_channel_name", "").strip()
        if new_channel_name:
            channel = global_followups.get_or_create_channel(
                new_channel_name,
                icon=request.POST.get("new_channel_icon", "fa-solid fa-bolt").strip(),
                color=request.POST.get("new_channel_color", "indigo").strip(),
                user=request.user,
            )
        else:
            channel = request.POST.get("channel") or None

        assigned_id = _to_int(request.POST.get("assigned_to"), 0)
        assigned_to = (team().filter(pk=assigned_id).first() if assigned_id else None) or request.user

        global_followups.schedule_follow_up(
            title=title,
            due_at=due_at,
            channel=channel,
            note=request.POST.get("note", "").strip(),
            priority=request.POST.get("priority", GlobalFollowUp.Priority.NORMAL),  # invalid -> normal in schedule_follow_up
            assigned_to=assigned_to,
            created_by=request.user,
            related_label=request.POST.get("related_label", "").strip(),
            related_url=request.POST.get("related_url", "").strip(),
        )
        messages.success(request, f"Reminder \"{title}\" added.")
        return redirect_next(request, "core:reminders")


class FollowUpChannelCreateView(RoleRequiredMixin, View):
    http_method_names = ["post"]
    role = edit_role = AGENT

    def post(self, request):
        if request.content_type == "application/json":
            try:
                data = json.loads(request.body.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                return JsonResponse({"error": "Invalid JSON"}, status=400)
            name = (data.get("name") or "").strip()
            icon = (data.get("icon") or "fa-solid fa-bolt").strip()
            color = (data.get("color") or "indigo").strip()
        else:
            name = request.POST.get("name", "").strip()
            icon = request.POST.get("icon", "fa-solid fa-bolt").strip()
            color = request.POST.get("color", "indigo").strip()

        if not name:
            if request.content_type == "application/json":
                return JsonResponse({"error": "Channel name is required."}, status=400)
            messages.error(request, "Channel name is required.")
            return redirect("core:follow-ups")

        channel = global_followups.get_or_create_channel(name, icon=icon, color=color, user=request.user)
        if request.content_type == "application/json" or request.headers.get("Accept") == "application/json":
            return JsonResponse({"id": channel.pk, "name": channel.name, "slug": channel.slug, "icon": channel.icon, "color": channel.color}, status=201)
        messages.success(request, f"Channel \"{channel.name}\" ready.")
        return redirect("core:follow-ups")


class GlobalFollowUpActionView(RoleRequiredMixin, View):
    http_method_names = ["post"]
    role = edit_role = AGENT

    def post(self, request, pk: int):
        fu = get_object_or_404(_visible_followups(request.user), pk=pk)
        if request.content_type == "application/json":
            try:
                data = json.loads(request.body.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                data = {}
            action = data.get("action", "")
            hours = _to_int(data.get("hours"), 24)
        else:
            action = request.POST.get("action", "")
            hours = _to_int(request.POST.get("hours"), 24)

        if action == "done":
            global_followups.complete_follow_up(fu)
            msg = "Reminder marked done."
        elif action == "snooze":
            global_followups.snooze_follow_up(fu, hours=hours)
            msg = f"Reminder snoozed +{max(1, min(hours, 24 * 30))}h."
        elif action == "cancel":
            global_followups.cancel_follow_up(fu)
            msg = "Reminder cancelled."
        else:
            if request.content_type == "application/json" or request.headers.get("Accept") == "application/json":
                return JsonResponse({"error": "Unknown action"}, status=400)
            return redirect("core:reminders")

        if request.content_type == "application/json" or request.headers.get("Accept") == "application/json":
            return JsonResponse({"ok": True, "status": fu.status, "due_at": fu.due_at.isoformat()})
        messages.success(request, msg)
        return redirect_next(request, "core:reminders")

