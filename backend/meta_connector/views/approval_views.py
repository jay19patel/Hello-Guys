from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from meta_connector.models import PendingAIReply
from meta_connector.roles import AGENT, RoleRequiredMixin
from meta_connector.services import SendError, send_message


class ApprovalQueueView(RoleRequiredMixin, ListView):
    """Queue of AI-drafted replies awaiting human approval before sending."""
    required_role = AGENT
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
    required_role = AGENT

    def post(self, request, pk: int):
        item = get_object_or_404(PendingAIReply.objects.select_related('contact', 'platform_account'), pk=pk)
        next_url = request.POST.get('next') or 'dashboard-approvals'
        action = request.POST.get('action')

        if item.status != PendingAIReply.STATUS_PENDING:
            messages.info(request, "This AI draft was already reviewed.")
            return redirect(next_url)

        if action == 'reject':
            item.status = PendingAIReply.STATUS_REJECTED
            item.reviewed_by = request.user
            item.reviewed_at = timezone.now()
            item.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
            messages.info(request, "AI draft rejected.")
            return redirect(next_url)

        if action == 'approve':
            text = (request.POST.get('reply_text') or item.draft_text).strip()
            if not text:
                messages.error(request, "Reply text cannot be empty.")
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
            except SendError as exc:
                item.status = PendingAIReply.STATUS_FAILED
                item.error = str(exc)[:500]
                item.save(update_fields=['status', 'edited_text', 'reviewed_by', 'reviewed_at', 'error'])
                messages.error(request, f"Could not send reply: {exc}")
            return redirect(next_url)

        return redirect(next_url)
