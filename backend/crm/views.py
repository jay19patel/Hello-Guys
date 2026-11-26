"""CRM dashboard (/crm/). Roles from meta_connector.roles: Viewer reads, Agent works customers/opportunities/chats,
Admin manages settings and deletes. Detail pages take POSTs with an `action` field; each runs `post_<action>()`."""

from datetime import timedelta

from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView

import json

from crm import google, meetings as meetings_svc, services
from crm.forms import (
    CustomerForm, DriveFileForm, GoogleCredentialsForm, MeetingForm, MeetingOutcomeForm,
    MeetingTranscriptForm, MessageForm, NoteForm, OpportunityForm, ProposeTimeForm,
    ReplyForm, ShareForm, UploadForm,
)
from django.contrib.auth import get_user_model
from crm.models import (
    Activity, Customer, FollowUp, GoogleAccount, GoogleFile, Meeting, MeetingTranscript,
    Opportunity, Service, Source,
)
from hello_guy_ai.conf import ai_settings
from hello_guy_ai.exceptions import AssistantNotFound
from hello_guy_ai.models import Message as AIMessage, Thread
from hello_guy_ai.services import chat
from meta_connector.models import Contact, Message as MetaMessage
from meta_connector.roles import ADMIN, AGENT, RoleRequiredMixin, get_role, has_role
from meta_connector.services import SendError, send_message

PAGE_SIZE = 25
OAUTH_SALT = "crm.google.oauth"


class CRMView(RoleRequiredMixin):
    """Role check (RoleRequiredMixin) + what crm/base.html needs."""

    def get_context_data(self, **kwargs):
        user = self.request.user
        return super().get_context_data(
            crm_role=get_role(user), can_edit=has_role(user, AGENT), is_admin=has_role(user, ADMIN),
            inbox_waiting=Thread.objects.filter(needs_human=True).count(), **kwargs)

    def require(self, role: str) -> None:
        if not has_role(self.request.user, role):
            raise PermissionDenied(f"This needs the {role} role.")


class ActionsMixin:
    """POST action=<name> runs post_<name>() (dashes become underscores) and returns its response."""

    def post(self, request, *args, **kwargs):
        if hasattr(self, "get_object"):
            self.object = self.get_object()
        handler = getattr(self, f"post_{request.POST.get('action', '').replace('-', '_')}", None)
        if handler is None:
            return HttpResponseBadRequest("Unknown action")
        try:
            return handler()
        except google.GoogleError as exc:
            messages.error(request, f"Google: {exc}")
            return redirect(request.path)

    def rerender(self, **forms):
        """Show the page again with the invalid form's errors."""
        return self.render_to_response(self.get_context_data(**forms))


# ---- overview --------------------------------------------------------------------------------


class OverviewView(CRMView, TemplateView):
    template_name = "crm/overview.html"

    @staticmethod
    def _delta(curr: int, prev: int) -> int | None:
        if prev == 0:
            return None if curr == 0 else 100
        return round(((curr - prev) / prev) * 100)

    def get_context_data(self, **kwargs):
        now = timezone.now()
        try:
            days = int(self.request.GET.get("days", 30))
        except (TypeError, ValueError):
            days = 30
        if days not in (7, 30, 90):
            days = 30
        since = now - timedelta(days=days)
        prev_since = since - timedelta(days=days)

        opportunities = Opportunity.objects.select_related("customer", "service", "assigned_to")
        by_status = dict(opportunities.values_list("status").annotate(n=Count("id")))

        # Activity KPIs
        calls_now = Activity.objects.filter(kind=Activity.Kind.CALL, created_at__gte=since).count()
        calls_prev = Activity.objects.filter(kind=Activity.Kind.CALL, created_at__gte=prev_since, created_at__lt=since).count()
        meetings_now = Meeting.objects.filter(start_at__gte=since).count()
        meetings_prev = Meeting.objects.filter(start_at__gte=prev_since, start_at__lt=since).count()
        meetings_upcoming = Meeting.objects.filter(status=Meeting.Status.SCHEDULED, start_at__gte=now).count()
        fu_pending = FollowUp.objects.filter(status=FollowUp.Status.PENDING).count()
        fu_overdue = FollowUp.objects.filter(status=FollowUp.Status.PENDING, due_at__lt=now).count()
        fu_upcoming = fu_pending - fu_overdue
        notes_now = Activity.objects.filter(kind=Activity.Kind.NOTE, created_at__gte=since).count()
        notes_prev = Activity.objects.filter(kind=Activity.Kind.NOTE, created_at__gte=prev_since, created_at__lt=since).count()

        # Pipeline KPIs
        contacts_now = Contact.objects.filter(first_seen__gte=since).count()
        contacts_prev = Contact.objects.filter(first_seen__gte=prev_since, first_seen__lt=since).count()
        cust_now = Customer.objects.filter(created_at__gte=since).count()
        cust_prev = Customer.objects.filter(created_at__gte=prev_since, created_at__lt=since).count()
        opps_now = opportunities.filter(created_at__gte=since).count()
        opps_prev = opportunities.filter(created_at__gte=prev_since, created_at__lt=since).count()
        won_now = opportunities.filter(status=Opportunity.Status.WON, updated_at__gte=since).count()
        lost_now = opportunities.filter(status=Opportunity.Status.LOST, updated_at__gte=since).count()
        win_rate = round((won_now / (won_now + lost_now)) * 100) if (won_now + lost_now) > 0 else None

        # Daily Trend (last min(days, 14) days)
        span = min(days, 14)
        day_dates = [(now - timedelta(days=i)).date() for i in range(span - 1, -1, -1)]
        cust_by_day = {d: 0 for d in day_dates}
        opp_by_day = {d: 0 for d in day_dates}
        act_by_day = {d: 0 for d in day_dates}
        trend_start = now - timedelta(days=span - 1)
        for dt in Customer.objects.filter(created_at__date__gte=trend_start.date()).values_list("created_at", flat=True):
            d = timezone.localtime(dt).date()
            if d in cust_by_day:
                cust_by_day[d] += 1
        for dt in Opportunity.objects.filter(created_at__date__gte=trend_start.date()).values_list("created_at", flat=True):
            d = timezone.localtime(dt).date()
            if d in opp_by_day:
                opp_by_day[d] += 1
        for dt in Activity.objects.filter(created_at__date__gte=trend_start.date()).values_list("created_at", flat=True):
            d = timezone.localtime(dt).date()
            if d in act_by_day:
                act_by_day[d] += 1

        daily_trend = {
            "labels": [d.strftime("%d %b") for d in day_dates],
            "customers": [cust_by_day[d] for d in day_dates],
            "opportunities": [opp_by_day[d] for d in day_dates],
            "activities": [act_by_day[d] for d in day_dates],
        }

        # Conversion Funnel
        total_contacts = Contact.objects.count()
        total_customers = Customer.objects.count()
        total_opps = opportunities.count()
        qualified_opps = opportunities.filter(status__in=[
            Opportunity.Status.QUALIFIED, Opportunity.Status.PROPOSAL, Opportunity.Status.WON
        ]).count()
        won_opps = by_status.get(Opportunity.Status.WON, 0)
        funnel_max = max(total_contacts, total_customers, total_opps, 1)
        funnel = [
            {"label": "Inbox Contacts (IG / WA)", "count": total_contacts, "pct": round(total_contacts / funnel_max * 100), "color": "bg-blue-500"},
            {"label": "CRM Customers", "count": total_customers, "pct": round(total_customers / funnel_max * 100), "color": "bg-indigo-500"},
            {"label": "Opportunities", "count": total_opps, "pct": round(total_opps / funnel_max * 100), "color": "bg-violet-500"},
            {"label": "Qualified / Proposal", "count": qualified_opps, "pct": round(qualified_opps / funnel_max * 100), "color": "bg-amber-500"},
            {"label": "Won Deals", "count": won_opps, "pct": round(won_opps / funnel_max * 100), "color": "bg-emerald-500"},
        ]

        # Source Attribution
        source_rows = []
        for src_val, src_lbl in Source.choices:
            c_cnt = Customer.objects.filter(source=src_val).count()
            o_cnt = opportunities.filter(source=src_val).count()
            w_cnt = opportunities.filter(source=src_val, status=Opportunity.Status.WON).count()
            if c_cnt or o_cnt:
                source_rows.append({"label": src_lbl, "customers": c_cnt, "opportunities": o_cnt, "won": w_cnt})

        # Team Leaderboard
        User = get_user_model()
        team_board = []
        for u in User.objects.filter(is_active=True)[:8]:
            acts = Activity.objects.filter(created_by=u, created_at__gte=since).count()
            fu_done = FollowUp.objects.filter(assigned_to=u, status=FollowUp.Status.DONE).count()
            opps_u = opportunities.filter(assigned_to=u).count()
            won_u = opportunities.filter(assigned_to=u, status=Opportunity.Status.WON).count()
            if acts or fu_done or opps_u or u == self.request.user:
                team_board.append({
                    "name": u.get_full_name() or u.username,
                    "is_you": u == self.request.user,
                    "activities": acts,
                    "followups_done": fu_done,
                    "opportunities": opps_u,
                    "won": won_u,
                })

        return super().get_context_data(
            days=days,
            ranges=[7, 30, 90],
            stats={
                "open": opportunities.filter(status__in=Opportunity.OPEN).count(),
                "new_this_week": opportunities.filter(created_at__gte=now - timedelta(days=7)).count(),
                "won_this_month": opportunities.filter(status=Opportunity.Status.WON,
                                                       updated_at__gte=now - timedelta(days=30)).count(),
                "customers": total_customers,
            },
            activity_kpis=[
                {"label": "Calls", "value": calls_now, "delta": self._delta(calls_now, calls_prev), "dot": "bg-blue-500", "sub": "logged in period"},
                {"label": "Meetings", "value": meetings_now, "delta": self._delta(meetings_now, meetings_prev), "dot": "bg-violet-500", "sub": f"{meetings_upcoming} upcoming"},
                {"label": "Follow-ups", "value": fu_pending, "delta": None, "dot": "bg-amber-500", "sub": f"{fu_overdue} overdue · {fu_upcoming} upcoming", "overdue": fu_overdue},
                {"label": "Notes", "value": notes_now, "delta": self._delta(notes_now, notes_prev), "dot": "bg-emerald-500", "sub": "added in period"},
            ],
            pipeline_kpis=[
                {"label": "Contacts (IG/WA)", "value": contacts_now, "delta": self._delta(contacts_now, contacts_prev), "dot": "bg-blue-500"},
                {"label": "New Customers", "value": cust_now, "delta": self._delta(cust_now, cust_prev), "dot": "bg-indigo-500"},
                {"label": "Opportunities", "value": opps_now, "delta": self._delta(opps_now, opps_prev), "dot": "bg-violet-500"},
                {"label": "Win Rate", "value": f"{win_rate}%" if win_rate is not None else "—", "delta": None, "dot": "bg-emerald-500", "sub": f"{won_now} won · {lost_now} lost"},
            ],
            daily_trend=daily_trend,
            funnel=funnel,
            source_rows=source_rows,
            team_board=team_board,
            do_next_followups=FollowUp.objects.filter(status=FollowUp.Status.PENDING).select_related("customer", "opportunity").order_by("due_at")[:6],
            pipeline=[(label, by_status.get(value, 0), value) for value, label in Opportunity.Status.choices],
            recent=opportunities[:8],
            follow_ups=opportunities.filter(status__in=Opportunity.OPEN, next_follow_up__lte=now + timedelta(days=1))
            .order_by("next_follow_up")[:8],
            meetings=Meeting.objects.filter(status=Meeting.Status.SCHEDULED, start_at__gte=now - timedelta(hours=1))
            .select_related("customer")[:6],
            waiting_chats=Thread.objects.filter(needs_human=True).order_by("-updated_at")[:5],
            **kwargs,
        )


# ---- opportunities ---------------------------------------------------------------------------


class OpportunityListView(CRMView, ListView):
    """The pipeline: what every customer is interested in."""
    template_name = "crm/opportunity_list.html"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs, get = Opportunity.objects.select_related("customer", "service", "assigned_to").prefetch_related("services"), self.request.GET
        status, service, q = get.get("status", "open"), get.get("service", ""), get.get("q", "").strip()
        if status == "open":
            qs = qs.filter(status__in=Opportunity.OPEN)
        elif status in Opportunity.Status.values:
            qs = qs.filter(status=status)
        if service.isdigit():
            qs = qs.filter(Q(service_id=service) | Q(services__id=service)).distinct()
        if get.get("mine"):
            qs = qs.filter(assigned_to=self.request.user)
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(reference__iexact=q) | Q(customer__name__icontains=q)
                           | Q(customer__email__icontains=q) | Q(customer__phone__icontains=q)
                           | Q(customer__company__icontains=q))
        self.filters = {"status": status, "service": service, "q": q}
        return qs

    def get_context_data(self, **kwargs):
        view_mode = self.request.GET.get("view", "board")
        kanban_columns = []
        if view_mode == "board":
            all_opps = list(self.object_list[:120])
            by_status = {val: [] for val, _ in Opportunity.Status.choices}
            for opp in all_opps:
                by_status.setdefault(opp.status, []).append(opp)
            kanban_columns = [
                {"value": val, "label": label, "items": by_status.get(val, [])}
                for val, label in Opportunity.Status.choices
            ]
        return super().get_context_data(
            statuses=Opportunity.Status.choices,
            services=Service.objects.all(),
            view_mode=view_mode,
            kanban_columns=kanban_columns,
            **self.filters,
            **kwargs,
        )


class OpportunityStageView(CRMView, View):
    """Quick stage update from the Kanban board (supports drag-and-drop JSON and form POST)."""
    role = AGENT

    def post(self, request, pk: int):
        opp = get_object_or_404(Opportunity.objects.select_related("customer"), pk=pk)
        new_status = request.POST.get("status", "")
        is_json = "application/json" in request.headers.get("Accept", "") or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if new_status not in Opportunity.Status.values:
            if is_json:
                return JsonResponse({"ok": False, "error": "Invalid stage"}, status=400)
            return redirect(request.POST.get("next") or "crm:opportunities")
        if new_status != opp.status:
            services.set_status(opp, new_status, request.user)
            if not is_json:
                messages.success(request, f"Moved '{opp.title}' to {opp.get_status_display()}.")
        if is_json:
            return JsonResponse({"ok": True, "status": opp.status, "label": opp.get_status_display()})
        return redirect(request.POST.get("next") or "crm:opportunities")


class OpportunityCreateView(CRMView, CreateView):
    """Add what a customer is interested in (?customer=<id> preselects the customer)."""
    role = AGENT
    form_class = OpportunityForm
    template_name = "crm/form.html"
    extra_context = {"title": "New opportunity"}

    def get_initial(self):
        return {"customer": self.request.GET.get("customer")}

    def form_valid(self, form):
        opportunity = form.save()
        services.add_note(user=self.request.user, customer=opportunity.customer, opportunity=opportunity,
                          kind=Activity.Kind.SYSTEM, body=f"New opportunity: {opportunity.title}")
        messages.success(self.request, f"Opportunity {opportunity.reference} added.")
        return redirect("crm:opportunity", opportunity.pk)

    def get_context_data(self, **kwargs):
        customer = self.request.GET.get("customer", "")
        back = reverse("crm:customer", args=[customer]) if customer.isdigit() else reverse("crm:opportunities")
        return super().get_context_data(back=back, **kwargs)


class OpportunityDetailView(CRMView, ActionsMixin, DetailView):
    template_name = "crm/opportunity_detail.html"
    queryset = Opportunity.objects.select_related("customer", "service", "assigned_to", "thread").prefetch_related("services")

    def get_context_data(self, **kwargs):
        opp = self.object
        kwargs.setdefault("form", OpportunityForm(instance=opp))
        kwargs.setdefault("note_form", NoteForm())
        return super().get_context_data(
            activities=opp.activities.select_related("created_by")[:50], meetings=opp.meetings.all(),
            transcript=transcript(opp.thread) if opp.thread_id else [], **kwargs)

    def post_update(self):
        old_status = self.object.status
        form = OpportunityForm(self.request.POST, instance=self.object)
        if not form.is_valid():
            return self.rerender(form=form)
        opp = form.save(commit=False)
        new_status, opp.status = opp.status, old_status
        opp.save()
        if new_status != old_status:
            services.set_status(opp, new_status, self.request.user)  # logs the change on the timeline
        messages.success(self.request, "Opportunity saved.")
        return redirect("crm:opportunity", opp.pk)

    def post_note(self):
        form = NoteForm(self.request.POST)
        if not form.is_valid():
            return self.rerender(note_form=form)
        services.add_note(user=self.request.user, customer=self.object.customer, opportunity=self.object,
                          **form.cleaned_data)
        return redirect("crm:opportunity", self.object.pk)

    def post_delete(self):
        self.require(ADMIN)
        customer = self.object.customer
        self.object.delete()
        messages.success(self.request, "Opportunity deleted.")
        return redirect("crm:customer", customer.pk)


# ---- customers -------------------------------------------------------------------------------


class CustomerListView(CRMView, ListView):
    template_name = "crm/customer_list.html"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs = Customer.objects.select_related("owner").annotate(
            open_count=Count("opportunities", filter=Q(opportunities__status__in=Opportunity.OPEN)))
        if q := self.request.GET.get("q", "").strip():
            qs = qs.filter(Q(name__icontains=q) | Q(company__icontains=q) | Q(email__icontains=q)
                           | Q(phone__icontains=q) | Q(instagram_username__icontains=q.lstrip("@")))
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(q=self.request.GET.get("q", "").strip(), **kwargs)


class CustomerCreateView(CRMView, CreateView):
    """New customer by hand, or from an inbox chat (?thread=<id>: prefilled and linked to its WhatsApp /
    Instagram contact, so the team can message them later)."""
    role = AGENT
    form_class = CustomerForm
    template_name = "crm/form.html"
    extra_context = {"title": "New customer"}

    def dispatch(self, request, *args, **kwargs):
        thread_id = request.GET.get("thread") or request.POST.get("thread")
        self.thread = Thread.objects.filter(pk=thread_id).first() if thread_id else None
        self.contact = services.contact_for_thread(self.thread) if self.thread else None
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        c = self.contact
        if c is None:
            return {}
        handle = c.username if c.platform_account.platform == "instagram" else ""
        return {"name": c.name or c.username, "phone": c.phone, "email": c.email, "instagram_username": handle}

    def form_valid(self, form):
        form.instance.source = services.THREAD_SOURCES.get(channel(self.thread), Source.MANUAL) if self.thread \
            else Source.MANUAL
        customer = form.save()
        if self.contact:
            customer.contacts.add(self.contact)
        services.add_note(user=self.request.user, customer=customer, kind=Activity.Kind.SYSTEM,
                          body=f"Customer added ({customer.get_source_display()})")
        return redirect("crm:customer", customer.pk)

    def get_context_data(self, **kwargs):
        return super().get_context_data(back=reverse("crm:customers"),
                                        hidden={"thread": self.thread.pk} if self.thread else {}, **kwargs)


class CustomerDetailView(CRMView, ActionsMixin, DetailView):
    template_name = "crm/customer_detail.html"
    queryset = Customer.objects.select_related("owner")

    def get_context_data(self, **kwargs):
        customer = self.object
        kwargs.setdefault("form", CustomerForm(instance=customer))
        kwargs.setdefault("note_form", NoteForm())
        contacts = list(customer.contacts.select_related("platform_account"))
        channel_messages = (
            MetaMessage.objects.filter(contact__in=contacts)
            .select_related("contact", "platform_account", "sent_by")
            .order_by("-created_at")[:30]
            if contacts else []
        )
        return super().get_context_data(
            opportunities=customer.opportunities.select_related("service"),
            contacts=contacts,
            channel_messages=channel_messages,
            follow_ups=customer.follow_ups.select_related("opportunity", "assigned_to").order_by("status", "due_at")[:15],
            activities=customer.activities.select_related("created_by")[:50],
            meetings=customer.meetings.all(), files=customer.google_files.all(),
            drive_form=DriveFileForm(), upload_form=UploadForm(), share_form=ShareForm(initial={"target": "folder"}),
            message_form=MessageForm(customer=customer), google_connected=GoogleAccount.get_solo().is_connected,
            **kwargs)

    def done(self, text: str = ""):
        if text:
            messages.success(self.request, text)
        return redirect("crm:customer", self.object.pk)

    def post_update(self):
        form = CustomerForm(self.request.POST, instance=self.object)
        if not form.is_valid():
            return self.rerender(form=form)
        form.save()
        return self.done("Customer saved.")

    def post_note(self):
        form = NoteForm(self.request.POST)
        if not form.is_valid():
            return self.rerender(note_form=form)
        services.add_note(user=self.request.user, customer=self.object, **form.cleaned_data)
        return self.done()

    def post_message(self):
        """WhatsApp / Instagram / Messenger message to one of the customer's inbox contacts."""
        form = MessageForm(self.request.POST, customer=self.object)
        if not form.is_valid():
            messages.error(self.request, "Pick a contact and write a message (up to 1000 characters).")
            return self.done()
        try:
            send_message(form.cleaned_data["contact"], form.cleaned_data["text"], sent_by=self.request.user)
        except SendError as exc:
            messages.error(self.request, f"Not delivered: {exc}")
            return self.done()
        services.add_note(user=self.request.user, customer=self.object, kind=Activity.Kind.MESSAGE,
                          body=form.cleaned_data["text"])
        return self.done("Message sent.")

    def post_drive_folder(self):
        google.customer_folder(self.object)
        return self.done("Google Drive folder ready.")

    def post_drive_create(self):
        form = DriveFileForm(self.request.POST)
        if not form.is_valid():
            messages.error(self.request, "Pick a type and a name.")
            return self.done()
        gfile = google.create_file(self.object, form.cleaned_data["kind"], form.cleaned_data["name"], self.request.user)
        return self.done(f"Created {gfile.name}.")

    def post_drive_upload(self):
        form = UploadForm(self.request.POST, self.request.FILES)
        if not form.is_valid():
            messages.error(self.request, "; ".join(e for errors in form.errors.values() for e in errors))
            return self.done()
        gfile = google.upload_file(self.object, form.cleaned_data["file"], self.request.user)
        return self.done(f"Uploaded {gfile.name}.")

    def post_drive_delete(self):
        self.require(ADMIN)
        gfile = get_object_or_404(GoogleFile, pk=self.request.POST.get("file"), customer=self.object)
        google.delete_file(gfile)
        services.add_note(user=self.request.user, customer=self.object, kind=Activity.Kind.SYSTEM,
                          body=f"Moved {gfile.get_kind_display()} \"{gfile.name}\" to Google Drive trash")
        return self.done(f"Deleted {gfile.name} (it is in Google Drive's trash for 30 days).")

    def post_share(self):
        form = ShareForm(self.request.POST)
        if not form.is_valid():
            messages.error(self.request, "Enter a valid email.")
            return self.done()
        email, role = form.cleaned_data["email"], form.cleaned_data["role"]
        if form.cleaned_data["target"] == "folder":
            google.share(google.customer_folder(self.object), email, role)
            services.add_note(user=self.request.user, customer=self.object, kind=Activity.Kind.SYSTEM,
                              body=f"Shared the Drive folder with {email} ({role})")
        else:
            google.share_file(get_object_or_404(GoogleFile, pk=form.cleaned_data["target"], customer=self.object),
                              email, role)
        return self.done(f"Shared with {email}.")

    def post_delete(self):
        self.require(ADMIN)
        self.object.delete()
        messages.success(self.request, "Customer deleted (their Google files stay in Drive).")
        return redirect("crm:customers")


# ---- live chat inbox -------------------------------------------------------------------------


def chat_threads():
    """Website visitor chats and WhatsApp/Instagram/Messenger chats of the public bot."""
    try:
        bot = chat.public_user()
    except AssistantNotFound:
        return Thread.objects.none()
    return Thread.objects.filter(created_by=bot, assistant_id=ai_settings.PUBLIC_AGENT).exclude(external_id="")


def transcript(thread: Thread) -> list[dict]:
    """What the visitor and the bot/team said (tool rows and empty tool-call turns left out)."""
    rows = thread.messages.filter(role__in=[AIMessage.Role.USER, AIMessage.Role.ASSISTANT]).exclude(content="")
    return [
        {"id": m.pk, "who": "visitor" if m.role == AIMessage.Role.USER else
         ("staff" if m.name.startswith(chat.STAFF_NAME_PREFIX) else "ai"),
         "name": m.name.removeprefix(chat.STAFF_NAME_PREFIX), "text": m.content, "at": m.created_at}
        for m in rows.order_by("id")[:500]
    ]


def channel(thread: Thread) -> str:
    return (thread.external_id or "").split(":", 1)[0]


class InboxView(CRMView, ListView):
    template_name = "crm/inbox.html"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        show, qs = self.request.GET.get("show", "waiting"), chat_threads().select_related("handled_by")
        if show == "waiting":
            qs = qs.filter(needs_human=True)
        elif show == "mine":
            qs = qs.filter(human_mode=True, handled_by=self.request.user)
        elif show == "live":
            qs = qs.filter(human_mode=True)
        return qs.order_by("-needs_human", "-updated_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(show=self.request.GET.get("show", "waiting"), **kwargs)
        for thread in context["page_obj"]:  # one page (25 rows): a query each is fine
            thread.channel = channel(thread)
            thread.last = thread.messages.filter(role__in=["user", "assistant"]).exclude(content="").order_by("-id").first()
        return context


class InboxThreadView(CRMView, ActionsMixin, DetailView):
    """One chat: read it live (the page polls ?format=json&after=<id>), take it over, reply, hand back."""
    template_name = "crm/inbox_thread.html"
    pk_url_kwarg = "thread_id"
    context_object_name = "thread"

    def get_queryset(self):
        return chat_threads().select_related("handled_by")

    def get(self, request, *args, **kwargs):
        if request.GET.get("format") != "json":
            return super().get(request, *args, **kwargs)
        thread, after = self.get_object(), int(request.GET.get("after") or 0)
        rows = [dict(m, at=m["at"].isoformat()) for m in transcript(thread) if m["id"] > after]
        return JsonResponse({"messages": rows, "human": thread.human_mode, "waiting": thread.needs_human})

    def get_context_data(self, **kwargs):
        thread = self.object
        contact = services.contact_for_thread(thread)
        opportunity = Opportunity.objects.select_related("customer").filter(thread=thread).first()
        customer = opportunity.customer if opportunity else services.match_customer(contact=contact)
        return super().get_context_data(
            channel=channel(thread), contact=contact, customer=customer, opportunity=opportunity,
            transcript=transcript(thread), reply_form=ReplyForm(), **kwargs)

    def done(self):
        if self.request.headers.get("Accept") == "application/json":
            return JsonResponse({"ok": True})
        return redirect("crm:inbox-thread", self.object.pk)

    def post_take(self):
        chat.take_over(self.object, self.request.user)
        return self.done()

    def post_release(self):
        chat.hand_back(self.object)
        messages.success(self.request, "The AI answers this chat again.")
        return self.done()

    def post_reply(self):
        """Website chats: pushed live to the visitor's widget. WhatsApp/Instagram: sent through Meta first."""
        form = ReplyForm(self.request.POST)
        if not form.is_valid():
            messages.error(self.request, "Write a reply (up to 2000 characters).")
            return self.done()
        text = form.cleaned_data["content"]
        if contact := services.contact_for_thread(self.object):
            try:
                send_message(contact, text, sent_by=self.request.user)
            except SendError as exc:
                messages.error(self.request, f"Not delivered: {exc}")
                return self.done()
        chat.post_staff_reply(self.object, self.request.user, text)
        return self.done()


# ---- settings + Google OAuth -----------------------------------------------------------------


def redirect_uri(request) -> str:
    return request.build_absolute_uri(reverse("crm:google-callback"))


class SettingsView(CRMView, ActionsMixin, TemplateView):
    role = edit_role = ADMIN
    template_name = "crm/settings.html"

    def get_context_data(self, **kwargs):
        account = GoogleAccount.get_solo()
        kwargs.setdefault("form", GoogleCredentialsForm(instance=account))
        return super().get_context_data(
            account=account,
            redirect_uri=redirect_uri(self.request),
            scopes=google.SCOPES,
            **kwargs,
        )

    def post_credentials(self):
        form = GoogleCredentialsForm(self.request.POST, instance=GoogleAccount.get_solo())
        if not form.is_valid():
            return self.rerender(form=form)
        form.save()
        messages.success(self.request, "Google credentials saved.")
        return redirect("crm:settings")

    def post_disconnect(self):
        google.disconnect(GoogleAccount.get_solo())
        messages.success(self.request, "Google disconnected.")
        return redirect("crm:settings")


class GoogleConnectView(CRMView, View):
    """Send the admin to Google's consent screen. The signed, single-use state and the PKCE verifier stay in
    this session, so a forged callback cannot attach someone else's Google account."""
    role = edit_role = ADMIN
    http_method_names = ["post"]

    def post(self, request):
        nonce = get_random_string(32)
        state = signing.dumps({"nonce": nonce, "user": request.user.pk}, salt=OAUTH_SALT)
        try:
            url, verifier = google.auth_url(GoogleAccount.get_solo(), redirect_uri(request), state)
        except google.GoogleError as exc:
            messages.error(request, str(exc))
            return redirect("crm:settings")
        request.session["crm_google_oauth"] = {"nonce": nonce, "verifier": verifier}
        return redirect(url)


class GoogleCallbackView(CRMView, View):
    role = ADMIN

    def get(self, request):
        saved = request.session.pop("crm_google_oauth", None) or {}
        try:
            state = signing.loads(request.GET.get("state", ""), salt=OAUTH_SALT, max_age=600)
        except signing.BadSignature:
            state = {}
        if not saved.get("nonce") or state.get("nonce") != saved["nonce"] or state.get("user") != request.user.pk:
            raise Http404  # stale, replayed or forged
        if error := request.GET.get("error"):
            messages.error(request, f"Google sign-in was cancelled ({error}).")
            return redirect("crm:settings")
        try:
            account = google.exchange_code(GoogleAccount.get_solo(), request.GET.get("code", ""),
                                           redirect_uri(request), saved["verifier"])
        except google.GoogleError as exc:
            messages.error(request, f"Google: {exc}")
            return redirect("crm:settings")
        messages.success(request, f"Connected Google account {account.email}.")
        return redirect("crm:settings")


# ---- follow-up scheduler ---------------------------------------------------------------------


class FollowUpListView(CRMView, ListView):
    """Scheduled follow-ups: overdue, due today, upcoming, and completed."""
    template_name = "crm/follow_ups.html"
    context_object_name = "follow_ups"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        status = self.request.GET.get("status", FollowUp.Status.PENDING)
        qs = FollowUp.objects.select_related("customer", "opportunity", "assigned_to")
        if status in FollowUp.Status.values:
            qs = qs.filter(status=status)
        if self.request.GET.get("mine"):
            qs = qs.filter(assigned_to=self.request.user)
        return qs.order_by("due_at" if status == FollowUp.Status.PENDING else "-updated_at")

    def get_context_data(self, **kwargs):
        now = timezone.now()
        return super().get_context_data(
            status_filter=self.request.GET.get("status", FollowUp.Status.PENDING),
            now=now,
            overdue_count=FollowUp.objects.filter(status=FollowUp.Status.PENDING, due_at__lt=now).count(),
            customers=Customer.objects.order_by("name")[:100],
            channels=FollowUp.Channel.choices,
            **kwargs,
        )


class FollowUpCreateView(CRMView, View):
    role = AGENT

    def post(self, request):
        customer = get_object_or_404(Customer, pk=request.POST.get("customer_id"))
        opp_id = request.POST.get("opportunity_id")
        opportunity = Opportunity.objects.filter(pk=opp_id, customer=customer).first() if opp_id else None
        due_raw = request.POST.get("due_at", "").strip()
        note = request.POST.get("note", "").strip()
        channel = request.POST.get("channel", FollowUp.Channel.WHATSAPP)
        if channel not in FollowUp.Channel.values:
            channel = FollowUp.Channel.WHATSAPP
        try:
            due_at = timezone.datetime.fromisoformat(due_raw)
            if timezone.is_naive(due_at):
                due_at = timezone.make_aware(due_at, timezone.get_current_timezone())
        except ValueError:
            due_at = timezone.now() + timedelta(days=1)
        services.schedule_follow_up(
            customer=customer,
            opportunity=opportunity,
            due_at=due_at,
            channel=channel,
            note=note,
            created_by=request.user,
        )
        messages.success(request, f"Follow-up scheduled for {customer.name}.")
        return redirect(request.POST.get("next") or "crm:follow-ups")


class FollowUpActionView(CRMView, View):
    role = AGENT

    def post(self, request, pk: int):
        fu = get_object_or_404(FollowUp.objects.select_related("customer", "opportunity"), pk=pk)
        action = request.POST.get("action")
        next_url = request.POST.get("next") or "crm:follow-ups"
        if action == "done":
            services.complete_follow_up(fu, user=request.user)
            messages.success(request, "Follow-up marked done.")
        elif action == "cancel":
            fu.status = FollowUp.Status.CANCELLED
            fu.save(update_fields=["status", "updated_at"])
            messages.info(request, "Follow-up cancelled.")
        elif action == "snooze":
            hours = int(request.POST.get("hours", 24) or 24)
            fu.due_at = timezone.now() + timedelta(hours=max(1, min(hours, 24 * 30)))
            fu.save(update_fields=["due_at", "updated_at"])
            messages.success(request, "Follow-up rescheduled.")
        return redirect(next_url)


# ---- meetings & transcripts ------------------------------------------------------------------


class MeetingListView(CRMView, ListView):
    """Meetings hub: upcoming, past, proposed-time requests, and stored transcripts table."""
    template_name = "crm/meetings.html"
    context_object_name = "meetings"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        tab = self.request.GET.get("tab", "upcoming")
        q = self.request.GET.get("q", "").strip()
        now = timezone.now()
        qs = Meeting.objects.select_related("customer", "opportunity", "organizer").prefetch_related("transcripts")

        if tab == "upcoming":
            qs = qs.filter(status=Meeting.Status.SCHEDULED, end_at__gte=now - timedelta(hours=2)).order_by("start_at")
        elif tab == "past":
            qs = qs.filter(Q(status=Meeting.Status.DONE) | Q(end_at__lt=now)).exclude(status=Meeting.Status.CANCELLED).order_by("-start_at")
        elif tab == "cancelled":
            qs = qs.filter(status=Meeting.Status.CANCELLED).order_by("-start_at")
        elif tab == "proposals":
            ids = [m.pk for m in qs.filter(status=Meeting.Status.SCHEDULED) if m.pending_proposals]
            qs = qs.filter(pk__in=ids).order_by("start_at")
        elif tab == "transcripts":
            qs = qs.filter(transcripts__isnull=False).distinct().order_by("-start_at")
        else:
            qs = qs.order_by("-start_at")

        if q:
            qs = qs.filter(
                Q(title__icontains=q)
                | Q(description__icontains=q)
                | Q(customer__name__icontains=q)
                | Q(attendees__icontains=q)
                | Q(transcripts__raw_text__icontains=q)
            ).distinct()
        return qs

    def get_context_data(self, **kwargs):
        now = timezone.now()
        tab = self.request.GET.get("tab", "upcoming")
        q = self.request.GET.get("q", "").strip()
        all_scheduled = list(Meeting.objects.filter(status=Meeting.Status.SCHEDULED))
        proposals_count = sum(1 for m in all_scheduled if m.pending_proposals)
        today_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        transcripts_qs = MeetingTranscript.objects.select_related("meeting", "meeting__customer", "created_by")
        if q:
            transcripts_qs = transcripts_qs.filter(
                Q(title__icontains=q)
                | Q(raw_text__icontains=q)
                | Q(summary__icontains=q)
                | Q(meeting__title__icontains=q)
                | Q(meeting__customer__name__icontains=q)
            )

        default_start = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        quick_form = MeetingForm(initial={
            "start_at": timezone.localtime(default_start).strftime("%Y-%m-%dT%H:%M"),
            "end_at": timezone.localtime(default_start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
        })
        google_account = GoogleAccount.get_solo()

        return super().get_context_data(
            tab=tab,
            q=q,
            google_account=google_account,
            google_connected=google_account.is_connected,
            quick_form=quick_form,
            transcript_rows=transcripts_qs[:100],
            stats={
                "upcoming": Meeting.objects.filter(status=Meeting.Status.SCHEDULED, end_at__gte=now - timedelta(hours=2)).count(),
                "today": Meeting.objects.filter(status=Meeting.Status.SCHEDULED, start_at__gte=today_start, start_at__lt=today_end).count(),
                "proposals": proposals_count,
                "transcripts": MeetingTranscript.objects.count(),
                "synced": Meeting.objects.exclude(google_event_id="").count(),
            },
            **kwargs,
        )


class MeetingCreateView(CRMView, CreateView):
    """Schedule a new CRM meeting (with optional Google Calendar + Meet sync)."""
    role = AGENT
    form_class = MeetingForm
    template_name = "crm/form.html"
    extra_context = {"title": "Schedule meeting"}

    def get_initial(self):
        initial = super().get_initial()
        now = timezone.now()
        default_start = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        initial["start_at"] = timezone.localtime(default_start).strftime("%Y-%m-%dT%H:%M")
        initial["end_at"] = timezone.localtime(default_start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        if cid := self.request.GET.get("customer"):
            customer = Customer.objects.filter(pk=cid).first()
            if customer:
                initial["customer"] = customer
                if customer.email:
                    initial["attendees"] = [customer.email]
        if oid := self.request.GET.get("opportunity"):
            opp = Opportunity.objects.select_related("customer").filter(pk=oid).first()
            if opp:
                initial["opportunity"] = opp
                initial["customer"] = opp.customer
                if opp.customer.email and "attendees" not in initial:
                    initial["attendees"] = [opp.customer.email]
        return initial

    def form_valid(self, form):
        form.instance.organizer = self.request.user
        meeting = form.save()
        if GoogleAccount.get_solo().is_connected:
            google.sync_meeting(meeting)
            if meeting.sync_error:
                messages.warning(self.request, f"Meeting saved in CRM, but Google sync failed: {meeting.sync_error}")
            else:
                messages.success(self.request, "Meeting scheduled and synced to Google Calendar.")
        else:
            messages.success(self.request, "Meeting scheduled in CRM.")

        if meeting.customer_id:
            services.add_note(
                user=self.request.user,
                customer=meeting.customer,
                opportunity=meeting.opportunity,
                kind=Activity.Kind.MEETING,
                body=f"Scheduled meeting \"{meeting.title}\" for {timezone.localtime(meeting.start_at):%d %b %Y, %H:%M}.",
            )
        return redirect("crm:meeting", meeting.pk)

    def get_context_data(self, **kwargs):
        return super().get_context_data(back=reverse("crm:meetings"), **kwargs)


class MeetingSyncView(CRMView, View):
    """Pull & import upcoming meetings and RSVP updates from Google Calendar."""
    role = AGENT
    http_method_names = ["post"]

    def post(self, request):
        next_url = request.POST.get("next") or reverse("crm:meetings")
        if not GoogleAccount.get_solo().is_connected:
            messages.error(request, "Google is not connected. Connect it in CRM > Settings first.")
            return redirect(next_url)
        try:
            result = google.import_calendar_events()
        except google.GoogleError as exc:
            messages.error(request, f"Google Calendar sync failed: {exc}")
            return redirect(next_url)
        messages.success(
            request,
            f"Synced with Google Calendar: {result['created']} imported, {result['updated']} updated.",
        )
        return redirect(next_url)


class MeetingDetailView(CRMView, ActionsMixin, DetailView):
    """Single meeting: Google Meet/Calendar link, RSVP breakdown, proposed times, and stored transcripts."""
    template_name = "crm/meeting_detail.html"
    context_object_name = "meeting"
    queryset = Meeting.objects.select_related("customer", "opportunity", "organizer").prefetch_related(
        "transcripts__created_by"
    )

    def get_context_data(self, **kwargs):
        m = self.object
        kwargs.setdefault("form", MeetingForm(instance=m))
        kwargs.setdefault("outcome_form", MeetingOutcomeForm(initial={"outcome": m.outcome}))
        kwargs.setdefault("propose_form", ProposeTimeForm())
        kwargs.setdefault("transcript_form", MeetingTranscriptForm())
        return super().get_context_data(
            guests=m.detailed_guests,
            rsvp=m.rsvp_summary,
            proposals=m.proposed_times or [],
            pending_proposals=m.pending_proposals,
            transcripts=m.transcripts.all(),
            google_connected=GoogleAccount.get_solo().is_connected,
            **kwargs,
        )

    def done(self, text: str = ""):
        if text:
            messages.success(self.request, text)
        return redirect("crm:meeting", self.object.pk)

    def post_update(self):
        form = MeetingForm(self.request.POST, instance=self.object)
        if not form.is_valid():
            return self.rerender(form=form)
        meeting = form.save()
        if GoogleAccount.get_solo().is_connected:
            google.sync_meeting(meeting)
            if meeting.sync_error:
                messages.warning(self.request, f"Updated in CRM, but Google sync failed: {meeting.sync_error}")
                return self.done()
        return self.done("Meeting updated.")

    def post_sync(self):
        if not GoogleAccount.get_solo().is_connected:
            messages.error(self.request, "Google is not connected.")
            return self.done()
        if self.object.google_event_id:
            changes = google.pull_meeting(self.object)
            msg = f"Synced from Google: {', '.join(changes)}." if changes else "Already up to date with Google Calendar."
            return self.done(msg)
        google.sync_meeting(self.object)
        if self.object.sync_error:
            messages.error(self.request, f"Google sync failed: {self.object.sync_error}")
            return self.done()
        return self.done("Pushed to Google Calendar.")

    def post_complete(self):
        form = MeetingOutcomeForm(self.request.POST)
        if form.is_valid():
            self.object.outcome = form.cleaned_data["outcome"]
        self.object.status = Meeting.Status.DONE
        self.object.save(update_fields=["status", "outcome", "updated_at"])
        if self.object.customer_id:
            services.add_note(
                user=self.request.user,
                customer=self.object.customer,
                opportunity=self.object.opportunity,
                kind=Activity.Kind.MEETING,
                body=f"Completed meeting \"{self.object.title}\".{f' Outcome: {self.object.outcome}' if self.object.outcome else ''}",
            )
        return self.done("Meeting marked as completed.")

    def post_cancel(self):
        self.object.status = Meeting.Status.CANCELLED
        self.object.save(update_fields=["status", "updated_at"])
        if GoogleAccount.get_solo().is_connected and self.object.google_event_id:
            google.sync_meeting(self.object)
        if self.object.customer_id:
            services.add_note(
                user=self.request.user,
                customer=self.object.customer,
                opportunity=self.object.opportunity,
                kind=Activity.Kind.MEETING,
                body=f"Cancelled meeting \"{self.object.title}\".",
            )
        return self.done("Meeting cancelled.")

    def post_propose_time(self):
        form = ProposeTimeForm(self.request.POST)
        if not form.is_valid():
            return self.rerender(propose_form=form)
        meetings_svc.propose_meeting_time(
            self.object,
            email=form.cleaned_data["email"],
            name=form.cleaned_data["name"],
            proposed_start=form.cleaned_data["proposed_start"],
            proposed_end=form.cleaned_data["proposed_end"],
            note=form.cleaned_data["note"],
            user=self.request.user,
        )
        return self.done("Proposed time recorded.")

    def post_resolve_proposal(self):
        proposal_id = self.request.POST.get("proposal_id", "").strip()
        decision = self.request.POST.get("decision", "").strip()
        try:
            meetings_svc.resolve_proposed_time(self.object, proposal_id, decision=decision, user=self.request.user)
        except ValueError as exc:
            messages.error(self.request, str(exc))
            return self.done()
        label = "accepted and rescheduled" if decision == "accepted" else "declined"
        return self.done(f"Proposed time {label}.")

    def post_add_transcript(self):
        form = MeetingTranscriptForm(self.request.POST, self.request.FILES)
        if not form.is_valid():
            return self.rerender(transcript_form=form)
        meetings_svc.create_transcript(
            self.object,
            form.cleaned_data["raw_text"],
            title=form.cleaned_data["title"],
            source=form.cleaned_data["source"],
            summary=form.cleaned_data["summary"],
            language=form.cleaned_data["language"] or "en",
            user=self.request.user,
        )
        return self.done("Meeting transcript saved.")

    def post_save_transcript_doc(self):
        if not self.object.customer_id:
            messages.error(self.request, "Link a customer to this meeting first to save to their Google Drive folder.")
            return self.done()
        transcript = get_object_or_404(MeetingTranscript, pk=self.request.POST.get("transcript_id"), meeting=self.object)
        content_parts = [f"# {transcript.title or self.object.title}"]
        if transcript.summary:
            content_parts.append(f"\n## Summary\n{transcript.summary}")
        content_parts.append(f"\n## Transcript\n{transcript.raw_text}")
        gfile = google.upload_text_as_doc(
            self.object.customer,
            transcript.title or f"{self.object.title} Transcript",
            "\n".join(content_parts),
            user=self.request.user,
        )
        transcript.google_doc_id = gfile.google_id
        transcript.google_doc_url = gfile.url
        transcript.save(update_fields=["google_doc_id", "google_doc_url", "updated_at"])
        return self.done("Transcript saved to Google Drive.")

    def post_delete_transcript(self):
        self.require(ADMIN)
        transcript = get_object_or_404(MeetingTranscript, pk=self.request.POST.get("transcript_id"), meeting=self.object)
        transcript.delete()
        return self.done("Transcript deleted.")

    def post_delete(self):
        self.require(ADMIN)
        self.object.delete()
        messages.success(self.request, "Meeting deleted.")
        return redirect("crm:meetings")


class MeetingTranscriptAPIView(CRMView, View):
    """JSON endpoint to store a meeting transcript at the end of a call/meeting."""
    role = AGENT
    http_method_names = ["post"]

    def post(self, request, pk: int):
        meeting = get_object_or_404(Meeting, pk=pk)
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        raw_text = (payload.get("raw_text") or "").strip()
        if not raw_text:
            return JsonResponse({"error": "raw_text is required."}, status=400)

        source = payload.get("source") or MeetingTranscript.Source.AI_AGENT
        if source not in MeetingTranscript.Source.values:
            source = MeetingTranscript.Source.MANUAL

        transcript = meetings_svc.create_transcript(
            meeting,
            raw_text,
            title=(payload.get("title") or "").strip(),
            source=source,
            segments=payload.get("segments") if isinstance(payload.get("segments"), list) else None,
            summary=(payload.get("summary") or "").strip(),
            action_items=payload.get("action_items") if isinstance(payload.get("action_items"), list) else None,
            key_decisions=payload.get("key_decisions") if isinstance(payload.get("key_decisions"), list) else None,
            language=(payload.get("language") or "en")[:20],
            duration_seconds=payload.get("duration_seconds"),
            user=request.user,
        )
        return JsonResponse(
            {
                "id": transcript.pk,
                "meeting_id": meeting.pk,
                "word_count": transcript.word_count,
                "segments_count": len(transcript.segments or []),
            },
            status=201,
        )
