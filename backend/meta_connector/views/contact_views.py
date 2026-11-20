from datetime import timedelta
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from meta_connector.roles import AGENT, VIEWER, has_role, role_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from meta_connector.models import Contact, Message
from meta_connector.services.message_service import MessageService

REPLY_WINDOW = timedelta(hours=24)  # Meta only allows free-form replies this long after the contact's last DM


@role_required(VIEWER)
def contact_detail_view(request, pk):
    """One contact: details, full conversation (DMs + comments + auto-DMs), and a reply box."""
    contact = get_object_or_404(Contact.objects.select_related('platform_account'), pk=pk)
    account = contact.platform_account

    if request.method == 'POST':
        if not has_role(request.user, AGENT):
            raise PermissionDenied("Viewers can't send messages.")
        text = request.POST.get('text', '').strip()
        if not text:
            messages.error(request, "Type a message first.")
        elif len(text) > 1000:
            messages.error(request, "Message is too long (max 1000 characters).")
        else:
            _, ok, error = MessageService.send_message(
                platform=account.platform, to=contact.platform_user_id, text=text, account_id=account.pk,
                sent_by=request.user)
            if ok:
                messages.success(request, "Message sent.")
            else:
                messages.error(request, f"Not sent. Meta said: {error}")
        return redirect('dashboard-contact', pk=pk)

    conversation = list(contact.messages.select_related('sent_by').order_by('created_at')[:500])
    counts = contact.messages.aggregate(
        incoming=Count('id', filter=Q(direction=Message.DIRECTION_INCOMING) & ~Q(message_type=Message.TYPE_COMMENT)),
        outgoing=Count('id', filter=Q(direction=Message.DIRECTION_OUTGOING)),
        comments=Count('id', filter=Q(message_type=Message.TYPE_COMMENT)),
    )
    last_dm = (contact.messages.filter(direction=Message.DIRECTION_INCOMING)
               .exclude(message_type=Message.TYPE_COMMENT).order_by('-created_at').first())
    window_closes = last_dm.created_at + REPLY_WINDOW if last_dm else None

    return render(request, 'dashboard/contact_detail.html', {
        'contact': contact,
        'is_wa': account.platform == account.PLATFORM_WHATSAPP,
        'display': contact.name or contact.username or contact.phone or contact.platform_user_id,
        'account': account,
        'conversation': conversation,
        'counts': counts,
        'window_open': bool(window_closes and window_closes > timezone.now()),
        'window_closes': window_closes,
        'has_dm': bool(last_dm),
        'auto_dm_logs': contact.auto_dm_logs.order_by('-created_at')[:10],
        'instagram_handle': contact.username if contact.username and contact.username != contact.platform_user_id else '',
    })
