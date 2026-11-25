import calendar as pycal
import json
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from hello_guy_meet import services
from hello_guy_meet.forms import MeetRoomForm, MeetSettingsForm
from hello_guy_meet.models import MeetMinutes, MeetRoom, MeetSettings


class MeetStaffView(LoginRequiredMixin):
    """Base view for authenticated Hello Guy Meet pages (independent of any other app)."""

    def get_context_data(self, **kwargs):
        user = self.request.user
        is_admin = bool(user.is_superuser or user.is_staff)
        return super().get_context_data(
            meet_role="Admin" if is_admin else "Member",
            can_edit=user.is_authenticated,
            is_admin=is_admin,
            **kwargs,
        )


class MeetDashboardView(MeetStaffView, ListView):
    """Main Hello Guy Meet hub (/meet/): create rooms, copy invite links, join or review MoMs."""
    template_name = "meet/dashboard.html"
    context_object_name = "rooms"
    paginate_by = 20

    def get_queryset(self):
        return MeetRoom.objects.select_related("host", "minutes").order_by("-created_at")

    def get_context_data(self, **kwargs):
        cfg = MeetSettings.get_solo()
        initial = {"bot_name": cfg.default_bot_name, "ai_enabled": cfg.ai_auto_join_default}
        kwargs.setdefault("form", MeetRoomForm(initial=initial))
        ctx = super().get_context_data(meet_settings=cfg, **kwargs)
        try:
            from hello_guy_meet.realtime_server import hub
            for r in ctx.get("rooms", []):
                r.live_participants = hub.get_participants(r.room_code)
        except Exception:
            for r in ctx.get("rooms", []):
                r.live_participants = []
        return ctx

    def post(self, request):
        form = MeetRoomForm(request.POST)
        if not form.is_valid():
            self.object_list = self.get_queryset()
            return self.render_to_response(self.get_context_data(form=form))
        room = services.create_meet_room(
            title=form.cleaned_data["title"],
            host=request.user,
            scheduled_start=form.cleaned_data.get("scheduled_start"),
            scheduled_end=form.cleaned_data.get("scheduled_end"),
            ai_enabled=form.cleaned_data.get("ai_enabled", False),
            bot_name=form.cleaned_data.get("bot_name", ""),
            custom_prompt=form.cleaned_data.get("system_prompt", ""),
        )
        messages.success(request, f"Room '{room.title}' ({room.room_code}) created.")
        if request.POST.get("submit_action") == "create":
            return redirect("meet:dashboard")
        return redirect("meet:room", room_code=room.room_code)


class MeetCalendarView(MeetStaffView, TemplateView):
    """Google Calendar-style Schedule & Agenda view (/meet/calendar/) for MeetRooms."""
    template_name = "meet/calendar.html"

    def get_context_data(self, **kwargs):
        today = timezone.localdate()
        try:
            year = int(self.request.GET.get("year", today.year))
            month = int(self.request.GET.get("month", today.month))
            if not (1 <= month <= 12):
                raise ValueError
        except (TypeError, ValueError):
            year, month = today.year, today.month

        first_day = date(year, month, 1)
        prev_month = (first_day - timedelta(days=1)).replace(day=1)
        next_month = (first_day + timedelta(days=32)).replace(day=1)

        cal = pycal.Calendar(firstweekday=6)
        month_days = cal.monthdatescalendar(year, month)

        events_by_day: dict[date, list[dict]] = {}
        start_range = month_days[0][0]
        end_range = month_days[-1][-1] + timedelta(days=1)

        for r in MeetRoom.objects.filter(scheduled_start__date__gte=start_range, scheduled_start__date__lt=end_range).order_by("scheduled_start"):
            d = timezone.localtime(r.scheduled_start).date()
            events_by_day.setdefault(d, []).append({
                "kind": "meet",
                "title": r.title,
                "time": timezone.localtime(r.scheduled_start).strftime("%H:%M"),
                "url": reverse("meet:room", args=[r.room_code]),
                "status": r.status,
            })

        weeks = [
            [
                {
                    "date": d,
                    "iso": d.isoformat(),
                    "in_month": d.month == month,
                    "is_today": d == today,
                    "events": events_by_day.get(d, []),
                }
                for d in week
            ]
            for week in month_days
        ]

        cfg = MeetSettings.get_solo()
        kwargs.setdefault("form", MeetRoomForm(initial={"bot_name": cfg.default_bot_name, "ai_enabled": cfg.ai_auto_join_default}))
        return super().get_context_data(
            year=year,
            month=month,
            month_label=first_day.strftime("%B %Y"),
            prev_year=prev_month.year,
            prev_month=prev_month.month,
            next_year=next_month.year,
            next_month=next_month.month,
            weeks=weeks,
            upcoming_rooms=MeetRoom.objects.filter(status__in=[MeetRoom.Status.SCHEDULED, MeetRoom.Status.LIVE]).order_by("scheduled_start", "-created_at")[:10],
            **kwargs,
        )

    def post(self, request):
        form = MeetRoomForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        room = services.create_meet_room(
            title=form.cleaned_data["title"],
            host=request.user,
            scheduled_start=form.cleaned_data.get("scheduled_start"),
            scheduled_end=form.cleaned_data.get("scheduled_end"),
            ai_enabled=form.cleaned_data.get("ai_enabled", True),
            bot_name=form.cleaned_data.get("bot_name", ""),
            custom_prompt=form.cleaned_data.get("system_prompt", ""),
        )
        messages.success(request, f"Scheduled '{room.title}' ({room.room_code}).")
        return redirect("meet:calendar")


class MeetMinutesListView(MeetStaffView, ListView):
    """Minutes of Meeting (MoM) table viewer (/meet/mom/)."""
    template_name = "meet/mom_list.html"
    context_object_name = "minutes_list"
    paginate_by = 20

    def get_queryset(self):
        return MeetMinutes.objects.select_related("room", "created_by").order_by("-created_at")


class MeetRoomView(DetailView):
    """WebRTC Video/Audio & Pipecat AI Meeting Room (/meet/room/<room_code>/). Open to anyone with link."""
    model = MeetRoom
    slug_field = "room_code"
    slug_url_kwarg = "room_code"
    template_name = "meet/room.html"
    context_object_name = "room"

    def get_context_data(self, **kwargs):
        room = self.object
        cfg = MeetSettings.get_solo()
        ws_signal_url = f"/ws/meet/signal/{room.room_code}"
        ws_ai_url = f"/ws/meet/ai/{room.room_code}"
        is_host = self.request.user.is_authenticated
        display_name = (
            self.request.GET.get("name")
            or ((self.request.user.get_full_name() or self.request.user.username) if self.request.user.is_authenticated else "Guest")
        )
        is_ai_active_now = bool(room.ai_enabled) and (
            room.scheduled_start is None or timezone.now() >= room.scheduled_start
        )
        lk_url, _, _ = cfg.get_livekit_credentials()
        if lk_url.startswith("ws://livekit:"):
            lk_url = lk_url.replace("ws://livekit:", "ws://localhost:", 1)
        import uuid
        identity = (
            f"host-{self.request.user.pk}-{uuid.uuid4().hex[:4]}"
            if self.request.user.is_authenticated
            else f"guest-{uuid.uuid4().hex[:8]}"
        )
        livekit_token = services.create_livekit_token(
            room_code=room.room_code,
            identity=identity,
            name=display_name,
            is_host=is_host,
        )
        if room.status == MeetRoom.Status.SCHEDULED:
            room.status = MeetRoom.Status.LIVE
            room.started_at = room.started_at or timezone.now()
            room.save(update_fields=["status", "started_at", "updated_at"])
        meet_config = {
            "roomCode": room.room_code,
            "displayName": display_name,
            "needsName": (not is_host) and (not bool(self.request.GET.get("name"))),
            "isHost": is_host,
            "aiEnabled": is_ai_active_now,
            "botName": room.bot_name,
            "livekitUrl": lk_url,
            "livekitToken": livekit_token,
            "wsSignalUrl": ws_signal_url,
            "wsAiUrl": ws_ai_url,
            "iceServers": cfg.ice_servers or [],
            "initialChat": room.chat_messages or [],
            "initialTranscript": room.transcript or [],
            "toggleAiUrl": reverse("meet:room-toggle-ai", args=[room.room_code]),
            "endUrl": reverse("meet:room-end", args=[room.room_code]),
        }
        return super().get_context_data(
            meet_config=meet_config,
            is_host=is_host,
            display_name=display_name,
            meet_settings=cfg,
            **kwargs,
        )


class MeetToggleAIView(MeetStaffView, View):
    """POST /meet/api/rooms/<room_code>/toggle-ai/ — add/remove AI Assistant from Meeting Rooms."""
    http_method_names = ["post"]

    def post(self, request, room_code: str):
        room = get_object_or_404(MeetRoom, room_code=room_code)
        if room.status == MeetRoom.Status.ENDED:
            messages.warning(request, f"'{room.title}' has already ended and cannot be reopened.")
            return redirect("meet:dashboard")
        room.ai_enabled = not room.ai_enabled
        room.save(update_fields=["ai_enabled", "updated_at"])
        try:
            from asgiref.sync import async_to_sync
            from hello_guy_meet.realtime_server import hub
            if hub.rooms.get(room.room_code):
                async_to_sync(hub.broadcast)(room.room_code, {"type": "ai-state", "enabled": room.ai_enabled})
        except Exception:
            pass
        state = "added to" if room.ai_enabled else "removed from"
        messages.success(request, f"AI Assistant {state} '{room.title}'.")
        return redirect("meet:dashboard")


class MeetEndAPIView(MeetStaffView, View):
    """POST /meet/api/rooms/<room_code>/end/ — end meeting and save MoM to MeetMinutes table."""
    http_method_names = ["post"]

    def post(self, request, room_code: str):
        room = get_object_or_404(MeetRoom, room_code=room_code)
        transcript = None
        if request.content_type and "application/json" in request.content_type:
            try:
                payload = json.loads(request.body.decode("utf-8") or "{}")
                if isinstance(payload.get("transcript"), list):
                    transcript = payload["transcript"]
            except json.JSONDecodeError:
                pass
        minutes = services.finalize_room(room, transcript=transcript, user=request.user)
        try:
            from asgiref.sync import async_to_sync
            from hello_guy_meet.realtime_server import hub
            if hub.rooms.get(room.room_code):
                async_to_sync(hub.broadcast)(room.room_code, {"type": "meeting-ended"})
        except Exception:
            pass
        is_json = "application/json" in (request.content_type or "") or "application/json" in request.headers.get("Accept", "")
        if not is_json:
            messages.success(request, f"Ended '{room.title}' and saved MoM to database.")
            return redirect("meet:mom-list")
        return JsonResponse({
            "ok": True,
            "room_code": room.room_code,
            "status": room.status,
            "minutes_id": str(minutes.id),
            "call_summary": minutes.summary,
            "action_items": minutes.action_items,
        })


class MeetSettingsView(MeetStaffView, UserPassesTestMixin, TemplateView):
    """Dedicated Settings page (/meet/settings/) for WebRTC ICE servers & Pipecat AI."""
    template_name = "meet/settings.html"

    def test_func(self):
        return bool(self.request.user.is_superuser or self.request.user.is_staff)

    def get_context_data(self, **kwargs):
        cfg = MeetSettings.get_solo()
        kwargs.setdefault("form", MeetSettingsForm(instance=cfg))
        return super().get_context_data(cfg=cfg, **kwargs)

    def post(self, request):
        cfg = MeetSettings.get_solo()
        form = MeetSettingsForm(request.POST, instance=cfg)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        form.save()
        messages.success(request, "Hello Guy Meet settings saved.")
        return redirect("meet:settings")


class MeetSettingsTestView(MeetStaffView, UserPassesTestMixin, View):
    """POST /meet/settings/test/ — live check for STT key, LLM reply, and TTS test audio."""
    http_method_names = ["post"]

    def test_func(self):
        return bool(self.request.user.is_superuser or self.request.user.is_staff)

    def post(self, request):
        import asyncio
        import base64

        cfg = MeetSettings.get_solo()

        # 1. STT check
        if cfg.stt_provider == MeetSettings.STTProvider.GROQ:
            stt_ok = bool(cfg.get_api_key("GROQ_API_KEY"))
            stt_detail = "Groq API key configured" if stt_ok else "Missing GROQ_API_KEY"
        elif cfg.stt_provider == MeetSettings.STTProvider.DEEPGRAM:
            stt_ok = bool(cfg.get_api_key("DEEPGRAM_API_KEY"))
            stt_detail = "Deepgram API key configured" if stt_ok else "Missing DEEPGRAM_API_KEY"
        else:
            stt_ok = True
            stt_detail = "Browser Web Speech"

        # 2. LLM live test
        model, api_key = services.resolve_llm_model_and_key(cfg)
        llm_ok = False
        reply_text = ""
        llm_detail = ""
        if not api_key:
            llm_detail = f"No API key configured for model '{model}'"
        else:
            try:
                import litellm
                resp = litellm.completion(
                    model=model,
                    api_key=api_key,
                    messages=[
                        {"role": "system", "content": "Reply in 1 short friendly sentence confirming you are ready."},
                        {"role": "user", "content": "Hello, test connection."},
                    ],
                    max_tokens=60,
                )
                reply_text = resp.choices[0].message.content.strip()
                llm_ok = bool(reply_text)
                llm_detail = "Connected"
            except Exception as exc:
                llm_detail = str(exc)[:200]

        # 3. TTS live audio test (fixed to Edge TTS)
        tts_ok = False
        audio_b64 = ""
        tts_detail = ""
        speech_text = reply_text or f"Hello! {cfg.default_bot_name} audio test is working."
        try:
            import edge_tts

            async def _synth() -> bytes:
                comm = edge_tts.Communicate(speech_text, cfg.edge_voice or "en-IN-NeerjaNeural")
                buf = bytearray()
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        buf.extend(chunk["data"])
                return bytes(buf)

            raw_mp3 = asyncio.run(_synth())
            if raw_mp3:
                audio_b64 = base64.b64encode(raw_mp3).decode("ascii")
                tts_ok = True
                tts_detail = f"Edge TTS ({cfg.edge_voice})"
            else:
                tts_detail = "Edge TTS returned empty audio"
        except Exception as exc:
            tts_detail = str(exc)[:200]

        return JsonResponse({
            "ok": bool(stt_ok and llm_ok and tts_ok),
            "stt": {"provider": cfg.get_stt_provider_display(), "ok": stt_ok, "detail": stt_detail},
            "llm": {"model": model, "ok": llm_ok, "reply": reply_text, "detail": llm_detail},
            "tts": {"provider": "Edge TTS", "ok": tts_ok, "audio_b64": audio_b64, "detail": tts_detail},
        })
