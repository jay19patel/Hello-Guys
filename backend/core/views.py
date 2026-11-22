import logging
import platform
from typing import Any
import django
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from meta_connector.models import PlatformAccount, Contact, Message
from meta_connector.services.maintenance import webhook_health
from cms.models import (
    InquirySubmission,
    Project,
    BlogPost,
    SiteSettings,
    Solution,
)

logger = logging.getLogger(__name__)


class HubPortalView(LoginRequiredMixin, TemplateView):
    """
    Root module selector hub (mounted at /).
    Provides access to Meta Connector and CMS Studio modules with live status telemetry.
    """
    template_name = 'core/portal.html'
    login_url = 'dashboard-login'

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)

        # ------------------------------------------------------------------
        # 1. Meta Connector module metrics
        # ------------------------------------------------------------------
        active_accounts = PlatformAccount.objects.filter(is_active=True)
        wa_count = active_accounts.filter(platform=PlatformAccount.PLATFORM_WHATSAPP).count()
        ig_count = active_accounts.filter(platform=PlatformAccount.PLATFORM_INSTAGRAM).count()
        fb_count = active_accounts.filter(platform=PlatformAccount.PLATFORM_FACEBOOK).count()
        total_contacts = Contact.objects.count()
        total_messages = Message.objects.count()
        
        health_info = None
        try:
            health_info = webhook_health()
        except Exception as exc:
            logger.warning("Could not evaluate webhook health in hub portal: %s", exc)

        meta_module = {
            'name': 'Meta Connector',
            'tagline': 'WhatsApp & Instagram Omnichannel Automation',
            'accounts_count': active_accounts.count(),
            'wa_count': wa_count,
            'ig_count': ig_count,
            'fb_count': fb_count,
            'contacts_count': total_contacts,
            'messages_count': total_messages,
            'webhook_healthy': not health_info,
            'url': '/dashboard/',
        }

        # ------------------------------------------------------------------
        # 2. CMS Studio module metrics
        # ------------------------------------------------------------------
        total_inquiries = InquirySubmission.objects.count()
        unreplied_inquiries = InquirySubmission.objects.filter(is_replied=False).count()
        total_projects = Project.objects.count()
        featured_projects = Project.objects.filter(featured=True, is_active=True).count()
        total_blogs = BlogPost.objects.count()
        total_solutions = Solution.objects.count()
        site_settings = SiteSettings.get_solo()

        cms_module = {
            'name': 'CMS Studio',
            'tagline': 'Decoupled Content Engine & Client Inquiries CRM',
            'inquiries_count': total_inquiries,
            'unreplied_inquiries': unreplied_inquiries,
            'projects_count': total_projects,
            'featured_projects': featured_projects,
            'blogs_count': total_blogs,
            'solutions_count': total_solutions,
            'brand_name': site_settings.brand_name,
            'url': '/admin/cms/',
        }

        # ------------------------------------------------------------------
        # 3. System Telemetry
        # ------------------------------------------------------------------
        context.update({
            'meta_module': meta_module,
            'cms_module': cms_module,
            'python_version': platform.python_version(),
            'django_version': django.__version__,
            'page_title': 'Workspace Hub',
        })
        return context

