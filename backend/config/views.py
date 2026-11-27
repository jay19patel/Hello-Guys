"""Project-level pages that span several apps."""

import platform
from datetime import datetime, time, timedelta

import django
from django.db.models import Count, Q
from django.utils import timezone
from django.views.generic import TemplateView

from cms.models import BlogPost, Project, Solution
from core.models import FollowUp
from crm.models import Customer, Meeting, Opportunity
from hello_guy_ai.models import Thread
from meta_connector.models import Contact, Message, PendingAIReply, PlatformAccount
from meta_connector.roles import RoleRequiredMixin


class HubPortalView(RoleRequiredMixin, TemplateView):
    """/ : pick a module (Meta inbox, CRM, CMS) with a few live numbers & daily action summary."""

    template_name = 'portal.html'

    def get_context_data(self, **kwargs):
        now = timezone.now()
        tz = timezone.get_current_timezone()
        today_date = timezone.localdate(now)
        today_start = datetime.combine(today_date, time.min, tzinfo=tz)
        today_end = today_start + timedelta(days=1)

        accounts = PlatformAccount.objects.filter(is_active=True).aggregate(
            accounts_count=Count('id'),
            wa_count=Count('id', filter=Q(platform=PlatformAccount.PLATFORM_WHATSAPP)),
            ig_count=Count('id', filter=Q(platform=PlatformAccount.PLATFORM_INSTAGRAM)),
        )
        opportunities = Opportunity.objects.aggregate(
            open_count=Count('id', filter=Q(status__in=Opportunity.OPEN)),
            new_count=Count('id', filter=Q(status=Opportunity.Status.NEW)),
        )
        fu_agg = FollowUp.objects.filter(status=FollowUp.Status.PENDING).aggregate(
            overdue=Count('id', filter=Q(due_at__lt=now)),
            today=Count('id', filter=Q(due_at__gte=today_start, due_at__lt=today_end)),
        )
        waiting_chats = Thread.objects.filter(needs_human=True).count()
        meetings_today = Meeting.objects.filter(
            status=Meeting.Status.SCHEDULED,
            start_at__gte=now - timedelta(hours=1),
            start_at__lt=today_end,
        ).count()
        meetings_upcoming = Meeting.objects.filter(status=Meeting.Status.SCHEDULED, start_at__gte=now).count()
        pending_approvals = PendingAIReply.objects.filter(status=PendingAIReply.STATUS_PENDING).count()

        return super().get_context_data(
            meta_module={
                **accounts,
                'contacts_count': Contact.objects.count(),
                'messages_count': Message.objects.count(),
                'pending_approvals': pending_approvals,
                'url': '/dashboard/',
            },
            crm_module={
                **opportunities,
                'customers_count': Customer.objects.count(),
                'meetings_count': meetings_upcoming,
                'waiting_chats': waiting_chats,
                'url': '/crm/',
            },
            cms_module={
                'solutions_count': Solution.objects.count(),
                'projects_count': Project.objects.count(),
                'featured_projects': Project.objects.filter(featured=True, is_active=True).count(),
                'blogs_count': BlogPost.objects.count(),
                'url': '/admin/cms/',
            },
            attention={
                'overdue_followups': fu_agg['overdue'] or 0,
                'today_followups': fu_agg['today'] or 0,
                'waiting_chats': waiting_chats,
                'pending_approvals': pending_approvals,
                'meetings_today': meetings_today,
            },
            python_version=platform.python_version(),
            django_version=django.__version__,
            **kwargs,
        )
