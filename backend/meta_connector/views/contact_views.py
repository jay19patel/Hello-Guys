from datetime import timedelta

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import redirect
from django.utils import timezone
from django.views.generic import DetailView

from meta_connector.models import Contact, Message
from meta_connector.roles import RoleRequiredMixin
from meta_connector.services import SendError, send_message

REPLY_WINDOW = timedelta(hours=24)  # Meta only allows free-form replies this long after the contact's last DM


class ContactDetailView(RoleRequiredMixin, DetailView):
    """One contact: details, full conversation (DMs + comments + auto-DMs), and a reply box (Agents)."""
    queryset = Contact.objects.select_related('platform_account')
    template_name = 'dashboard/contact_detail.html'

    def post(self, request, pk):
        contact = self.get_object()
        action = request.POST.get('action')
        if action == 'create_customer':
            from crm import services as crm_services
            customer = crm_services.get_or_create_customer(
                name=contact.name or contact.username or contact.phone or f"Lead {contact.pk}",
                email=contact.email,
                phone=contact.phone,
                instagram_username=contact.username,
                contact=contact,
                source=crm_services.THREAD_SOURCES.get(contact.platform, 'manual'),
                created_by=request.user,
            )
            messages.success(request, f"Linked to CRM customer: {customer.name}")
            return redirect('crm:customer', pk=customer.pk)
        if action == 'link_customer':
            from crm.models import Customer
            customer_id = request.POST.get('customer_id')
            customer = Customer.objects.filter(pk=customer_id).first()
            if customer:
                customer.contacts.add(contact)
                messages.success(request, f"Linked {contact} to {customer.name}.")
            else:
                messages.error(request, "Customer not found.")
            return redirect('dashboard-contact', pk=pk)

        text = request.POST.get('text', '').strip()
        if not text:
            messages.error(request, "Type a message first.")
        elif len(text) > 1000:
            messages.error(request, "Message is too long (max 1000 characters).")
        else:
            try:
                send_message(contact, text, sent_by=request.user)
                messages.success(request, "Message sent.")
            except SendError as exc:
                messages.error(request, f"Not sent. Meta said: {exc}")
        return redirect('dashboard-contact', pk=pk)

    def get_context_data(self, **kwargs):
        contact, now = self.object, timezone.now()
        account = contact.platform_account
        # Newest 500, shown oldest-first. shortcut: older history isn't reachable here; add "load earlier" paging if chats get that long.
        conversation = list(reversed(contact.messages.select_related('sent_by').order_by('-created_at')[:500]))
        counts = contact.messages.aggregate(
            incoming=Count('id', filter=Q(direction=Message.DIRECTION_INCOMING) & ~Q(message_type=Message.TYPE_COMMENT)),
            outgoing=Count('id', filter=Q(direction=Message.DIRECTION_OUTGOING)),
            comments=Count('id', filter=Q(message_type=Message.TYPE_COMMENT)),
        )
        last_dm = (contact.messages.filter(direction=Message.DIRECTION_INCOMING)
                   .exclude(message_type=Message.TYPE_COMMENT).order_by('-created_at').first())
        window_closes = last_dm.created_at + REPLY_WINDOW if last_dm else None
        crm_customer = contact.crm_customers.first()
        from crm.models import Customer
        return super().get_context_data(
            is_wa=account.platform == account.PLATFORM_WHATSAPP,
            display=contact.name or contact.username or contact.phone or contact.platform_user_id,
            account=account,
            conversation=conversation,
            counts=counts,
            window_open=bool(window_closes and window_closes > now),
            window_closed=bool(window_closes and window_closes <= now),
            window_closes=window_closes,
            has_dm=bool(last_dm),
            auto_dm_logs=contact.auto_dm_logs.order_by('-created_at')[:10],
            pending_approvals=contact.pending_ai_replies.filter(status='pending').order_by('-created_at'),
            crm_customer=crm_customer,
            all_customers=Customer.objects.order_by('name')[:100] if not crm_customer else [],
            instagram_handle=contact.username if contact.username and contact.username != contact.platform_user_id else '',
            **kwargs,
        )
