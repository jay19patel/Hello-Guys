import json

from django import forms

from hello_guy_meet.models import MeetRoom, MeetSettings


DATETIME_INPUT = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class MeetRoomForm(forms.ModelForm):
    class Meta:
        model = MeetRoom
        fields = ["title", "scheduled_start", "scheduled_end", "ai_enabled", "bot_name", "system_prompt"]
        widgets = {
            "scheduled_start": DATETIME_INPUT,
            "scheduled_end": DATETIME_INPUT,
            "system_prompt": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["scheduled_start"].required = False
        self.fields["scheduled_end"].required = False
        self.fields["scheduled_start"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["scheduled_end"].input_formats = ["%Y-%m-%dT%H:%M"]


class MeetSettingsForm(forms.ModelForm):
    ice_servers_json = forms.CharField(
        label="WebRTC ICE Servers (STUN / TURN JSON)",
        widget=forms.Textarea(attrs={"rows": 3, "class": "font-mono text-[12.5px]"}),
        help_text='JSON list, e.g. [{"urls": "stun:stun.l.google.com:19302"}]',
    )

    class Meta:
        model = MeetSettings
        fields = [
            "default_bot_name",
            "ws_base_url",
            "llm_model",
            "stt_provider",
            "tts_provider",
            "cartesia_voice_id",
            "ai_auto_join_default",
            "allow_interruptions",
            "default_system_prompt",
        ]
        widgets = {"default_system_prompt": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial["ice_servers_json"] = json.dumps(self.instance.ice_servers or [], indent=2)

    def clean_ice_servers_json(self):
        raw = self.cleaned_data.get("ice_servers_json", "[]")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"Invalid JSON: {exc}") from None
        if not isinstance(parsed, list):
            raise forms.ValidationError("ICE servers must be a JSON array.")
        return parsed

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.ice_servers = self.cleaned_data["ice_servers_json"]
        if commit:
            obj.save()
        return obj
