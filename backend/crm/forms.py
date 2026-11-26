from zoneinfo import available_timezones

from django import forms
from django.contrib.auth import get_user_model
from django.core.validators import validate_email

from crm.google import MAX_UPLOAD_BYTES, SHARE_ROLES
from crm.models import Activity, Customer, GoogleAccount, GoogleFile, Meeting, MeetingTranscript, Opportunity, Service
from meta_connector.models import Contact
from meta_connector.roles import team

DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class TeamChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        return user.get_full_name() or user.get_username()


class OpportunityForm(forms.ModelForm):
    assigned_to = TeamChoiceField(queryset=get_user_model().objects.none(), required=False)
    services = forms.ModelMultipleChoiceField(
        queryset=Service.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Services",
        help_text="Select one or more services this customer is interested in.",
    )

    class Meta:
        model = Opportunity
        fields = ["customer", "services", "title", "details", "status", "assigned_to", "next_follow_up"]
        widgets = {"next_follow_up": DATETIME, "details": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = team()
        self.fields["services"].queryset = Service.objects.filter(is_active=True)
        self.fields["next_follow_up"].input_formats = ["%Y-%m-%dT%H:%M"]
        if self.instance.pk and not self.is_bound:
            selected = list(self.instance.services.values_list("pk", flat=True))
            if not selected and self.instance.service_id:
                selected = [self.instance.service_id]
            self.initial["services"] = selected

    def save(self, commit=True):
        instance = super().save(commit=False)
        selected = list(self.cleaned_data.get("services") or [])
        instance.service = selected[0] if selected else None
        if commit:
            instance.save()
            instance.services.set(selected)
        return instance


class CustomerForm(forms.ModelForm):
    owner = TeamChoiceField(queryset=get_user_model().objects.none(), required=False)

    class Meta:
        model = Customer
        fields = ["name", "company", "email", "phone", "instagram_username", "website", "owner", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = team()

    def clean_instagram_username(self):
        return self.cleaned_data["instagram_username"].strip().lstrip("@")


class NoteForm(forms.Form):
    kind = forms.ChoiceField(choices=[(k, label) for k, label in Activity.Kind.choices
                                      if k in ("note", "call", "email", "message")])
    body = forms.CharField(max_length=5000, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Add a note…"}))


class EmailListField(forms.CharField):
    """Comma or newline separated emails -> list."""

    def to_python(self, value):
        value = super().to_python(value) or ""
        emails = [e.strip() for e in value.replace("\n", ",").split(",") if e.strip()]
        for email in emails:
            validate_email(email)
        return sorted(set(emails), key=emails.index)

    def prepare_value(self, value):
        return ", ".join(value) if isinstance(value, list) else value


class MeetingForm(forms.ModelForm):
    attendees = EmailListField(required=False, help_text="Guests' emails, comma separated. Google sends the invites.")
    timezone = forms.ChoiceField(choices=[(z, z) for z in sorted(available_timezones())], initial="Asia/Kolkata")

    class Meta:
        model = Meeting
        fields = ["title", "customer", "opportunity", "start_at", "end_at", "timezone", "location", "attendees",
                  "description"]
        widgets = {"start_at": DATETIME, "end_at": DATETIME, "description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("start_at", "end_at"):
            self.fields[name].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["customer"].required = False
        self.fields["customer"].queryset = Customer.objects.order_by("name")
        self.fields["opportunity"].queryset = Opportunity.objects.filter(status__in=Opportunity.OPEN).select_related(
            "customer")
        self.fields["location"].help_text = "Leave blank to create a Google Meet link."


class MeetingOutcomeForm(forms.Form):
    outcome = forms.CharField(max_length=5000, widget=forms.Textarea(attrs={"rows": 3}), required=False)


class ProposeTimeForm(forms.Form):
    email = forms.EmailField(label="Guest email")
    name = forms.CharField(max_length=120, required=False, label="Guest name")
    proposed_start = forms.DateTimeField(widget=DATETIME, input_formats=["%Y-%m-%dT%H:%M"], label="Proposed start")
    proposed_end = forms.DateTimeField(widget=DATETIME, input_formats=["%Y-%m-%dT%H:%M"], label="Proposed end")
    note = forms.CharField(max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Reason or note…"}))

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("proposed_start"), cleaned.get("proposed_end")
        if start and end and end <= start:
            self.add_error("proposed_end", "Proposed end time must be after start time.")
        return cleaned


class MeetingTranscriptForm(forms.Form):
    title = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs={"placeholder": "e.g. Discovery Call Transcript"}))
    source = forms.ChoiceField(choices=MeetingTranscript.Source.choices, initial=MeetingTranscript.Source.MANUAL)
    language = forms.CharField(max_length=20, initial="en", required=False)
    raw_text = forms.CharField(
        required=False,
        label="Transcript text",
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "Paste transcript (Speaker: message, WebVTT, or SRT)…"}),
    )
    transcript_file = forms.FileField(required=False, label="Or upload .txt / .vtt / .srt")
    summary = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Optional summary / key takeaways…"}),
    )

    def clean(self):
        cleaned = super().clean()
        text = (cleaned.get("raw_text") or "").strip()
        uploaded = cleaned.get("transcript_file")
        if uploaded:
            if uploaded.size > 5 * 1024 * 1024:
                self.add_error("transcript_file", "Transcript file must be under 5 MB.")
                return cleaned
            try:
                text = uploaded.read().decode("utf-8", errors="replace").strip()
            except Exception:
                self.add_error("transcript_file", "Could not read transcript file.")
                return cleaned
            cleaned["raw_text"] = text
            if cleaned.get("source") == MeetingTranscript.Source.MANUAL:
                cleaned["source"] = MeetingTranscript.Source.UPLOAD
        if not text:
            raise forms.ValidationError("Paste transcript text or upload a transcript file.")
        return cleaned


class GoogleCredentialsForm(forms.ModelForm):
    """OAuth app credentials from Google Cloud Console (APIs & Services > Credentials > OAuth client ID)."""

    new_client_secret = forms.CharField(
        required=False, label="Client secret", widget=forms.PasswordInput(render_value=False),
        help_text="Saved encrypted and never shown again. Leave blank to keep the current one.",
    )

    class Meta:
        model = GoogleAccount
        fields = ["client_id", "calendar_id"]
        help_texts = {"calendar_id": "Automatically locked to the connected Google account email (read-only)."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cal = self.fields["calendar_id"]
        cal.disabled = True
        cal.widget.attrs["class"] = "opacity-75 cursor-not-allowed bg-subtle/60"
        if self.instance and self.instance.is_connected and self.instance.email:
            self.initial["calendar_id"] = self.instance.email
            self.instance.calendar_id = self.instance.email

    def save(self, commit=True):
        account = super().save(commit=False)
        if secret := self.cleaned_data.get("new_client_secret", "").strip():
            account.client_secret = secret
        if commit:
            account.save()
        return account


class DriveFileForm(forms.Form):
    kind = forms.ChoiceField(choices=[(k, label) for k, label in GoogleFile.Kind.choices if k != GoogleFile.Kind.FILE],
                             initial=GoogleFile.Kind.DOCUMENT)
    name = forms.CharField(max_length=200)


class UploadForm(forms.Form):
    file = forms.FileField()

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError("Files up to 25 MB.")
        return f


class ShareForm(forms.Form):
    target = forms.CharField(max_length=128, widget=forms.HiddenInput)  # GoogleFile pk, or "folder"
    email = forms.EmailField()
    role = forms.ChoiceField(choices=[(r, r.title()) for r in SHARE_ROLES], initial="reader")


class ReplyForm(forms.Form):
    content = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Type a reply…"}))


class ContactChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, contact):
        who = contact.name or contact.username or contact.phone or contact.platform_user_id
        return f"{contact.platform_account.get_platform_display()}: {who}"


class MessageForm(forms.Form):
    """A WhatsApp / Instagram / Messenger message to one of the customer's linked inbox contacts."""
    contact = ContactChoiceField(queryset=Contact.objects.none(), empty_label=None)
    text = forms.CharField(max_length=1000, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Message…"}))

    def __init__(self, *args, customer: Customer, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contact"].queryset = customer.contacts.select_related("platform_account")
