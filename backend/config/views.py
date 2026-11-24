"""Project-level pages that span several apps."""

import platform

import django
from django.db.models import Count, Q
from django.utils import timezone
from django.views.generic import TemplateView

from cms.models import BlogPost, Project, Solution
from crm.models import Customer, Meeting, Opportunity
from hello_guy_ai.models import Thread
from meta_connector.models import Contact, Message, PlatformAccount
from meta_connector.roles import RoleRequiredMixin


class HubPortalView(RoleRequiredMixin, TemplateView):
    """/ : pick a module (Meta inbox, CRM, CMS) with a few live numbers. Any dashboard role can open it."""
    template_name = 'portal.html'

    def get_context_data(self, **kwargs):
        accounts = PlatformAccount.objects.filter(is_active=True).aggregate(
            accounts_count=Count('id'),
            wa_count=Count('id', filter=Q(platform=PlatformAccount.PLATFORM_WHATSAPP)),
            ig_count=Count('id', filter=Q(platform=PlatformAccount.PLATFORM_INSTAGRAM)),
        )
        opportunities = Opportunity.objects.aggregate(
            open_count=Count('id', filter=Q(status__in=Opportunity.OPEN)),
            new_count=Count('id', filter=Q(status=Opportunity.Status.NEW)))
        return super().get_context_data(
            meta_module={**accounts, 'contacts_count': Contact.objects.count(),
                         'messages_count': Message.objects.count(), 'url': '/dashboard/'},
            crm_module={**opportunities, 'customers_count': Customer.objects.count(),
                        'meetings_count': Meeting.objects.filter(status=Meeting.Status.SCHEDULED,
                                                                 start_at__gte=timezone.now()).count(),
                        'waiting_chats': Thread.objects.filter(needs_human=True).count(), 'url': '/crm/'},
            cms_module={'solutions_count': Solution.objects.count(), 'projects_count': Project.objects.count(),
                        'featured_projects': Project.objects.filter(featured=True, is_active=True).count(),
                        'blogs_count': BlogPost.objects.count(), 'url': '/admin/cms/'},
            python_version=platform.python_version(),
            django_version=django.__version__,
            **kwargs,
        )
