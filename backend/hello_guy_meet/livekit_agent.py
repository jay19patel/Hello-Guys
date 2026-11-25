"""Production LiveKit Agents worker for Hello Guy Meet.

Joins LiveKit rooms as a server-side AI participant using:
- Silero VAD (server-side voice activity & interruption handling)
- Groq Whisper STT (OpenAI-compatible)
- Mistral AI / Groq / OpenAI LLM
- Edge TTS (free, natural voices streamed as 24kHz PCM)

Run:
    cd backend && ../.venv/bin/python -m hello_guy_meet.livekit_agent dev
"""

import asyncio
import io
import json
import logging
import os
import sys
import uuid
from pathlib import Path

import av
import edge_tts
from asgiref.sync import sync_to_async
from django.utils import timezone
from livekit.agents import (
    APIConnectOptions,
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    tts,
)
from livekit.plugins import openai, silero

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_ENV", "local")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from hello_guy_meet.models import MeetRoom, MeetSettings  # noqa: E402
from hello_guy_meet.services import resolve_llm_model_and_key  # noqa: E402

logger = logging.getLogger(__name__)

EDGE_SAMPLE_RATE = 24000
EDGE_NUM_CHANNELS = 1


class EdgeTTS(tts.TTS):
    """LiveKit TTS adapter for free Microsoft Edge TTS."""

    def __init__(self, voice: str = "en-IN-NeerjaNeural", rate: str = "+18%") -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=EDGE_SAMPLE_RATE,
            num_channels=EDGE_NUM_CHANNELS,
        )
        self.voice = voice
        self.rate = rate

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions | None = None,
    ) -> "EdgeChunkedStream":
        return EdgeChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options or APIConnectOptions(),
        )


class EdgeChunkedStream(tts.ChunkedStream):
    """Decodes Edge TTS MP3 bytes to 24kHz PCM frames for LiveKit."""

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=str(uuid.uuid4())[:8],
            sample_rate=EDGE_SAMPLE_RATE,
            num_channels=EDGE_NUM_CHANNELS,
            mime_type="audio/pcm",
        )
        comm = edge_tts.Communicate(self.input_text, self._tts.voice, rate=self._tts.rate)
        mp3_bytes = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                mp3_bytes.extend(chunk["data"])

        if not mp3_bytes:
            return

        container = av.open(io.BytesIO(bytes(mp3_bytes)), format="mp3")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=EDGE_SAMPLE_RATE)
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                output_emitter.push(resampled.to_ndarray().tobytes())
        for resampled in resampler.resample(None):
            output_emitter.push(resampled.to_ndarray().tobytes())
        output_emitter.flush()


def _build_llm(cfg: MeetSettings) -> openai.LLM:
    """Create a LiveKit OpenAI-compatible LLM for Mistral, Groq, or OpenAI."""
    model, api_key = resolve_llm_model_and_key(cfg)
    if model.startswith("mistral/"):
        return openai.LLM(
            model=model.removeprefix("mistral/"),
            base_url="https://api.mistral.ai/v1",
            api_key=api_key,
        )
    if model.startswith("groq/"):
        return openai.LLM(
            model=model.removeprefix("groq/"),
            base_url="https://api.groq.com/openai/v1",
            api_key=api_key,
        )
    return openai.LLM(model=model, api_key=api_key)


async def entrypoint(ctx: JobContext) -> None:
    """Join a LiveKit room when enabled and run the AI Voice Assistant."""
    room_code = ctx.room.name
    room = await sync_to_async(MeetRoom.objects.filter(room_code=room_code).first)()
    if room is None or room.status == MeetRoom.Status.ENDED or not room.ai_enabled:
        return

    if room.scheduled_start and timezone.now() < room.scheduled_start:
        wait_secs = min((room.scheduled_start - timezone.now()).total_seconds(), 3600)
        if wait_secs > 0:
            await asyncio.sleep(wait_secs)
        room = await sync_to_async(MeetRoom.objects.filter(room_code=room_code).first)()
        if room is None or room.status == MeetRoom.Status.ENDED or not room.ai_enabled:
            return

    cfg = await sync_to_async(MeetSettings.get_solo)()
    groq_key = cfg.get_api_key("GROQ_API_KEY")
    if not groq_key:
        logger.warning("Skipping LiveKit AI agent for %s: GROQ_API_KEY not configured.", room_code)
        return

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    stt = openai.STT(
        model="whisper-large-v3-turbo",
        base_url="https://api.groq.com/openai/v1",
        api_key=groq_key,
        detect_language=True,
    )
    llm = _build_llm(cfg)
    voice_tts = EdgeTTS(voice=cfg.edge_voice or "en-IN-NeerjaNeural", rate="+18%")

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=stt,
        llm=llm,
        tts=voice_tts,
        allow_interruptions=cfg.allow_interruptions,
    )

    @session.on("conversation_item_added")
    def on_item(ev) -> None:
        item = getattr(ev, "item", None)
        if not item:
            return
        role = getattr(item, "role", "user")
        text = getattr(item, "text_content", "") or ""
        if not text.strip():
            return
        speaker = room.bot_name if role == "assistant" else "Participant"

        def _append() -> None:
            r = MeetRoom.objects.filter(pk=room.pk).first()
            if r is None:
                return
            t = list(r.transcript or [])
            t.append({"role": role, "speaker": speaker, "text": text.strip()})
            r.transcript = t
            r.save(update_fields=["transcript", "updated_at"])

        asyncio.create_task(sync_to_async(_append)())
        payload = json.dumps({"type": "transcript", "role": role, "speaker": speaker, "text": text.strip()}).encode("utf-8")
        asyncio.create_task(ctx.room.local_participant.publish_data(payload, reliable=True))

    agent = Agent(
        instructions=(room.system_prompt or cfg.default_system_prompt)
        + " Keep responses brief (1-2 sentences) for live voice calls."
    )
    await session.start(agent=agent, room=ctx.room)


if __name__ == "__main__":
    cfg = MeetSettings.get_solo()
    lk_url, lk_key, lk_secret = cfg.get_livekit_credentials()
    os.environ.setdefault("LIVEKIT_URL", lk_url)
    os.environ.setdefault("LIVEKIT_API_KEY", lk_key)
    os.environ.setdefault("LIVEKIT_API_SECRET", lk_secret)
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
