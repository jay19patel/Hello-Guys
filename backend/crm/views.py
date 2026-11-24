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

from crm import google, services
from crm.forms import (
    CustomerForm, DriveFileForm, GoogleCredentialsForm, MeetingForm, MeetingOutcomeForm, MessageForm, NoteForm,
    OpportunityForm, ReplyForm, ShareForm, UploadForm,
)
from crm.models import Activity, Customer, GoogleAccount, GoogleFile, Meeting, Opportunity, Service, Source
from hello_guy_ai.conf import ai_settings
from hello_guy_ai.exceptions import AssistantNotFound
from hello_guy_ai.models import Message as AIMessage, Thread
from hello_guy_ai.services import chat
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

    def get_context_data(self, **kwargs):
        now, opportunities = timezone.now(), Opportunity.objects.select_related("customer")
        by_status = dict(opportunities.values_list("status").annotate(n=Count("id")))
        return super().get_context_data(
            stats={
                "open": opportunities.filter(status__in=Opportunity.OPEN).count(),
                "new_this_week": opportunities.filter(created_at__gte=now - timedelta(days=7)).count(),
                "won_this_month": opportunities.filter(status=Opportunity.Status.WON,
                                                       updated_at__gte=now - timedelta(days=30)).count(),
                "customers": Customer.objects.count(),
            },
            pipeline=[(label, by_status.get(value, 0), value) for value, label in Opportunity.Status.choices],
            recent=opportunities[:8],
            follow_ups=opportunities.filter(status__in=Opportunity.OPEN, next_follow_up__lte=now + timedelta(days=1))
            .order_by("next_follow_up")[:8],
            meetings=Meeting.objects.filter(status=Meeting.Status.SCHEDULED, start_at__gte=now - timedelta(hours=1))
            .select_related("customer")[:8],
            waiting_chats=Thread.objects.filter(needs_human=True).order_by("-updated_at")[:5],
            **kwargs,
        )


# ---- opportunities ---------------------------------------------------------------------------


class OpportunityListView(CRMView, ListView):
    """The pipeline: what every customer is interested in."""
    template_name = "crm/opportunity_list.html"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs, get = Opportunity.objects.select_related("customer", "service", "assigned_to"), self.request.GET
        status, service, q = get.get("status", "open"), get.get("service", ""), get.get("q", "").strip()
        if status == "open":
            qs = qs.filter(status__in=Opportunity.OPEN)
        elif status in Opportunity.Status.values:
            qs = qs.filter(status=status)
        if service.isdigit():
            qs = qs.filter(service_id=service)
        if get.get("mine"):
            qs = qs.filter(assigned_to=self.request.user)
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(reference__iexact=q) | Q(customer__name__icontains=q)
                           | Q(customer__email__icontains=q) | Q(customer__phone__icontains=q)
                           | Q(customer__company__icontains=q))
        self.filters = {"status": status, "service": service, "q": q}
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(statuses=Opportunity.Status.choices, services=Service.objects.all(),
                                        **self.filters, **kwargs)


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
    queryset = Opportunity.objects.select_related("customer", "service", "assigned_to", "thread")

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
        return super().get_context_data(
            opportunities=customer.opportunities.select_related("service"),
            contacts=customer.contacts.select_related("platform_account"),
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


# ---- meetings --------------------------------------------------------------------------------


def sync_to_calendar(request, meeting: Meeting, done: str) -> None:
    """Push the meeting to Google Calendar and say how it went (the CRM entry is saved either way)."""
    google.sync_meeting(meeting)
    if meeting.sync_error:
        messages.warning(request, f"{done} in the CRM. Google Calendar: {meeting.sync_error}")
    else:
        messages.success(request, f"{done}. Google Calendar invite sent"
                         + (" with a Meet link." if meeting.meet_url else "."))


class MeetingListView(CRMView, ListView):
    template_name = "crm/meeting_list.html"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        now, qs = timezone.now(), Meeting.objects.select_related("customer", "opportunity", "organizer")
        if self.request.GET.get("when") == "past":
            return qs.filter(start_at__lt=now).order_by("-start_at")
        return qs.filter(start_at__gte=now - timedelta(hours=1))

    def get_context_data(self, **kwargs):
        return super().get_context_data(show_past=self.request.GET.get("when") == "past", **kwargs)


class MeetingCreateView(CRMView, CreateView):
    role = AGENT
    form_class = MeetingForm
    template_name = "crm/form.html"
    extra_context = {"title": "Schedule a meeting"}

    def get_initial(self):
        return {"customer": self.request.GET.get("customer"), "opportunity": self.request.GET.get("opportunity")}

    def form_valid(self, form):
        form.instance.organizer = self.request.user
        meeting = form.save()
        services.add_note(user=self.request.user, customer=meeting.customer, opportunity=meeting.opportunity,
                          kind=Activity.Kind.MEETING, body=f"Meeting scheduled: {meeting}")
        sync_to_calendar(self.request, meeting, "Meeting scheduled")
        return redirect("crm:meeting", meeting.pk)

    def get_context_data(self, **kwargs):
        return super().get_context_data(back=reverse("crm:meetings"), **kwargs)


class MeetingDetailView(CRMView, ActionsMixin, DetailView):
    template_name = "crm/meeting_detail.html"
    queryset = Meeting.objects.select_related("customer", "opportunity", "organizer")

    def get_context_data(self, **kwargs):
        kwargs.setdefault("form", MeetingForm(instance=self.object))
        return super().get_context_data(outcome_form=MeetingOutcomeForm(initial={"outcome": self.object.outcome}),
                                        **kwargs)

    def post_update(self):
        form = MeetingForm(self.request.POST, instance=self.object)
        if not form.is_valid():
            return self.rerender(form=form)
        sync_to_calendar(self.request, form.save(), "Meeting updated")
        return redirect("crm:meeting", self.object.pk)

    def post_sync(self):
        sync_to_calendar(self.request, self.object, "Synced")
        return redirect("crm:meeting", self.object.pk)

    def post_cancel(self):
        return self.close(Meeting.Status.CANCELLED)

    def post_done(self):
        return self.close(Meeting.Status.DONE)

    def close(self, status: str):
        meeting = self.object
        meeting.status = status
        form = MeetingOutcomeForm(self.request.POST)
        if form.is_valid() and form.cleaned_data["outcome"]:
            meeting.outcome = form.cleaned_data["outcome"]
        meeting.save()
        if status == Meeting.Status.CANCELLED:
            google.sync_meeting(meeting)  # deletes the Calendar event; Google emails the guests
        label = meeting.get_status_display().lower()
        services.add_note(user=self.request.user, customer=meeting.customer, opportunity=meeting.opportunity,
                          kind=Activity.Kind.MEETING,
                          body=f"Meeting {label}: {meeting.title}" + (f"\n{meeting.outcome}" if meeting.outcome else ""))
        messages.success(self.request, f"Meeting marked {label}.")
        return redirect("crm:meeting", meeting.pk)


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
        return super().get_context_data(account=account, redirect_uri=redirect_uri(self.request),
                                        scopes=google.SCOPES, **kwargs)

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
