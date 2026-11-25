"""Production-grade WebRTC Signaling & Pipecat AI Voice Server for Hello Guy Meet.

Run alongside Django:
    cd backend && ../.venv/bin/uvicorn hello_guy_meet.realtime_server:app --host 0.0.0.0 --port 8001
"""

import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from asgiref.sync import sync_to_async
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_ENV", "local")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings as django_settings  # noqa: E402
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler  # noqa: E402
from django.core.asgi import get_asgi_application  # noqa: E402
from django.utils import timezone  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from hello_guy_meet.models import MeetRoom, MeetSettings  # noqa: E402
from hello_guy_meet.services import finalize_room  # noqa: E402

logger = logging.getLogger(__name__)
app = FastAPI(title="Hello Guy Meet Realtime Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class RoomHub:
    """In-memory WebRTC peer registry per room_code."""

    def __init__(self) -> None:
        self.rooms: dict[str, dict[str, dict[str, Any]]] = {}

    async def join(self, room_code: str, peer_id: str, ws: WebSocket, name: str, is_host: bool) -> list[dict[str, Any]]:
        peers = self.rooms.setdefault(room_code, {})
        existing = [
            {"peer_id": pid, "name": info["name"], "is_host": info["is_host"]}
            for pid, info in peers.items()
        ]
        peers[peer_id] = {"ws": ws, "name": name, "is_host": is_host}
        await self.broadcast(room_code, {"type": "peer-joined", "peer_id": peer_id, "name": name, "is_host": is_host}, exclude=peer_id)
        return existing

    async def leave(self, room_code: str, peer_id: str) -> None:
        peers = self.rooms.get(room_code, {})
        peers.pop(peer_id, None)
        if not peers:
            self.rooms.pop(room_code, None)
        else:
            await self.broadcast(room_code, {"type": "peer-left", "peer_id": peer_id})

    async def send_to(self, room_code: str, target_id: str, payload: dict[str, Any]) -> None:
        peer = self.rooms.get(room_code, {}).get(target_id)
        if peer:
            try:
                await peer["ws"].send_json(payload)
            except Exception:
                pass

    async def broadcast(self, room_code: str, payload: dict[str, Any], exclude: str | None = None) -> None:
        for pid, info in list(self.rooms.get(room_code, {}).items()):
            if pid == exclude:
                continue
            try:
                await info["ws"].send_json(payload)
            except Exception:
                pass


hub = RoomHub()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws/meet/signal/{room_code}")
async def webrtc_signal(websocket: WebSocket, room_code: str) -> None:
    """WebRTC mesh signaling: peer discovery, SDP offer/answer, ICE candidates, chat, and live transcript."""
    await websocket.accept()
    room = await sync_to_async(MeetRoom.objects.filter(room_code=room_code).first)()
    if room is None:
        await websocket.close(code=4004, reason="Room not found")
        return

    peer_id = str(uuid.uuid4())[:8]
    name = websocket.query_params.get("name", "Guest")[:80]
    is_host = websocket.query_params.get("host") == "1"

    def _mark_live() -> None:
        if room.status == MeetRoom.Status.SCHEDULED:
            room.status = MeetRoom.Status.LIVE
            room.started_at = room.started_at or timezone.now()
            room.save(update_fields=["status", "started_at", "updated_at"])

    await sync_to_async(_mark_live)()
    existing_peers = await hub.join(room_code, peer_id, websocket, name, is_host)
    await websocket.send_json({"type": "welcome", "peer_id": peer_id, "peers": existing_peers})

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type in ("offer", "answer", "ice-candidate"):
                target = msg.get("target")
                if target:
                    msg["sender"] = peer_id
                    msg["sender_name"] = name
                    await hub.send_to(room_code, target, msg)
            elif msg_type in ("mute-state", "ai-state"):
                msg["sender"] = peer_id
                msg["sender_name"] = name
                await hub.broadcast(room_code, msg, exclude=peer_id)
            elif msg_type == "chat":
                text = (msg.get("text") or "").strip()
                if text:
                    chat_entry = {
                        "sender": name,
                        "text": text,
                        "at": timezone.localtime(timezone.now()).strftime("%H:%M"),
                    }

                    def _save_chat() -> None:
                        r = MeetRoom.objects.get(pk=room.pk)
                        msgs = list(r.chat_messages or [])
                        msgs.append(chat_entry)
                        r.chat_messages = msgs[-200:]
                        r.save(update_fields=["chat_messages", "updated_at"])

                    await sync_to_async(_save_chat)()
                    await hub.broadcast(room_code, {"type": "chat", **chat_entry}, exclude=peer_id)
            elif msg_type == "transcript":
                text = (msg.get("text") or "").strip()
                if text:
                    speaker = msg.get("speaker") or name
                    entry = {"role": "user", "speaker": speaker, "text": text}

                    def _append() -> None:
                        r = MeetRoom.objects.get(pk=room.pk)
                        t = list(r.transcript or [])
                        t.append(entry)
                        r.transcript = t
                        r.save(update_fields=["transcript", "updated_at"])

                    await sync_to_async(_append)()
                    await hub.broadcast(room_code, {"type": "transcript", **entry}, exclude=peer_id)
    except WebSocketDisconnect:
        await hub.leave(room_code, peer_id)
    except Exception:
        logger.exception("Signaling error in room %s", room_code)
        await hub.leave(room_code, peer_id)


@app.websocket("/ws/meet/ai/{room_code}")
async def meet_ai_ws(websocket: WebSocket, room_code: str) -> None:
    """Pipecat AI Voice Assistant WebSocket (Silero VAD + STT -> LLM -> TTS) with text fallback."""
    await websocket.accept()
    room = await sync_to_async(MeetRoom.objects.filter(room_code=room_code).first)()
    if room is None:
        await websocket.close(code=4004, reason="Room not found")
        return

    cfg = await sync_to_async(MeetSettings.get_solo)()
    transcript: list[dict[str, str]] = list(room.transcript or [])

    try:
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
        from pipecat.services.deepgram import DeepgramSTTService
        from pipecat.services.openai import OpenAILLMService
        from pipecat.transports.network.fastapi_websocket import (
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
        )

        if cfg.tts_provider == MeetSettings.TTSProvider.OPENAI:
            from pipecat.services.openai import OpenAITTSService
            tts = OpenAITTSService(api_key=os.environ["OPENAI_API_KEY"])
        else:
            from pipecat.services.cartesia import CartesiaTTSService
            tts = CartesiaTTSService(
                api_key=os.environ["CARTESIA_API_KEY"],
                voice_id=cfg.cartesia_voice_id,
            )

        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                vad_enabled=True,
                vad_analyzer=SileroVADAnalyzer(),
                vad_audio_passthrough=True,
            ),
        )
        stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"])
        llm = OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"], model=cfg.llm_model)
        context = OpenAILLMContext(
            messages=[{"role": "system", "content": room.system_prompt or cfg.default_system_prompt}]
        )
        context_aggregator = llm.create_context_aggregator(context)

        pipeline = Pipeline([
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ])
        task = PipelineTask(
            pipeline,
            params=PipelineParams(allow_interruptions=cfg.allow_interruptions, enable_metrics=True),
        )

        @transport.event_handler("on_client_disconnected")
        async def on_disconnect(_transport, _client) -> None:
            captured = [
                {"role": m.get("role", ""), "speaker": room.bot_name if m.get("role") == "assistant" else "Participant", "text": str(m.get("content", ""))}
                for m in context.messages
                if m.get("role") in ("user", "assistant") and m.get("content")
            ]
            if captured:
                await sync_to_async(finalize_room)(room, transcript=captured)
            await task.cancel()

        await PipelineRunner().run(task)

    except (ImportError, KeyError) as exc:
        logger.info("Pipecat audio mode unavailable (%s); running JSON text AI fallback.", exc)
        await websocket.send_json({"type": "ai-ready", "mode": "text_fallback", "bot_name": room.bot_name})
        try:
            while True:
                raw = await websocket.receive_text()
                data = json.loads(raw)
                user_text = (data.get("text") or "").strip()
                if not user_text:
                    continue
                speaker = data.get("speaker") or "Participant"
                transcript.append({"role": "user", "speaker": speaker, "text": user_text})
                reply_text = f"Got it, {speaker}. I've noted: \"{user_text[:120]}\"."
                if os.environ.get("OPENAI_API_KEY"):
                    try:
                        import litellm
                        resp = litellm.completion(
                            model=cfg.llm_model,
                            messages=[
                                {"role": "system", "content": room.system_prompt or cfg.default_system_prompt},
                                *[{"role": t["role"], "content": t["text"]} for t in transcript[-10:]],
                            ],
                            max_tokens=160,
                        )
                        reply_text = resp.choices[0].message.content.strip()
                    except Exception:
                        pass
                transcript.append({"role": "assistant", "speaker": room.bot_name, "text": reply_text})

                def _save_t() -> None:
                    r = MeetRoom.objects.get(pk=room.pk)
                    r.transcript = transcript
                    r.save(update_fields=["transcript", "updated_at"])

                await sync_to_async(_save_t)()
                await websocket.send_json({"type": "ai-reply", "speaker": room.bot_name, "text": reply_text})
        except WebSocketDisconnect:
            pass
    except WebSocketDisconnect:
        pass


# Mount Django (HTTP + static/media) on the same port as WebSockets
if getattr(django_settings, "DEBUG", False) and getattr(django_settings, "MEDIA_ROOT", None):
    Path(django_settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(django_settings.MEDIA_ROOT)), name="media")

_django_asgi = get_asgi_application()
if getattr(django_settings, "DEBUG", False):
    _django_asgi = ASGIStaticFilesHandler(_django_asgi)
app.mount("/", _django_asgi)
