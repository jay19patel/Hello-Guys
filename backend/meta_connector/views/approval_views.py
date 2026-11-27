import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from core.safety import safe_next
from meta_connector.models import PendingAIReply, PlatformAccount
from meta_connector.roles import AGENT, RoleRequiredMixin
from meta_connector.services import SendError, send_message


logger = logging.getLogger(__name__)

# Platform text limits (IG/Messenger 1000, WhatsApp 4096).
MAX_REPLY_CHARS = {PlatformAccount.PLATFORM_WHATSAPP: 4096}
DEFAULT_MAX_REPLY_CHARS = 1000


class ApprovalQueueView(RoleRequiredMixin, ListView):
    """Queue of AI-drafted replies awaiting human approval before sending."""
    role = AGENT
    template_name = 'dashboard/approvals.html'
    context_object_name = 'approvals'
    paginate_by = 25

    def get_queryset(self):
        status = self.request.GET.get('status', PendingAIReply.STATUS_PENDING)
        qs = PendingAIReply.objects.select_related('contact', 'platform_account', 'trigger_message', 'reviewed_by')
        if status != 'all':
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['status_filter'] = self.request.GET.get('status', PendingAIReply.STATUS_PENDING)
        ctx['pending_count'] = PendingAIReply.objects.filter(status=PendingAIReply.STATUS_PENDING).count()
        return ctx


class ApprovalActionView(RoleRequiredMixin, View):
    """Approve & send (with optional edits) or reject a pending AI reply."""
    role = edit_role = AGENT

    def post(self, request, pk: int):
        item = get_object_or_404(PendingAIReply.objects.select_related('contact', 'platform_account'), pk=pk)
        next_url = safe_next(request, 'dashboard-approvals')
        action = request.POST.get('action')

        if item.status not in (PendingAIReply.STATUS_PENDING, PendingAIReply.STATUS_FAILED):
            messages.info(request, "This AI draft was already reviewed.")
            return redirect(next_url)

        if action == 'reject':
            rejected = PendingAIReply.objects.filter(
                pk=item.pk, status__in=(PendingAIReply.STATUS_PENDING, PendingAIReply.STATUS_FAILED),
            ).update(status=PendingAIReply.STATUS_REJECTED, reviewed_by=request.user, reviewed_at=timezone.now())
            messages.info(request, "AI draft rejected." if rejected else "This AI draft was already reviewed.")
            return redirect(next_url)

        if action == 'approve':
            text = (request.POST.get('reply_text') or item.draft_text).strip()
            if not text:
                messages.error(request, "Reply text cannot be empty.")
                return redirect(next_url)
            limit = MAX_REPLY_CHARS.get(item.platform_account.platform, DEFAULT_MAX_REPLY_CHARS)
            if len(text) > limit:
                messages.error(request, f"Reply is too long for {item.platform_account.get_platform_display()} ({len(text)}/{limit} characters).")
                return redirect(next_url)
            # Compare-and-set claim so a double click / second agent cannot send twice.
            claimed = PendingAIReply.objects.filter(
                pk=item.pk, status__in=(PendingAIReply.STATUS_PENDING, PendingAIReply.STATUS_FAILED),
            ).update(status=PendingAIReply.STATUS_SENDING)
            if not claimed:
                messages.info(request, "This AI draft was already reviewed.")
                return redirect(next_url)
            item.edited_text = text if text != item.draft_text else ''
            item.reviewed_by = request.user
            item.reviewed_at = timezone.now()
            try:
                sent_msg = send_message(item.contact, text, sent_by=request.user)
                item.status = PendingAIReply.STATUS_APPROVED
                item.sent_message = sent_msg
                item.error = ''
                item.save(update_fields=['status', 'edited_text', 'reviewed_by', 'reviewed_at', 'sent_message', 'error'])
                messages.success(request, f"Approved and sent reply to {item.contact}.")
            except Exception as exc:  # incl. SendError; never leave the row stuck in 'sending'
                if not isinstance(exc, SendError):
                    logger.exception("Approving AI reply %s failed", item.pk)
                item.status = PendingAIReply.STATUS_FAILED
                item.error = str(exc)[:500]
                item.save(update_fields=['status', 'edited_text', 'reviewed_by', 'reviewed_at', 'error'])
                messages.error(request, f"Could not send reply: {exc}")
            return redirect(next_url)

        return redirect(next_url)
