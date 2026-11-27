"""CRM dashboard (/crm/). Roles from meta_connector.roles: Viewer reads, Agent works customers/opportunities/chats,
Admin manages settings and deletes. Detail pages take POSTs with an `action` field; each runs `post_<action>()`."""

from decimal import Decimal
import logging
from datetime import timedelta

from django.contrib import messages
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.dateparse import parse_datetime
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView

from core.models import FollowUp
from core.safety import ActionsMixin, int_or, json_body, safe_next
from crm import analytics, email_service, google, meetings as meetings_svc, services
from crm.forms import (
    CustomerForm, DriveFileForm, GoogleCredentialsForm, MeetingForm, MeetingOutcomeForm,
    MeetingTranscriptForm, MessageForm, NoteForm, OpportunityForm, PaymentMilestoneForm,
    ProposalForm, ProposeTimeForm, ReplyForm, SendEmailForm, ShareForm, UploadForm,
)
from crm.models import (
    Activity, Customer, GoogleAccount, GoogleFile, Meeting, MeetingTranscript,
    Opportunity, PaymentMilestone, Proposal, Service, Source,
)
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from hello_guy_ai.conf import ai_settings
from hello_guy_ai.exceptions import AssistantNotFound
from hello_guy_ai.models import Message as AIMessage, Thread
from hello_guy_ai.services import chat
from meta_connector.models import Contact, Message as MetaMessage
from meta_connector.roles import ADMIN, AGENT, HasRole, RoleRequiredMixin, get_role, has_role
from meta_connector.services import SendError, send_message

logger = logging.getLogger(__name__)

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


class CRMActionsMixin(ActionsMixin):
    """ActionsMixin that turns GoogleError into a flash message on the same page."""

    action_exceptions = (google.GoogleError,)

    def handle_action_error(self, request, exc: Exception):
        messages.error(request, f"Google: {exc}")
        return redirect(request.path)


# ---- overview --------------------------------------------------------------------------------


class OverviewView(CRMView, TemplateView):
    template_name = "crm/overview.html"

    def get_context_data(self, **kwargs):
        days = int_or(self.request.GET.get("days"), 30)
        return super().get_context_data(**analytics.overview_analytics(self.request.user, days), **kwargs)


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
                {
                    "value": val,
                    "label": label,
                    "items": by_status.get(val, []),
                    "total_value": sum((o.effective_value for o in by_status.get(val, [])), Decimal("0.00")),
                }
                for val, label in Opportunity.Status.choices
            ]
        return super().get_context_data(
            statuses=Opportunity.Status.choices,
            services=Service.objects.all(),
            view_mode=view_mode,
            kanban_columns=kanban_columns,
            now=timezone.now(),
            stale_cutoff=timezone.now() - timedelta(days=7),
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
            return redirect(safe_next(request, "crm:opportunities"))
        if new_status != opp.status:
            services.set_status(opp, new_status, request.user)
            if not is_json:
                messages.success(request, f"Moved '{opp.title}' to {opp.get_status_display()}.")
        if is_json:
            return JsonResponse({"ok": True, "status": opp.status, "label": opp.get_status_display()})
        return redirect(safe_next(request, "crm:opportunities"))


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


class OpportunityDetailView(CRMView, CRMActionsMixin, DetailView):
    template_name = "crm/opportunity_detail.html"
    queryset = Opportunity.objects.select_related("customer", "service", "assigned_to", "thread").prefetch_related("services")

    def get_context_data(self, **kwargs):
        opp = self.object
        kwargs.setdefault("form", OpportunityForm(instance=opp))
        kwargs.setdefault("note_form", NoteForm())
        kwargs.setdefault("proposal_form", ProposalForm(initial={"currency": opp.currency, "amount": opp.estimated_value or None}))
        kwargs.setdefault("milestone_form", PaymentMilestoneForm(initial={"currency": opp.currency}))
        kwargs.setdefault("email_form", SendEmailForm(initial={"to_email": opp.customer.email, "subject": f"Re: {opp.title}"}))
        milestones = list(opp.payment_milestones.all())
        paid_total = sum((m.amount for m in milestones if m.status == PaymentMilestone.Status.PAID), Decimal("0.00"))
        pending_total = sum(
            (m.amount for m in milestones if m.status in (PaymentMilestone.Status.PENDING, PaymentMilestone.Status.INVOICED)),
            Decimal("0.00"),
        )
        return super().get_context_data(
            activities=opp.activities.select_related("created_by")[:50],
            meetings=opp.meetings.all(),
            proposals=opp.proposals.all(),
            milestones=milestones,
            milestone_paid_total=paid_total,
            milestone_pending_total=pending_total,
            emails=opp.emails.select_related("sent_by")[:25],
            pms_projects=opp.pms_projects.all(),
            google_connected=GoogleAccount.get_solo().is_connected,
            transcript=transcript(opp.thread) if opp.thread_id else [],
            **kwargs,
        )

    def post_update(self):
        old_status = self.object.status
        form = OpportunityForm(self.request.POST, instance=self.object)
        if not form.is_valid():
            return self.rerender(form=form)
        opp = form.save(commit=False)
        new_status, opp.status = opp.status, old_status
        opp.save()
        form.save_m2m()  # the services checkboxes (OpportunityForm.save() only sets them when commit=True)
        if new_status != old_status:
            services.set_status(opp, new_status, self.request.user, lost_reason=opp.lost_reason, won_value=opp.won_value)
        messages.success(self.request, "Opportunity saved.")
        return redirect("crm:opportunity", opp.pk)

    def post_note(self):
        form = NoteForm(self.request.POST)
        if not form.is_valid():
            return self.rerender(note_form=form)
        services.add_note(user=self.request.user, customer=self.object.customer, opportunity=self.object,
                          **form.cleaned_data)
        return redirect("crm:opportunity", self.object.pk)

    def post_convert_project(self):
        project = services.convert_opportunity_to_project(self.object, user=self.request.user)
        messages.success(self.request, f"Project '{project.name}' is ready in PMS.")
        return redirect("pms:project", project.pk)

    def post_proposal_add(self):
        form = ProposalForm(self.request.POST)
        if not form.is_valid():
            return self.rerender(proposal_form=form)
        prop = services.add_proposal(
            opportunity=self.object,
            title=form.cleaned_data["title"],
            amount=form.cleaned_data["amount"],
            currency=form.cleaned_data["currency"],
            valid_until=form.cleaned_data["valid_until"],
            document_url=form.cleaned_data["document_url"],
            notes=form.cleaned_data["notes"],
            mark_sent=bool(self.request.POST.get("mark_sent")),
            user=self.request.user,
        )
        messages.success(self.request, f"Proposal '{prop.title}' saved.")
        return redirect("crm:opportunity", self.object.pk)

    def post_proposal_status(self):
        prop = get_object_or_404(Proposal, pk=int_or(self.request.POST.get("proposal_id"), 0), opportunity=self.object)
        status = self.request.POST.get("status", "")
        if status in Proposal.Status.values:
            services.update_proposal_status(prop, status, user=self.request.user)
            messages.success(self.request, f"Proposal marked {prop.get_status_display()}.")
        return redirect("crm:opportunity", self.object.pk)

    def post_milestone_add(self):
        form = PaymentMilestoneForm(self.request.POST)
        if not form.is_valid():
            return self.rerender(milestone_form=form)
        ms = services.add_payment_milestone(
            opportunity=self.object,
            title=form.cleaned_data["title"],
            amount=form.cleaned_data["amount"],
            currency=form.cleaned_data["currency"],
            due_date=form.cleaned_data["due_date"],
            invoice_number=form.cleaned_data["invoice_number"],
            status=form.cleaned_data["status"],
            user=self.request.user,
        )
        messages.success(self.request, f"Payment milestone '{ms.title}' added.")
        return redirect("crm:opportunity", self.object.pk)

    def post_milestone_status(self):
        ms = get_object_or_404(PaymentMilestone, pk=int_or(self.request.POST.get("milestone_id"), 0), opportunity=self.object)
        status = self.request.POST.get("status", "")
        if status in PaymentMilestone.Status.values:
            services.update_milestone_status(ms, status, user=self.request.user)
            messages.success(self.request, f"Milestone marked {ms.get_status_display()}.")
        return redirect("crm:opportunity", self.object.pk)

    def post_send_email(self):
        form = SendEmailForm(self.request.POST)
        if not form.is_valid():
            return self.rerender(email_form=form)
        try:
            email_service.send_crm_email(
                customer=self.object.customer,
                opportunity=self.object,
                to_email=form.cleaned_data["to_email"],
                subject=form.cleaned_data["subject"],
                body=form.cleaned_data["body"],
                user=self.request.user,
            )
            messages.success(self.request, "Email sent and logged.")
        except Exception as exc:
            messages.error(self.request, f"Email failed: {exc}")
        return redirect("crm:opportunity", self.object.pk)

    def post_sync_emails(self):
        try:
            count = email_service.sync_inbound_emails()
            messages.success(self.request, f"Synced Gmail: {count} new customer repl{'y' if count == 1 else 'ies'}.")
        except Exception as exc:
            messages.error(self.request, f"Gmail sync failed: {exc}")
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
            open_count=Count("opportunities", filter=Q(opportunities__status__in=Opportunity.OPEN))).order_by("-created_at")
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


class CustomerDetailView(CRMView, CRMActionsMixin, DetailView):
    template_name = "crm/customer_detail.html"
    queryset = Customer.objects.select_related("owner")

    def get_context_data(self, **kwargs):
        customer = self.object
        kwargs.setdefault("form", CustomerForm(instance=customer))
        kwargs.setdefault("note_form", NoteForm())
        kwargs.setdefault("email_form", SendEmailForm(initial={"to_email": customer.email}))
        contacts = list(customer.contacts.select_related("platform_account"))
        channel_messages = (
            MetaMessage.objects.filter(contact__in=contacts)
            .select_related("contact", "platform_account", "sent_by")
            .order_by("-created_at")[:30]
            if contacts else []
        )
        return super().get_context_data(
            opportunities=customer.opportunities.select_related("service").prefetch_related("services"),  # service_list reads both
            contacts=contacts,
            channel_messages=channel_messages,
            emails=customer.emails.select_related("sent_by", "opportunity")[:25],
            pms_projects=customer.pms_projects.all(),
            follow_ups=customer.follow_ups.select_related("opportunity", "assigned_to", "channel").order_by("status", "due_at")[:15],
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

    def post_send_email(self):
        form = SendEmailForm(self.request.POST)
        if not form.is_valid():
            return self.rerender(email_form=form)
        try:
            email_service.send_crm_email(
                customer=self.object,
                to_email=form.cleaned_data["to_email"],
                subject=form.cleaned_data["subject"],
                body=form.cleaned_data["body"],
                user=self.request.user,
            )
            return self.done("Email sent and logged.")
        except Exception as exc:
            messages.error(self.request, f"Email failed: {exc}")
            return self.done()

    def post_sync_emails(self):
        try:
            count = email_service.sync_inbound_emails()
            return self.done(f"Synced Gmail: {count} new customer repl{'y' if count == 1 else 'ies'}.")
        except Exception as exc:
            messages.error(self.request, f"Gmail sync failed: {exc}")
            return self.done()

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
        gfile = get_object_or_404(GoogleFile, pk=int_or(self.request.POST.get("file"), 0), customer=self.object)
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
            google.share_file(get_object_or_404(GoogleFile, pk=int_or(form.cleaned_data["target"], 0), customer=self.object),
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


class InboxThreadView(CRMView, CRMActionsMixin, DetailView):
    """One chat: read it live (the page polls ?format=json&after=<id>), take it over, reply, hand back."""
    template_name = "crm/inbox_thread.html"
    pk_url_kwarg = "thread_id"
    context_object_name = "thread"

    def get_queryset(self):
        return chat_threads().select_related("handled_by")

    def get(self, request, *args, **kwargs):
        if request.GET.get("format") != "json":
            return super().get(request, *args, **kwargs)
        thread, after = self.get_object(), int_or(request.GET.get("after"), 0)
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


class SettingsView(CRMView, CRMActionsMixin, TemplateView):
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


# ---- meetings & transcripts ------------------------------------------------------------------


class MeetingListView(CRMView, ListView):
    """Meetings hub: upcoming, past, proposed-time requests, and stored transcripts table."""
    template_name = "crm/meetings.html"
    context_object_name = "meetings"
    paginate_by = PAGE_SIZE

    def get(self, request, *args, **kwargs):
        if GoogleAccount.get_solo().is_connected and cache.add("crm_meetings_auto_sync", 1, timeout=300):
            try:
                google.import_calendar_events()
            except google.GoogleError as exc:
                logger.warning("Calendar auto-sync failed: %s", exc)
                messages.warning(request, f"Google Calendar sync failed: {exc}")
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        tab = self.request.GET.get("tab", "upcoming")
        q = self.request.GET.get("q", "").strip()
        now = timezone.now()
        qs = Meeting.objects.select_related("customer", "opportunity", "organizer").prefetch_related("transcripts")

        if tab == "upcoming":
            qs = qs.filter(status=Meeting.Status.SCHEDULED, end_at__gte=now - timedelta(hours=2)).order_by("start_at")
        elif tab == "today":
            today_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)
            qs = qs.filter(status=Meeting.Status.SCHEDULED, start_at__gte=today_start, start_at__lt=today_end).order_by("start_at")
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
        today_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        proposals_count = sum(
            1 for m in Meeting.objects.filter(status=Meeting.Status.SCHEDULED) if m.pending_proposals
        )
        m_stats = Meeting.objects.aggregate(
            upcoming=Count("id", filter=Q(status=Meeting.Status.SCHEDULED, end_at__gte=now - timedelta(hours=2))),
            today=Count("id", filter=Q(status=Meeting.Status.SCHEDULED, start_at__gte=today_start, start_at__lt=today_end)),
            past=Count("id", filter=(Q(status=Meeting.Status.DONE) | Q(end_at__lt=now)) & ~Q(status=Meeting.Status.CANCELLED)),
            cancelled=Count("id", filter=Q(status=Meeting.Status.CANCELLED)),
            all=Count("id"),
            synced=Count("id", filter=~Q(google_event_id="")),
        )

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
                "upcoming": m_stats["upcoming"] or 0,
                "today": m_stats["today"] or 0,
                "proposals": proposals_count,
                "transcripts": MeetingTranscript.objects.count(),
                "past": m_stats["past"] or 0,
                "cancelled": m_stats["cancelled"] or 0,
                "all": m_stats["all"] or 0,
                "synced": m_stats["synced"] or 0,
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
            customer = Customer.objects.filter(pk=int_or(cid, 0)).first()
            if customer:
                initial["customer"] = customer
                if customer.email:
                    initial["attendees"] = [customer.email]
        if oid := self.request.GET.get("opportunity"):
            opp = Opportunity.objects.select_related("customer").filter(pk=int_or(oid, 0)).first()
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
                body=f"Scheduled meeting \"{meeting.title}\" for {timezone.localtime(meeting.start_at):%d %b %Y, %I:%M %p}.",
            )
        return redirect("crm:meeting", meeting.pk)

    def get_context_data(self, **kwargs):
        return super().get_context_data(back=reverse("crm:meetings"), **kwargs)


class MeetingSyncView(CRMView, View):
    """Pull & import upcoming meetings and RSVP updates from Google Calendar."""
    role = AGENT
    http_method_names = ["post"]

    def post(self, request):
        next_url = safe_next(request, reverse("crm:meetings"))
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


class MeetingDetailView(CRMView, CRMActionsMixin, DetailView):
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
            proposals=[{**p, "start": parse_datetime(p.get("proposed_start") or ""), "end": parse_datetime(p.get("proposed_end") or "")}
                       for p in m.proposed_times or []],
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
        with timezone.override(self.object.tzinfo):  # the typed times are in the meeting's timezone, as shown
            form = ProposeTimeForm(self.request.POST)
            valid = form.is_valid()
        if not valid:
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
        transcript = get_object_or_404(MeetingTranscript, pk=int_or(self.request.POST.get("transcript_id"), 0), meeting=self.object)
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
        transcript = get_object_or_404(MeetingTranscript, pk=int_or(self.request.POST.get("transcript_id"), 0), meeting=self.object)
        transcript.delete()
        return self.done("Transcript deleted.")

    def post_delete(self):
        self.require(ADMIN)
        from django.db import connection, transaction

        if self.object.google_event_id and GoogleAccount.get_solo().is_connected:
            self.object.status = Meeting.Status.CANCELLED
            google.sync_meeting(self.object)
            if self.object.sync_error:
                messages.warning(self.request, f"Deleted in CRM, but could not cancel in Google Calendar: {self.object.sync_error}")

        with transaction.atomic():
            if "voice_agent_voicebotsession" in connection.introspection.table_names():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE voice_agent_voicebotsession SET meeting_id = NULL WHERE meeting_id = %s",
                        [self.object.pk],
                    )
            self.object.delete()
        messages.success(self.request, "Meeting deleted.")
        return redirect("crm:meetings")


class MeetingTranscriptAPIView(APIView):
    """JSON endpoint to store a meeting transcript at the end of a call/meeting. Agents and Admins only, by
    `Authorization: Token ...` (external AI/voice callers, no CSRF) or a logged-in session (CSRF enforced by DRF)."""
    permission_classes = [HasRole]

    def post(self, request, pk: int):
        meeting = get_object_or_404(Meeting, pk=pk)
        payload = json_body(request)
        if not payload:
            return JsonResponse({"error": "JSON object expected."}, status=400)

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
            duration_seconds=int_or(payload.get("duration_seconds"), 0) or None,
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


# ---- public website contact form -------------------------------------------------------------


class LeadSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=2, max_length=150)
    email = serializers.EmailField(max_length=254)
    message = serializers.CharField(min_length=5, max_length=5000)
    phone = serializers.CharField(max_length=50, required=False, allow_blank=True)
    subject = serializers.CharField(max_length=255, required=False, allow_blank=True)
    inquiry_type = serializers.CharField(max_length=60, required=False, allow_blank=True)


class LeadCreateAPIView(APIView):
    """POST /crm/api/leads/ - the website's contact form. Public (no login, so no CSRF) and throttled per IP
    with the `inquiry` rate; saves a customer + opportunity and alerts the team."""
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "inquiry"

    def post(self, request):
        from cms.views import check_maintenance  # same switch as the rest of the public API; imported late to avoid a cycle

        if (blocked := check_maintenance("inquiries")) is not None:
            return blocked
        serializer = LeadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        opportunity = services.create_opportunity(
            name=data["name"], email=data["email"], phone=data.get("phone", ""),
            title=data.get("subject") or data["message"].split("\n")[0][:120], details=data["message"],
            source=Source.WEBSITE_FORM, form_type=data.get("inquiry_type", ""),
        )
        return Response({"status": "ok", "inquiry_id": opportunity.reference,
                         "message": "Thank you! Your project inquiry has been received. We will get back to you shortly."},
                        status=201)
