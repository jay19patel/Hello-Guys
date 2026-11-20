from meta_connector.roles import VIEWER, role_required
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView, View
from django.db.models import Max
from django.http import JsonResponse
from meta_connector.models import Message, Contact, PlatformAccount, WebhookLog
from meta_connector.services.token_service import TokenService
from .insights_views import insights_context, parse_days


@method_decorator(role_required(VIEWER), name='dispatch')
class DashboardIndexView(TemplateView):
    """
    Renders the unified dashboard HTML UI overview.
    Accessible at: /dashboard/
    """
    template_name = 'dashboard/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(insights_context(parse_days(self.request)))
        context['total_contacts'] = Contact.objects.count()
        context['recent_messages'] = Message.objects.select_related('platform_account', 'contact', 'sent_by').order_by('-created_at')[:12]
        context['all_contacts'] = Contact.objects.select_related('platform_account').prefetch_related('messages').order_by('-last_message_at', '-first_seen')[:100]
        TokenService.refresh_stale()
        context['token_alerts'] = TokenService.alerts()

        return context


@method_decorator(role_required(VIEWER), name='dispatch')
class DashboardOverviewAPIView(View):
    """
    JSON REST metrics endpoint.
    Accessible at: /api/meta/dashboard/api/overview/
    """
    def get(self, request, *args, **kwargs):
        data = {
            'total_platform_accounts': PlatformAccount.objects.filter(is_active=True).count(),
            'total_contacts': Contact.objects.count(),
            'total_messages': Message.objects.count(),
            'incoming_messages': Message.objects.filter(direction='incoming').count(),
            'outgoing_messages': Message.objects.filter(direction='outgoing').count(),
            'total_webhooks': WebhookLog.objects.count(),
        }
        # Changes on any new/updated message, new contact, or new webhook; the dashboard polls this.
        latest = Message.objects.aggregate(m=Max('updated_at'))['m']
        latest_contact = Contact.objects.aggregate(c=Max('last_message_at'))['c']
        data_version = f"{latest}|{latest_contact}|{data['total_contacts']}|{data['total_webhooks']}"
        return JsonResponse({'status': 'ok', 'metrics': data, 'version': data_version})
