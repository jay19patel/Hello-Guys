"""CRM: customers (everyone we talk to), what each is interested in (opportunities), their timeline,
meetings and Google files.

Links to the channels without them knowing:
  Customer.contacts  -> meta_connector.Contact (WhatsApp / Instagram / Messenger identity, used to message them)
  Opportunity.thread -> hello_guy_ai.Thread (the AI or live chat it came from)
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models
from django.utils import timezone

from hello_guy_ai.crypto import decrypt, encrypt


def new_reference() -> str:
    return f"opp_{uuid.uuid4().hex[:12]}"


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Source(models.TextChoices):
    """Where a customer or an opportunity came from."""
    WEBSITE_FORM = "website_form", "Website form"
    WEBSITE_CHAT = "website_chat", "Website chat"
    WHATSAPP = "whatsapp", "WhatsApp"
    INSTAGRAM = "instagram", "Instagram"
    FACEBOOK = "facebook", "Messenger"
    MANUAL = "manual", "Added by team"


class Service(models.Model):
    """What we sell. Opportunities say which one the customer wants. Managed in Django Admin."""
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class Customer(TimeStamped):
    name = models.CharField(max_length=150)
    company = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True, db_index=True)
    phone = models.CharField(max_length=50, blank=True, db_index=True, help_text="With country code, e.g. +91…")
    instagram_username = models.CharField(max_length=100, blank=True, help_text="Without @")
    website = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL, db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="crm_customers"
    )
    # Inbox identities (WhatsApp number, Instagram/Messenger account) of this customer.
    contacts = models.ManyToManyField("meta_connector.Contact", blank=True, related_name="crm_customers")
    drive_folder_id = models.CharField(max_length=128, blank=True)
    drive_folder_url = models.URLField(blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.name} ({self.company})" if self.company else self.name


class Opportunity(TimeStamped):
    """Something a customer is interested in, from first message to won or lost."""

    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        QUALIFIED = "qualified", "Qualified"
        PROPOSAL = "proposal", "Proposal sent"
        WON = "won", "Won"
        LOST = "lost", "Lost"

    OPEN = (Status.NEW, Status.CONTACTED, Status.QUALIFIED, Status.PROPOSAL)

    reference = models.CharField(max_length=40, default=new_reference, unique=True, help_text="Shown to the visitor")
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="opportunities")
    service = models.ForeignKey(Service, on_delete=models.SET_NULL, null=True, blank=True, related_name="opportunities")
    services = models.ManyToManyField(Service, blank=True, related_name="multi_opportunities")
    title = models.CharField(max_length=255)
    details = models.TextField(blank=True, max_length=5000, help_text="What they asked for, budget, timeline…")
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL, db_index=True)
    form_type = models.CharField(max_length=60, blank=True, help_text="Which website form, e.g. Contact Form")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="crm_opportunities"
    )
    next_follow_up = models.DateTimeField(null=True, blank=True, db_index=True)
    thread = models.ForeignKey(
        "hello_guy_ai.Thread", on_delete=models.SET_NULL, null=True, blank=True, related_name="crm_opportunities"
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name_plural = "opportunities"
        indexes = [models.Index(fields=["status", "-created_at"], name="crm_opp_status_new")]

    def __str__(self) -> str:
        return f"{self.title} — {self.customer.name}"

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN

    @property
    def service_list(self) -> list[Service]:
        items = list(self.services.all())
        if not items and self.service_id:
            return [self.service]
        return items


class Activity(models.Model):
    """Timeline entry on a customer (and the opportunity it is about): notes, calls, status changes, events."""

    class Kind(models.TextChoices):
        NOTE = "note", "Note"
        CALL = "call", "Call"
        EMAIL = "email", "Email"
        MESSAGE = "message", "Message"
        MEETING = "meeting", "Meeting"
        STATUS = "status", "Status change"
        SYSTEM = "system", "System"

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="activities")
    opportunity = models.ForeignKey(
        Opportunity, on_delete=models.CASCADE, null=True, blank=True, related_name="activities"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.NOTE)
    body = models.TextField(max_length=5000)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name_plural = "activities"

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.body[:50]}"


class Meeting(TimeStamped):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, max_length=5000)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="meetings")
    opportunity = models.ForeignKey(
        Opportunity, on_delete=models.SET_NULL, null=True, blank=True, related_name="meetings"
    )
    start_at = models.DateTimeField(db_index=True)
    end_at = models.DateTimeField()
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    location = models.CharField(max_length=255, blank=True, help_text="Blank: Google Meet link is created")
    attendees = models.JSONField(default=list, blank=True, help_text="Guest emails (invited by Google Calendar)")
    guest_responses = models.JSONField(default=dict, blank=True, help_text="{email: accepted|declined|tentative|needsAction}, from Google")
    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="crm_meetings"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED, db_index=True)
    outcome = models.TextField(blank=True, help_text="Notes after the meeting")
    # Google Calendar copy. Empty event id = not synced (Google not connected, or the last sync failed).
    google_event_id = models.CharField(max_length=255, blank=True)
    google_event_url = models.URLField(blank=True, max_length=500)
    meet_url = models.URLField(blank=True)
    sync_error = models.TextField(blank=True)

    class Meta:
        ordering = ("start_at",)
        constraints = [models.CheckConstraint(condition=models.Q(end_at__gt=models.F("start_at")), name="crm_meeting_ends_after_start")]

    def __str__(self) -> str:
        return f"{self.title} @ {timezone.localtime(self.start_at):%Y-%m-%d %H:%M}"

    @property
    def guests(self) -> list[tuple[str, str]]:
        """(email, RSVP from Google Calendar or "") for the template."""
        return [(email, self.guest_responses.get(email.lower(), "")) for email in self.attendees or []]

    @property
    def rsvp_summary(self) -> dict[str, int]:
        """Counts of invited, accepted, declined, tentative (maybe), and pending RSVPs."""
        guests = self.guests
        counts = {"total": len(guests), "accepted": 0, "declined": 0, "tentative": 0, "pending": 0}
        for _, status in guests:
            if status in ("accepted", "declined", "tentative"):
                counts[status] += 1
            else:
                counts["pending"] += 1
        return counts

    def clean(self) -> None:
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValidationError({"end_at": "The meeting must end after it starts."})
        if self.opportunity_id and self.customer_id and self.opportunity.customer_id != self.customer_id:
            raise ValidationError({"opportunity": "Pick an opportunity of this customer."})
        for email in self.attendees or []:
            try:
                validate_email(email)
            except Exception:
                raise ValidationError({"attendees": f"{email!r} is not a valid email."}) from None


class GoogleFile(models.Model):
    """A Drive item created from the CRM for a customer (sub-folder, Google Doc, Google Sheet or uploaded file)."""

    class Kind(models.TextChoices):
        FOLDER = "folder", "Folder"
        DOCUMENT = "document", "Google Doc"
        SPREADSHEET = "spreadsheet", "Google Sheet"
        FILE = "file", "File"

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="google_files")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    google_id = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=255)
    url = models.URLField(max_length=500)
    shared_with = models.JSONField(default=list, blank=True, help_text="[{email, role}] shared from the CRM")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.name


class GoogleAccount(models.Model):
    """CRM settings: the one Google account the CRM works with (OAuth app credentials + its refresh token).

    Secrets are Fernet-encrypted with FIELD_ENCRYPTION_KEY. Edited in CRM > Settings, never shown back.
    """

    client_id = models.CharField(max_length=255, blank=True)
    client_secret_encrypted = models.TextField(blank=True)
    refresh_token_encrypted = models.TextField(blank=True)
    email = models.EmailField(blank=True, help_text="The connected Google account")
    scopes = models.TextField(blank=True)
    calendar_id = models.CharField(max_length=255, default="primary")
    root_folder_id = models.CharField(max_length=128, blank=True, help_text="Drive folder holding customer folders")
    connected_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Google account"

    def __str__(self) -> str:
        return self.email or "Google (not connected)"

    @classmethod
    def get_solo(cls) -> "GoogleAccount":
        return cls.objects.order_by("pk").first() or cls.objects.get_or_create(pk=1)[0]

    @property
    def client_secret(self) -> str:
        return decrypt(self.client_secret_encrypted)

    @client_secret.setter
    def client_secret(self, raw: str) -> None:
        self.client_secret_encrypted = encrypt(raw or "")

    @property
    def refresh_token(self) -> str:
        return decrypt(self.refresh_token_encrypted)

    @refresh_token.setter
    def refresh_token(self, raw: str) -> None:
        self.refresh_token_encrypted = encrypt(raw or "")

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret_encrypted)

    @property
    def is_connected(self) -> bool:
        return bool(self.refresh_token_encrypted)


class FollowUp(TimeStamped):
    """Scheduled follow-up task for a customer/opportunity (created by team or AI scheduler)."""

    class Channel(models.TextChoices):
        WHATSAPP = "whatsapp", "WhatsApp"
        INSTAGRAM = "instagram", "Instagram"
        CALL = "call", "Phone call"
        EMAIL = "email", "Email"
        MEETING = "meeting", "Meeting"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="follow_ups")
    opportunity = models.ForeignKey(
        Opportunity, on_delete=models.CASCADE, null=True, blank=True, related_name="follow_ups"
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="crm_follow_ups"
    )
    due_at = models.DateTimeField(db_index=True)
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.WHATSAPP)
    note = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_follow_ups"
    )

    class Meta:
        ordering = ("due_at",)
        indexes = [models.Index(fields=["status", "due_at"], name="crm_followup_status_due")]

    def __str__(self) -> str:
        return f"Follow-up {self.customer.name} ({self.due_at:%d %b %H:%M})"

    @property
    def is_overdue(self) -> bool:
        return self.status == self.Status.PENDING and self.due_at < timezone.now()
