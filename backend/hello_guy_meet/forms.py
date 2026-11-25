import json

from django import forms

from hello_guy_meet.models import MeetRoom, MeetSettings, encrypt_secret


DATETIME_INPUT = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class MeetRoomForm(forms.ModelForm):
    class Meta:
        model = MeetRoom
        fields = ["title", "scheduled_start", "scheduled_end", "ai_enabled", "bot_name", "system_prompt"]
        widgets = {
            "scheduled_start": DATETIME_INPUT,
            "scheduled_end": DATETIME_INPUT,
            "system_prompt": forms.Textarea(attrs={"rows": 2, "placeholder": "Meeting agenda / topics to cover…"}),
        }
        labels = {
            "system_prompt": "Agenda",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["scheduled_start"].required = False
        self.fields["scheduled_end"].required = False
        self.fields["bot_name"].required = False
        self.fields["scheduled_start"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["scheduled_end"].input_formats = ["%Y-%m-%dT%H:%M"]


class MeetSettingsForm(forms.ModelForm):
    groq_api_key = forms.CharField(
        required=False,
        label="Groq API Key",
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
    )
    mistral_api_key = forms.CharField(
        required=False,
        label="Mistral AI API Key (optional)",
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
    )
    ice_servers_json = forms.CharField(
        label="WebRTC ICE Servers (STUN / TURN JSON)",
        widget=forms.Textarea(attrs={"rows": 3, "class": "font-mono text-[12.5px]"}),
        help_text='JSON list, e.g. [{"urls": "stun:stun.l.google.com:19302"}]',
    )

    class Meta:
        model = MeetSettings
        fields = [
            "default_bot_name",
            "llm_model",
            "stt_provider",
            "edge_voice",
            "ai_auto_join_default",
            "allow_interruptions",
            "default_system_prompt",
        ]
        widgets = {"default_system_prompt": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial["ice_servers_json"] = json.dumps(self.instance.ice_servers or [], indent=2)
            key_checks = {
                "groq_api_key": "GROQ_API_KEY",
                "mistral_api_key": "MISTRAL_API_KEY",
            }
            for fname, env_name in key_checks.items():
                has_val = bool(self.instance.get_api_key(env_name))
                self.fields[fname].widget.attrs["placeholder"] = (
                    "Saved (leave blank to keep)" if has_val else f"Paste {env_name}…"
                )

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
        obj.tts_provider = MeetSettings.TTSProvider.EDGE_TTS
        obj.ice_servers = self.cleaned_data["ice_servers_json"]
        key_fields = {
            "groq_api_key": "groq_api_key_enc",
            "mistral_api_key": "mistral_api_key_enc",
        }
        for form_field, model_field in key_fields.items():
            raw = (self.cleaned_data.get(form_field) or "").strip()
            if raw:
                setattr(obj, model_field, encrypt_secret(raw))
        if commit:
            obj.save()
        return obj
