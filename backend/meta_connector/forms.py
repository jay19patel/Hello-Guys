from django import forms
from meta_connector.models import AutoDMRule, MetaConfig, PlatformAccount
from meta_connector.services.token_service import TokenService


class MetaConfigForm(forms.ModelForm):
    new_app_secret = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="App Secret",
        help_text="Leave blank to keep the current secret.",
    )
    clear_app_secret = forms.BooleanField(required=False, label="Remove saved App Secret")

    class Meta:
        model = MetaConfig
        fields = ['app_name', 'verify_token', 'graph_api_version', 'webhook_log_retention_days']

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.cleaned_data.get('clear_app_secret'):
            instance.app_secret = ''
        elif self.cleaned_data.get('new_app_secret'):
            instance.app_secret = self.cleaned_data['new_app_secret'].strip()
        if commit:
            instance.save()
        return instance


class PlatformAccountForm(forms.ModelForm):
    raw_access_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="Access Token",
        help_text="Page, System User or User token. A User token is turned into a permanent Page token. "
                  "Stored encrypted; leave blank to keep the current one.",
    )

    class Meta:
        model = PlatformAccount
        fields = ['platform', 'display_name', 'phone_number_id', 'instagram_id', 'verify_token', 'is_active', 'ai_auto_reply']

    def clean(self):
        data = super().clean()
        platform = data.get('platform')
        if platform == PlatformAccount.PLATFORM_WHATSAPP and not data.get('phone_number_id'):
            self.add_error('phone_number_id', "Required for WhatsApp accounts.")
        if platform in (PlatformAccount.PLATFORM_INSTAGRAM, PlatformAccount.PLATFORM_FACEBOOK) and not data.get('instagram_id'):
            label = "Facebook Page ID" if platform == PlatformAccount.PLATFORM_FACEBOOK else "Instagram account ID"
            self.add_error('instagram_id', f"Required for {platform.title()} accounts ({label}).")
        if not self.instance.pk and not data.get('raw_access_token'):
            self.add_error('raw_access_token', "Required for a new account.")
        self.token_info, self.token_note = None, ''
        if data.get('raw_access_token') and not self.errors:
            token, self.token_info, self.token_note, error = TokenService.prepare(
                data['platform'], data.get('instagram_id', ''), data['raw_access_token'])
            if error:
                self.add_error('raw_access_token', error)
            else:
                data['raw_access_token'] = token
        return data

    def save(self, commit=True):
        instance = super().save(commit=False)
        raw_token = self.cleaned_data.get('raw_access_token')
        if raw_token:
            instance.set_access_token(raw_token.strip())
            instance.token_is_valid = None  # new token: unknown until Meta confirms
            TokenService.apply(instance, self.token_info, '' if self.token_info else 'Could not reach Meta to check this token.')
        if commit:
            instance.save()
        return instance


class AutoDMRuleForm(forms.ModelForm):
    class Meta:
        model = AutoDMRule
        fields = ['is_enabled', 'message', 'keywords', 'public_reply']
        widgets = {'message': forms.Textarea(attrs={'rows': 3})}


class TeamMemberForm(forms.Form):
    """Admin adds a dashboard user with a role (Settings → Team)."""
    from meta_connector.roles import ROLES as _ROLES
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False, label="Name")
    password = forms.CharField(widget=forms.PasswordInput, min_length=8, help_text="At least 8 characters. Share it privately.")
    role = forms.ChoiceField(choices=[(r, r) for r in _ROLES], initial='Agent',
                             help_text="Admin: everything · Agent: reply & auto-DM, no delete/settings · Viewer: read only")

    def clean_username(self):
        from django.contrib.auth.models import User
        name = self.cleaned_data['username'].strip()
        if User.objects.filter(username__iexact=name).exists():
            raise forms.ValidationError("This username is taken.")
        return name

    def clean_password(self):
        from django.contrib.auth.password_validation import validate_password
        validate_password(self.cleaned_data['password'])
        return self.cleaned_data['password']
