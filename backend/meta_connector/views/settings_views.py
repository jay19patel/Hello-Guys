from django.contrib import messages
from meta_connector.roles import ADMIN, ROLES, get_role, role_required, set_role
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from meta_connector.services.maintenance import retry_webhooks
from meta_connector.forms import MetaConfigForm, PlatformAccountForm, TeamMemberForm
from meta_connector.models import MetaConfig, PlatformAccount
from meta_connector.services.token_service import TokenService


@role_required(ADMIN)
@require_POST
def webhook_actions_view(request):
    """Banner buttons: retry failed/stuck webhooks, or dismiss the bad-signature warning."""
    if request.POST.get('action') == 'retry':
        fixed, failing = retry_webhooks()
        level = messages.success if not failing else messages.warning
        level(request, f"Re-processed {fixed} webhook(s)." + (f" {failing} still failing; open Django Admin → Webhook logs to see why." if failing else ''))
    elif request.POST.get('action') == 'dismiss_signature':
        MetaConfig.objects.filter(pk=MetaConfig.get_solo().pk).update(signature_failures=0, last_signature_failure_at=None)
        messages.success(request, "Signature warning cleared.")
    back = request.POST.get('next', '')
    return redirect(back if url_has_allowed_host_and_scheme(back, {request.get_host()}) else 'dashboard')


@role_required(ADMIN)
def settings_view(request):
    """Dashboard > Settings: Meta app config + platform accounts, all stored in the DB."""
    config = MetaConfig.get_solo()
    edit_id = request.GET.get('edit') or request.POST.get('account_id')
    edit_account = get_object_or_404(PlatformAccount, pk=edit_id) if edit_id else None

    config_form = MetaConfigForm(instance=config)
    account_form = PlatformAccountForm(instance=edit_account)
    team_form = TeamMemberForm()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'config':
            config_form = MetaConfigForm(request.POST, instance=config)
            if config_form.is_valid():
                config_form.save()
                messages.success(request, "Meta app settings saved.")
                return redirect('dashboard-settings')
        elif action == 'account':
            account_form = PlatformAccountForm(request.POST, instance=edit_account)
            if account_form.is_valid():
                account = account_form.save()
                messages.success(request, f"Account '{account.display_name}' saved. {account_form.token_note}".strip())
                return redirect('dashboard-settings')
        elif action == 'check_token' and edit_account:
            TokenService.refresh(edit_account)
            health = edit_account.token_health
            level = messages.success if health == 'ok' else messages.warning
            level(request, f"Token for '{edit_account.display_name}' checked: {health.upper()}. {edit_account.token_error}".strip())
            return redirect('dashboard-settings')
        elif action == 'add_member':
            team_form = TeamMemberForm(request.POST)
            if team_form.is_valid():
                d = team_form.cleaned_data
                user = User.objects.create_user(d['username'], password=d['password'], first_name=d['first_name'])
                set_role(user, d['role'])
                messages.success(request, f"Added {user.username} as {d['role']}.")
                return redirect(reverse('dashboard-settings') + '#team')
        elif action in ('set_role', 'toggle_active'):
            member = get_object_or_404(User, pk=request.POST.get('user_id'))
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
        elif action == 'delete' and edit_account:
            if edit_account.messages.exists() or edit_account.contacts.exists():
                # Keep message history: deactivate instead of cascading deletes.
                edit_account.is_active = False
                edit_account.save(update_fields=['is_active'])
                messages.warning(request, f"'{edit_account.display_name}' has history, so it was deactivated instead of deleted.")
            else:
                edit_account.delete()
                messages.success(request, "Account deleted.")
            return redirect('dashboard-settings')

    TokenService.refresh_stale()
    return render(request, 'dashboard/settings.html', {
        'token_alerts': TokenService.alerts(),
        'team': [(u, get_role(u)) for u in User.objects.order_by('-is_active', 'username').prefetch_related('groups')],
        'team_form': team_form,
        'roles': ROLES,
        'config': config,
        'config_form': config_form,
        'account_form': account_form,
        'edit_account': edit_account,
        'accounts': PlatformAccount.objects.all(),
        'webhook_url': request.build_absolute_uri(reverse('meta_connector:meta-webhook')),
    })
