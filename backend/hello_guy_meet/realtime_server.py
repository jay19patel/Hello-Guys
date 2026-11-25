"""Production-grade WebRTC Signaling & Pipecat AI Voice Server for Hello Guy Meet.

Run alongside Django:
    cd backend && ../.venv/bin/uvicorn hello_guy_meet.realtime_server:app --host 0.0.0.0 --port 8001
"""

import asyncio
import base64
import io
import json
import logging
import time
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
from hello_guy_meet.services import finalize_room, resolve_llm_model_and_key  # noqa: E402

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

    def get_participants(self, room_code: str) -> list[dict[str, Any]]:
        peers = self.rooms.get(room_code, {})
        return [{"name": info["name"], "is_host": info["is_host"]} for info in peers.values()]


hub = RoomHub()
_room_ai_locks: dict[str, asyncio.Lock] = {}
_room_last_ai_ts: dict[str, float] = {}
_WHISPER_NOISE = {"thank you", "thank you.", "thanks", "thanks.", "you", "bye", "bye.", "you.", ""}


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
    if room.status == MeetRoom.Status.ENDED:
        await websocket.close(code=4003, reason="Meeting ended")
        return
    if room.status == MeetRoom.Status.ENDED:
        await websocket.close(code=4003, reason="Meeting ended")
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


async def _groq_transcribe(cfg: MeetSettings, audio_b64: str, mime: str) -> str:
    """Transcribe base64-encoded browser audio using Groq Whisper."""
    api_key = cfg.get_api_key("GROQ_API_KEY")
    if not api_key or not audio_b64:
        return ""
    from groq import AsyncGroq

    ext = "webm" if "webm" in (mime or "") else ("mp4" if "mp4" in (mime or "") else "wav")
    raw = base64.b64decode(audio_b64)
    buf = io.BytesIO(raw)
    buf.name = f"turn.{ext}"
    client = AsyncGroq(api_key=api_key)
    res = await client.audio.transcriptions.create(
        file=buf,
        model="whisper-large-v3-turbo",
        response_format="text",
    )
    return str(res).strip()


async def _groq_chat_reply(cfg: MeetSettings, system_prompt: str, transcript: list[dict[str, Any]]) -> str:
    """Generate a concise live meeting reply via Mistral, Groq, or OpenAI."""
    model, api_key = resolve_llm_model_and_key(cfg)
    if api_key:
        try:
            import litellm
            resp = await litellm.acompletion(
                model=model,
                api_key=api_key,
                messages=[
                    {"role": "system", "content": system_prompt + " Keep your reply very brief (1-2 short sentences) for a live voice call."},
                    *[{"role": t["role"], "content": t["text"]} for t in transcript[-8:] if t.get("text")],
                ],
                max_tokens=90,
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            logger.exception("LLM reply failed")
    last = transcript[-1]["text"][:120] if transcript else ""
    return f"Got it. I've noted: \"{last}\"."


async def _edge_tts_b64(text: str, voice: str) -> str:
    """Synthesize speech to base64 MP3 using free Microsoft Edge TTS."""
    if not text:
        return ""
    try:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice or "en-IN-NeerjaNeural", rate="+18%")
        chunks = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.extend(chunk["data"])
        return base64.b64encode(bytes(chunks)).decode("ascii") if chunks else ""
    except Exception:
        logger.exception("Edge TTS synthesis failed")
        return ""


@app.websocket("/ws/meet/ai/{room_code}")
async def meet_ai_ws(websocket: WebSocket, room_code: str) -> None:
    """AI Voice Assistant WebSocket: Groq Whisper + Groq LLM + Edge TTS (or Pipecat when configured)."""
    await websocket.accept()
    room = await sync_to_async(MeetRoom.objects.filter(room_code=room_code).first)()
    if room is None:
        await websocket.close(code=4004, reason="Room not found")
        return
    if room.status == MeetRoom.Status.ENDED:
        await websocket.close(code=4003, reason="Meeting ended")
        return
    if room.status == MeetRoom.Status.ENDED:
        await websocket.close(code=4003, reason="Meeting ended")
        return

    cfg = await sync_to_async(MeetSettings.get_solo)()
    transcript: list[dict[str, str]] = list(room.transcript or [])

    try:
        if True:
            raise ImportError("Using Groq + Edge TTS mode")
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

        openai_key = cfg.get_api_key("OPENAI_API_KEY")
        deepgram_key = cfg.get_api_key("DEEPGRAM_API_KEY")
        cartesia_key = cfg.get_api_key("CARTESIA_API_KEY")
        if not openai_key or not deepgram_key:
            raise KeyError("Missing Pipecat API key")

        if cfg.tts_provider == MeetSettings.TTSProvider.OPENAI:
            from pipecat.services.openai import OpenAITTSService
            tts = OpenAITTSService(api_key=openai_key)
        else:
            if not cartesia_key:
                raise KeyError("Missing CARTESIA_API_KEY")
            from pipecat.services.cartesia import CartesiaTTSService
            tts = CartesiaTTSService(
                api_key=cartesia_key,
                voice_id=cfg.cartesia_voice_id or os.environ.get("CARTESIA_VOICE_ID", "79a125e8-cd45-4c13-8a67-188112f4dd22"),
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
        stt = DeepgramSTTService(api_key=deepgram_key)
        llm = OpenAILLMService(api_key=openai_key, model=cfg.llm_model)
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
        logger.info("Running Groq + Edge TTS AI mode (%s).", exc)
        await websocket.send_json({"type": "ai-ready", "mode": "groq_edge_tts", "bot_name": room.bot_name})
        try:
            while True:
                raw = await websocket.receive_text()
                data = json.loads(raw)
                speaker = data.get("speaker") or "Participant"
                user_text = (data.get("text") or "").strip()

                if data.get("type") == "audio-turn" and data.get("audio_b64"):
                    user_text = await _groq_transcribe(cfg, data["audio_b64"], data.get("mime", "audio/webm"))
                    if user_text.strip().lower() in _WHISPER_NOISE or len(user_text.strip()) < 3:
                        continue
                    if user_text:
                        t_payload = {"type": "transcript", "role": "user", "speaker": speaker, "text": user_text}
                        if hub.rooms.get(room_code):
                            await hub.broadcast(room_code, t_payload)
                        else:
                            await websocket.send_json({"type": "user-transcript", "speaker": speaker, "text": user_text})

                if not user_text:
                    continue

                def _check_ai_active() -> bool:
                    r = MeetRoom.objects.filter(pk=room.pk).only("ai_enabled", "scheduled_start").first()
                    if not r or not r.ai_enabled:
                        return False
                    return r.scheduled_start is None or timezone.now() >= r.scheduled_start

                is_ai_on = await sync_to_async(_check_ai_active)()
                if data.get("transcribe_only") or not is_ai_on:
                    transcript.append({"role": "user", "speaker": speaker, "text": user_text})

                    def _save_only() -> None:
                        r = MeetRoom.objects.get(pk=room.pk)
                        r.transcript = transcript
                        r.save(update_fields=["transcript", "updated_at"])

                    await sync_to_async(_save_only)()
                    continue

                lock = _room_ai_locks.setdefault(room_code, asyncio.Lock())
                async with lock:
                    now_ts = time.monotonic()
                    if data.get("type") == "audio-turn" and (now_ts - _room_last_ai_ts.get(room_code, 0.0)) < 2.5:
                        continue
                    _room_last_ai_ts[room_code] = now_ts

                    transcript.append({"role": "user", "speaker": speaker, "text": user_text})
                    reply_text = await _groq_chat_reply(cfg, room.system_prompt or cfg.default_system_prompt, transcript)
                    transcript.append({"role": "assistant", "speaker": room.bot_name, "text": reply_text})

                    audio_b64 = await _edge_tts_b64(reply_text, cfg.edge_voice)

                    def _save_t() -> None:
                        r = MeetRoom.objects.get(pk=room.pk)
                        r.transcript = transcript
                        r.save(update_fields=["transcript", "updated_at"])

                    await sync_to_async(_save_t)()
                    _room_last_ai_ts[room_code] = time.monotonic()
                    reply_payload = {
                        "type": "ai-reply",
                        "speaker": room.bot_name,
                        "text": reply_text,
                        "audio_b64": audio_b64,
                    }
                    if hub.rooms.get(room_code):
                        await hub.broadcast(room_code, reply_payload)
                    else:
                        await websocket.send_json(reply_payload)
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
