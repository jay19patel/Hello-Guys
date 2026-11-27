from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.views import View
from meta_connector.roles import ADMIN, RoleRequiredMixin, has_role
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from meta_connector.forms import AutoDMRuleForm
from meta_connector.models import AutoDMLog, AutoDMRule, Contact, Message, PlatformAccount
from meta_connector.services.instagram_service import InstagramService
from meta_connector.services.token_service import TokenService


def _prefix(media_id: str) -> str:
    # Several rule forms share one page, so each gets its own field-name prefix.
    return f"m{media_id or 'default'}"


class AccountDetailView(RoleRequiredMixin, View):
    """Account page: channel profile, KPI cards, contacts, messages, and (Instagram) Reels/Auto-DM."""

    def get(self, request, pk: int):
        account = get_object_or_404(PlatformAccount, pk=pk)
        TokenService.refresh_stale()
        context = self._base_context(account)
        if account.platform == PlatformAccount.PLATFORM_INSTAGRAM:
            context.update(self._instagram_context(account, request.GET.get('after', '')))
        return render(request, 'dashboard/account_detail.html', context)

    def post(self, request, pk: int):
        account = get_object_or_404(PlatformAccount, pk=pk)
        if account.platform != PlatformAccount.PLATFORM_INSTAGRAM:
            return self.get(request, pk)
        if request.POST.get('action') == 'remove' and not has_role(request.user, ADMIN):
            raise PermissionDenied("Your role can't make this change.")

        after = request.GET.get('after', '')
        page_url = reverse('dashboard-account', args=[pk]) + ('?' + urlencode({'after': after}) if after else '')
        media_id = request.POST.get('media_id', '').strip()
        rule = AutoDMRule.objects.filter(platform_account=account, media_id=media_id).first()
        anchor = f"#media-{media_id}" if media_id else "#default-rule"

        if request.POST.get('action') == 'remove':
            if rule:
                rule.delete()
            messages.success(request, "Post rule removed. This post now uses the default message.")
            return redirect(page_url + anchor)

        form = AutoDMRuleForm(request.POST, instance=rule, prefix=_prefix(media_id))
        if form.is_valid():
            rule = form.save(commit=False)
            rule.platform_account, rule.media_id = account, media_id
            rule.save()
            messages.success(request, "Auto-DM saved." if rule.is_enabled else "Saved. Auto-DM is OFF for this post.")
        else:
            errors = '; '.join(f"{form.fields[f].label if f in form.fields else f}: {' '.join(e)}" for f, e in form.errors.items())
            messages.error(request, f"Not saved. {errors}")
        return redirect(page_url + anchor)

    @staticmethod
    def _base_context(account: PlatformAccount) -> dict:
        msg_stats = Message.objects.filter(platform_account=account).aggregate(
            total=Count('id'),
            incoming=Count('id', filter=Q(direction=Message.DIRECTION_INCOMING)),
            outgoing=Count('id', filter=Q(direction=Message.DIRECTION_OUTGOING)),
        )
        return {
            'account': account,
            'total_messages': msg_stats['total'] or 0,
            'incoming_messages': msg_stats['incoming'] or 0,
            'outgoing_messages': msg_stats['outgoing'] or 0,
            'total_contacts': Contact.objects.filter(platform_account=account).count(),
            'account_contacts': list(
                Contact.objects.filter(platform_account=account)
                .annotate(msg_count=Count('messages'))
                .order_by('-last_message_at', '-first_seen')[:25]
            ),
            'account_messages': list(
                Message.objects.filter(platform_account=account)
                .select_related('contact', 'sent_by')
                .order_by('-created_at')[:25]
            ),
            'token_alerts': TokenService.alerts(),
        }

    @staticmethod
    def _instagram_context(account: PlatformAccount, after: str) -> dict:
        rules = {r.media_id: r for r in AutoDMRule.objects.filter(platform_account=account)}
        default_rule = rules.get('')
        sent_counts = dict(
            AutoDMLog.objects.filter(platform_account=account, status=AutoDMLog.STATUS_SENT)
            .values_list('media_id').annotate(n=Count('id'))
        )

        profile_ok, profile, profile_error = InstagramService.get_profile(account)
        media_ok, media_page, media_error = InstagramService.list_media(account, after=after)
        media = media_page.get('data', []) if media_ok else []
        for item in media:
            item['rule'] = rules.get(item['id'])
            # Reels give thumbnail_url; images give media_url; some Reels (e.g. with licensed audio) give neither.
            item['thumb'] = item.get('thumbnail_url') or (item.get('media_url') if item.get('media_type') != 'VIDEO' else '')
            item['form'] = AutoDMRuleForm(instance=item['rule'], prefix=_prefix(item['id']))
            item['dm_sent'] = sent_counts.get(item['id'], 0)

        logs = list(AutoDMLog.objects.filter(platform_account=account).select_related('contact')[:25])
        permalinks = {item['id']: item.get('permalink') for item in media}
        for log in logs:
            log.permalink = permalinks.get(log.media_id)

        return {
            'profile': profile if profile_ok else {},
            'profile_error': profile_error,
            'media': media,
            'media_error': media_error,
            'next_cursor': media_page.get('paging', {}).get('cursors', {}).get('after') if media_page.get('paging', {}).get('next') else '',
            'is_first_page': not after,
            'default_rule': default_rule,
            'default_form': AutoDMRuleForm(instance=default_rule, prefix=_prefix('')),
            'logs': logs,
        }
