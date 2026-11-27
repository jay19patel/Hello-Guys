from django.contrib import messages
from django.contrib.auth.models import User
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from core.safety import redirect_next
from meta_connector.forms import MetaConfigForm, PlatformAccountForm, TeamMemberForm
from meta_connector.models import MetaConfig, PlatformAccount
from meta_connector.roles import ADMIN, ROLES, RoleRequiredMixin, get_role, set_role
from meta_connector.services.maintenance import retry_webhooks
from meta_connector.services.token_service import TokenService


class WebhookActionsView(RoleRequiredMixin, View):
    """Banner buttons: retry failed/stuck webhooks, or dismiss the bad-signature warning."""
    role = edit_role = ADMIN
    http_method_names = ['post']

    def post(self, request):
        if request.POST.get('action') == 'retry':
            fixed, failing = retry_webhooks()
            level = messages.success if not failing else messages.warning
            level(request, f"Re-processed {fixed} webhook(s)." + (f" {failing} still failing; open Django Admin → Webhook logs to see why." if failing else ''))
        elif request.POST.get('action') == 'dismiss_signature':
            MetaConfig.objects.filter(pk=MetaConfig.get_solo().pk).update(signature_failures=0, last_signature_failure_at=None)
            messages.success(request, "Signature warning cleared.")
        return redirect_next(request, 'dashboard')


class SettingsView(RoleRequiredMixin, View):
    """Dashboard > Settings: Meta app config, platform accounts and the team, all stored in the DB."""
    role = edit_role = ADMIN

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        edit_id = request.GET.get('edit') or request.POST.get('account_id')
        if edit_id and not edit_id.isdigit():
            raise Http404("Unknown account.")
        self.edit_account = get_object_or_404(PlatformAccount, pk=edit_id) if edit_id else None

    def get(self, request, config_form=None, account_form=None, team_form=None):
        config = MetaConfig.get_solo()
        TokenService.refresh_stale()
        return render(request, 'dashboard/settings.html', {
            'token_alerts': TokenService.alerts(),
            'team': [(u, get_role(u)) for u in User.objects.order_by('-is_active', 'username').prefetch_related('groups')],
            'team_form': team_form or TeamMemberForm(),
            'roles': ROLES,
            'config': config,
            'config_form': config_form or MetaConfigForm(instance=config),
            'account_form': account_form or PlatformAccountForm(instance=self.edit_account),
            'edit_account': self.edit_account,
            'accounts': PlatformAccount.objects.all(),
            'webhook_url': request.build_absolute_uri(reverse('meta_connector:meta-webhook')),
        })

    def post(self, request):
        action, account = request.POST.get('action'), self.edit_account
        if action == 'config':
            form = MetaConfigForm(request.POST, instance=MetaConfig.get_solo())
            if not form.is_valid():
                return self.get(request, config_form=form)
            form.save()
            messages.success(request, "Meta app settings saved.")
        elif action == 'account':
            form = PlatformAccountForm(request.POST, instance=account)
            if not form.is_valid():
                return self.get(request, account_form=form)
            saved = form.save()
            messages.success(request, f"Account '{saved.display_name}' saved. {form.token_note}".strip())
        elif action == 'check_token' and account:
            TokenService.refresh(account)
            level = messages.success if account.token_health == 'ok' else messages.warning
            level(request, f"Token for '{account.display_name}' checked: {account.token_health.upper()}. {account.token_error}".strip())
        elif action == 'delete' and account:
            if account.messages.exists() or account.contacts.exists():
                # Keep message history: deactivate instead of cascading deletes.
                account.is_active = False
                account.save(update_fields=['is_active'])
                messages.warning(request, f"'{account.display_name}' has history, so it was deactivated instead of deleted.")
            else:
                account.delete()
                messages.success(request, "Account deleted.")
        elif action == 'add_member':
            form = TeamMemberForm(request.POST)
            if not form.is_valid():
                return self.get(request, team_form=form)
            d = form.cleaned_data
            user = User.objects.create_user(d['username'], password=d['password'], first_name=d['first_name'])
            set_role(user, d['role'])
            messages.success(request, f"Added {user.username} as {d['role']}.")
            return redirect(reverse('dashboard-settings') + '#team')
        elif action in ('set_role', 'toggle_active'):
            user_id = request.POST.get('user_id', '')
            member = get_object_or_404(User, pk=user_id if user_id.isdigit() else 0)
            if member == request.user:
                messages.warning(request, "You can't change your own role or access. Ask another admin.")
            elif action == 'set_role' and request.POST.get('role') in ROLES:
                set_role(member, request.POST['role'])
                messages.success(request, f"{member.username} is now {request.POST['role']}.")
            elif action == 'toggle_active':
                member.is_active = not member.is_active
                member.save(update_fields=['is_active'])
                messages.success(request, f"{member.username} can {'now' if member.is_active else 'no longer'} sign in.")
            return redirect(reverse('dashboard-settings') + '#team')
        return redirect('dashboard-settings')
