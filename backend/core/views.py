from datetime import datetime, time, timedelta
from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from config.views import HubPortalView  # noqa: F401  (the single hub view, mounted at / by core.urls)
from core import follow_ups as global_followups, notifications as global_notifications
from core.models import FollowUp, FollowUpChannel
from core.safety import int_or, json_body, redirect_next, safe_url
from meta_connector.roles import ADMIN, AGENT, VIEWER, RoleRequiredMixin, team


def _wants_json(request) -> bool:
    return request.content_type == "application/json" or request.headers.get("Accept") == "application/json"


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
        limit = int_or(request.GET.get("limit"), 30)
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
        fu_qs = FollowUp.objects.select_related("channel").filter(
            status=FollowUp.Status.PENDING
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
                "related_url": safe_url(fu.related_url, "/follow-ups/"),
            }
            for fu in fu_qs[:12]
        ]
        return JsonResponse({
            "items": items,
            "unread_count": global_notifications.unread_count(request.user),
            "followups": followups,
            "followups_count": fu_qs.count(),
            "followups_due_count": fu_qs.filter(due_at__lte=now).count(),
        })


class NotificationUnreadCountView(_JsonLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request):
        global_followups.notify_due_follow_ups(request.user)
        now = timezone.now()
        fu_qs = FollowUp.objects.filter(
            status=FollowUp.Status.PENDING
        ).filter(
            Q(assigned_to=request.user) | Q(assigned_to__isnull=True, created_by=request.user)
        )
        return JsonResponse({
            "count": global_notifications.unread_count(request.user),
            "followups_count": fu_qs.count(),
            "followups_due": fu_qs.filter(due_at__lte=now).count(),
        })


class NotificationMarkReadView(_JsonLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        payload = json_body(request)
        ids = payload.get("ids")
        if ids is not None and not (isinstance(ids, list) and all(isinstance(i, int) for i in ids)):
            return JsonResponse({"error": "ids must be a list of integers."}, status=400)
        updated = global_notifications.mark_read(request.user, ids=ids)
        return JsonResponse({
            "count": updated,
            "unread_count": global_notifications.unread_count(request.user),
        })


def _parse_due_at(due_raw: str):
    dt = timezone.datetime.fromisoformat((due_raw or "").strip())
    return timezone.make_aware(dt, timezone.get_current_timezone()) if timezone.is_naive(dt) else dt


class FollowUpListView(RoleRequiredMixin, ListView):
    """Unified Follow-ups hub: CRM leads, meetings, payments, and general tasks."""

    template_name = "core/follow_ups.html"
    context_object_name = "follow_ups"
    paginate_by = 25
    role = VIEWER

    def get_queryset(self):
        from crm.models import Customer

        global_followups.ensure_default_channels()
        status = self.request.GET.get("status", FollowUp.Status.PENDING)
        channel_slug = self.request.GET.get("channel", "").strip()
        priority = self.request.GET.get("priority", "").strip()
        customer_id = int_or(self.request.GET.get("customer"))
        q = self.request.GET.get("q", "").strip()
        now = timezone.now()
        tz = timezone.get_current_timezone()
        today_date = timezone.localdate(now)
        today_start = datetime.combine(today_date, time.min, tzinfo=tz)
        today_end = today_start + timedelta(days=1)

        qs = global_followups.visible_followups(self.request.user).select_related(
            "channel", "assigned_to", "created_by", "customer", "opportunity"
        )
        if status == "overdue":
            qs = qs.filter(status=FollowUp.Status.PENDING, due_at__lt=now)
        elif status == "today":
            qs = qs.filter(status=FollowUp.Status.PENDING, due_at__gte=today_start, due_at__lt=today_end)
        elif status in FollowUp.Status.values:
            qs = qs.filter(status=status)

        if channel_slug:
            qs = qs.filter(channel__slug=channel_slug)
        if priority in FollowUp.Priority.values:
            qs = qs.filter(priority=priority)
        if customer_id:
            qs = qs.filter(customer_id=customer_id)
        if self.request.GET.get("mine"):
            qs = qs.filter(assigned_to=self.request.user)
        if q:
            qs = qs.filter(
                Q(title__icontains=q) | Q(note__icontains=q) | Q(related_label__icontains=q) | Q(customer__name__icontains=q)
            )
        self._customers = Customer.objects.order_by("name")[:100]
        return qs.order_by("due_at" if status in ("pending", "overdue", "today") else "-updated_at")

    def get_context_data(self, **kwargs):
        default_due = (timezone.now() + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
        analytics = global_followups.followup_analytics(self.request.user)
        return super().get_context_data(
            status_filter=self.request.GET.get("status", FollowUp.Status.PENDING),
            channel_filter=self.request.GET.get("channel", "").strip(),
            priority_filter=self.request.GET.get("priority", "").strip(),
            mine_filter=bool(self.request.GET.get("mine")),
            q=self.request.GET.get("q", "").strip(),
            channels=FollowUpChannel.objects.filter(is_active=True),
            priorities=FollowUp.Priority.choices,
            customers=getattr(self, "_customers", []),
            team_users=team().order_by("username"),
            default_due=timezone.localtime(default_due).strftime("%Y-%m-%dT%H:%M"),
            **analytics,
            **kwargs,
        )


class FollowUpCreateView(RoleRequiredMixin, View):
    http_method_names = ["post"]
    role = edit_role = AGENT

    def post(self, request):
        customer_raw = request.POST.get("customer_id")
        customer = opportunity = None
        if customer_raw:
            from crm.models import Customer, Opportunity

            customer = get_object_or_404(Customer, pk=int_or(customer_raw, 0))
            opp_id = int_or(request.POST.get("opportunity_id"), 0)
            opportunity = Opportunity.objects.filter(pk=opp_id, customer=customer).first() if opp_id else None

        title = request.POST.get("title", "").strip()
        if customer is None and not title:
            messages.error(request, "Give your follow-up a title.")
            return redirect_next(request, "core:follow-ups")

        try:
            due_at = _parse_due_at(request.POST.get("due_at", ""))
        except ValueError:
            messages.error(request, "Pick a valid due date and time.")
            return redirect_next(request, "core:follow-ups")

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

        priority = request.POST.get("priority", FollowUp.Priority.NORMAL)
        note = request.POST.get("note", "").strip()
        assigned_id = int_or(request.POST.get("assigned_to"))
        assigned_to = (team().filter(pk=assigned_id).first() if assigned_id else None) or (None if customer else request.user)

        if customer is not None:
            from crm import services as crm_services

            crm_services.schedule_follow_up(
                customer=customer,
                opportunity=opportunity,
                due_at=due_at,
                channel=(channel.name if hasattr(channel, "name") else (channel or "WhatsApp")),
                note=note,
                title=title,
                priority=priority,
                assigned_to=assigned_to,
                created_by=request.user,
            )
            messages.success(request, f"Follow-up scheduled for {customer.name}.")
        else:
            global_followups.schedule_follow_up(
                title=title,
                due_at=due_at,
                channel=channel,
                note=note,
                priority=priority,
                assigned_to=assigned_to,
                created_by=request.user,
                related_label=request.POST.get("related_label", "").strip(),
                related_url=request.POST.get("related_url", "").strip(),
            )
            messages.success(request, f"Follow-up \"{title}\" added.")
        return redirect_next(request, "core:follow-ups")


class FollowUpChannelCreateView(RoleRequiredMixin, View):
    http_method_names = ["post"]
    role = edit_role = AGENT

    def post(self, request):
        is_json = request.content_type == "application/json"
        data = json_body(request) if is_json else request.POST
        name = (data.get("name") or "").strip()
        icon = (data.get("icon") or "fa-solid fa-bolt").strip()
        color = (data.get("color") or "indigo").strip()

        if not name:
            if is_json:
                return JsonResponse({"error": "Channel name is required."}, status=400)
            messages.error(request, "Channel name is required.")
            return redirect("core:follow-ups")

        channel = global_followups.get_or_create_channel(name, icon=icon, color=color, user=request.user)
        if _wants_json(request):
            return JsonResponse({"id": channel.pk, "name": channel.name, "slug": channel.slug, "icon": channel.icon, "color": channel.color}, status=201)
        messages.success(request, f"Channel \"{channel.name}\" ready.")
        return redirect("core:follow-ups")


class FollowUpActionView(RoleRequiredMixin, View):
    http_method_names = ["post"]
    role = edit_role = AGENT

    def post(self, request, pk: int):
        fu = get_object_or_404(global_followups.visible_followups(request.user), pk=pk)
        data = json_body(request) if request.content_type == "application/json" else request.POST
        action = data.get("action", "")
        hours = int_or(data.get("hours"), 24)

        if action == "done":
            global_followups.complete_follow_up(fu, user=request.user)
            msg = "Follow-up marked done."
        elif action == "snooze":
            preset = (data.get("preset") or "").strip()
            custom_due = None
            tz = timezone.get_current_timezone()
            now_local = timezone.localtime(timezone.now(), tz)
            if preset == "tomorrow":
                custom_due = datetime.combine(now_local.date() + timedelta(days=1), time(10, 0), tzinfo=tz)
                msg = "Follow-up moved to tomorrow 10:00 AM."
            elif preset == "next_monday":
                days_ahead = (7 - now_local.weekday()) % 7 or 7
                custom_due = datetime.combine(now_local.date() + timedelta(days=days_ahead), time(10, 0), tzinfo=tz)
                msg = "Follow-up moved to next Monday 10:00 AM."
            elif data.get("due_at"):
                try:
                    custom_due = _parse_due_at(data.get("due_at", ""))
                    msg = "Follow-up rescheduled."
                except ValueError:
                    msg = f"Follow-up snoozed +{max(1, min(hours, 24 * 30))}h."
            else:
                msg = f"Follow-up snoozed +{max(1, min(hours, 24 * 30))}h."
            global_followups.snooze_follow_up(fu, hours=hours, due_at=custom_due)
        elif action == "cancel":
            global_followups.cancel_follow_up(fu)
            msg = "Follow-up cancelled."
        else:
            if _wants_json(request):
                return JsonResponse({"error": "Unknown action"}, status=400)
            return redirect("core:follow-ups")

        if _wants_json(request):
            return JsonResponse({"ok": True, "status": fu.status, "due_at": fu.due_at.isoformat()})
        messages.success(request, msg)
        return redirect_next(request, "core:follow-ups")


class QuickSearchAPIView(RoleRequiredMixin, View):
    """Global Cmd/Ctrl+K command palette search across pages, CRM leads/customers, Follow-ups and Projects."""

    http_method_names = ["get"]
    role = VIEWER

    _PAGES = [
        {"title": "Hub Portal", "sub": "Module launcher & daily summary", "url": "/", "icon": "fa-solid fa-shapes"},
        {"title": "Follow-ups", "sub": "Scheduled callbacks, reminders & trends", "url": "/follow-ups/", "icon": "fa-regular fa-clock"},
        {"title": "Overdue Follow-ups", "sub": "Action overdue follow-ups now", "url": "/follow-ups/?status=overdue", "icon": "fa-solid fa-triangle-exclamation"},
        {"title": "CRM Overview", "sub": "Pipeline KPIs, funnel & leaderboard", "url": "/crm/", "icon": "fa-solid fa-chart-simple"},
        {"title": "Opportunities Pipeline", "sub": "Kanban board & deal stages", "url": "/crm/opportunities/", "icon": "fa-solid fa-columns"},
        {"title": "Customers", "sub": "All contacts & accounts", "url": "/crm/customers/", "icon": "fa-solid fa-user-tie"},
        {"title": "Meetings", "sub": "Calendar & transcripts", "url": "/crm/meetings/", "icon": "fa-solid fa-calendar-days"},
        {"title": "Live Chat Inbox", "sub": "Website & Meta handoff inbox", "url": "/crm/inbox/", "icon": "fa-solid fa-comments"},
        {"title": "Projects", "sub": "Internal PMS tasks & milestones", "url": "/pms/", "icon": "fa-solid fa-diagram-project"},
        {"title": "Meta Inbox", "sub": "WhatsApp & Instagram messages", "url": "/meta/", "icon": "fa-solid fa-inbox"},
        {"title": "AI Approvals", "sub": "Review drafted WhatsApp/IG AI replies", "url": "/meta/approvals/", "icon": "fa-solid fa-wand-magic-sparkles"},
        {"title": "AI Analytics", "sub": "LLM usage, health & runs", "url": "/ai/analytics/", "icon": "fa-solid fa-chart-line"},
        {"title": "AI Chat", "sub": "Talk to Hello Guy & Sales Assistant", "url": "/chat/", "icon": "fa-solid fa-robot"},
    ]

    def get(self, request):
        from crm.models import Customer, Opportunity
        from pms.models import Project

        q = request.GET.get("q", "").strip()[:80]
        ql = q.lower()
        results = []

        pages = [p for p in self._PAGES if not ql or ql in p["title"].lower() or ql in p["sub"].lower()]
        for p in pages[:6]:
            results.append({"group": "Navigate", **p})

        if len(q) >= 2:
            for c in Customer.objects.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(company__icontains=q))[:5]:
                results.append({
                    "group": "Customers",
                    "title": c.name,
                    "sub": c.email or c.phone or c.company or c.get_source_display(),
                    "url": reverse("crm:customer", args=[c.pk]),
                    "icon": "fa-solid fa-user-tie",
                })
            for o in Opportunity.objects.select_related("customer").filter(
                Q(title__icontains=q) | Q(reference__icontains=q) | Q(customer__name__icontains=q)
            )[:5]:
                results.append({
                    "group": "Opportunities",
                    "title": o.title,
                    "sub": f"{o.customer.name} · {o.get_status_display()} · {o.reference}",
                    "url": reverse("crm:opportunity", args=[o.pk]),
                    "icon": "fa-solid fa-filter",
                })
            for fu in global_followups.visible_followups(request.user).select_related("channel").filter(
                Q(title__icontains=q) | Q(note__icontains=q) | Q(related_label__icontains=q)
            )[:5]:
                results.append({
                    "group": "Follow-ups",
                    "title": fu.title,
                    "sub": f"{fu.channel_name} · {fu.get_status_display()}",
                    "url": fu.safe_related_url or reverse("core:follow-ups"),
                    "icon": "fa-regular fa-clock",
                })
            for proj in Project.objects.visible_to(request.user).filter(Q(name__icontains=q) | Q(description__icontains=q))[:4]:
                results.append({
                    "group": "Projects",
                    "title": proj.name,
                    "sub": proj.get_status_display(),
                    "url": reverse("pms:project", args=[proj.pk]),
                    "icon": "fa-solid fa-diagram-project",
                })

        return JsonResponse({"items": results})

