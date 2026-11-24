import json

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView

from crm.models import Customer, Opportunity
from crm.views import CRMView
from meta_connector.roles import AGENT
from voice_agent import services
from voice_agent.models import VoiceBotSession


def _parse_payload(request) -> dict:
    if request.content_type and "application/json" in request.content_type:
        try:
            return json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}
    return request.POST.dict()


class VoiceBotStartAPIView(CRMView, View):
    """POST /api/voice-bot/start"""
    role = AGENT
    http_method_names = ["post"]

    def post(self, request):
        data = _parse_payload(request)
        lead_id = data.get("lead_id") or data.get("customer_id")
        opp_id = data.get("opportunity_id")
        meeting_id = data.get("meeting_id")
        try:
            session = services.start_bot_session(
                meet_code_or_url=data.get("meet_code_or_url", ""),
                phone_number=data.get("phone_number", ""),
                bot_name=data.get("bot_name") or "Hello Guys AI Assistant",
                custom_prompt=data.get("custom_prompt", ""),
                mode=data.get("mode") or VoiceBotSession.Mode.GOOGLE_MEET,
                provider=data.get("provider") or VoiceBotSession.Provider.MEETINGBAAS,
                customer_id=int(lead_id) if str(lead_id or "").isdigit() else None,
                opportunity_id=int(opp_id) if str(opp_id or "").isdigit() else None,
                meeting_id=int(meeting_id) if str(meeting_id or "").isdigit() else None,
                user=request.user,
            )
        except services.VoiceBotError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)

        return JsonResponse(
            {
                "ok": True,
                "session_id": str(session.id),
                "status": session.status,
                "status_label": session.get_status_display(),
                "meeting_url": session.meeting_url,
                "ws_url": services.build_ws_url(session),
                "external_bot_id": session.external_bot_id,
                "connection_logs": session.connection_logs,
            },
            status=201,
        )


class VoiceBotStopAPIView(CRMView, View):
    """POST /api/voice-bot/stop"""
    role = AGENT
    http_method_names = ["post"]

    def post(self, request):
        data = _parse_payload(request)
        session = get_object_or_404(VoiceBotSession, pk=data.get("session_id"))
        session = services.stop_bot_session(session, user=request.user)
        is_json = "application/json" in (request.content_type or "") or "application/json" in request.headers.get("Accept", "")
        if not is_json:
            messages.success(request, f"Stopped '{session.bot_name}' and saved summary to CRM.")
            return redirect(request.POST.get("next") or "crm:meetings")
        return JsonResponse(
            {
                "ok": True,
                "session_id": str(session.id),
                "status": session.status,
                "status_label": session.get_status_display(),
                "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                "call_summary": session.call_summary,
                "connection_logs": session.connection_logs,
            }
        )


class VoiceBotStatusAPIView(CRMView, View):
    """GET /api/voice-bot/status/<session_id>"""
    http_method_names = ["get"]

    def get(self, request, session_id):
        session = get_object_or_404(VoiceBotSession, pk=session_id)
        return JsonResponse(
            {
                "ok": True,
                "session_id": str(session.id),
                "status": session.status,
                "status_label": session.get_status_display(),
                "mode": session.mode,
                "provider": session.provider,
                "meeting_url": session.meeting_url,
                "bot_name": session.bot_name,
                "external_bot_id": session.external_bot_id,
                "connection_logs": session.connection_logs,
                "transcript": session.transcript,
                "call_summary": session.call_summary,
                "error_message": session.error_message,
            }
        )


class AIMeetingsPanelView(CRMView, ListView):
    """Dedicated CRM panel (/crm/ai-meetings/) for launching & monitoring AI Voice Bot sessions."""
    template_name = "crm/ai_meetings.html"
    context_object_name = "sessions"
    paginate_by = 20

    def get_queryset(self):
        return VoiceBotSession.objects.select_related("customer", "opportunity", "meeting").order_by("-created_at")

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            customers=Customer.objects.order_by("name")[:100],
            opportunities=Opportunity.objects.select_related("customer").filter(status__in=Opportunity.OPEN)[:50],
            modes=VoiceBotSession.Mode.choices,
            providers=VoiceBotSession.Provider.choices,
            **kwargs,
        )
