"""Decoupled Async Pipecat WebSocket Microservice (/ws/voice-bot/{session_id}).

Run alongside Django WSGI/ASGI:
    uvicorn voice_agent.pipecat_worker:app --host 0.0.0.0 --port 8001
"""

import logging
import os
import sys
from pathlib import Path

from asgiref.sync import sync_to_async
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

# Ensure Django ORM is initialized when launched standalone
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.utils import timezone  # noqa: E402
from voice_agent.models import VoiceBotSession  # noqa: E402
from voice_agent.services import finalize_session_to_crm  # noqa: E402

logger = logging.getLogger(__name__)
app = FastAPI(title="Hello Guys Pipecat Voice Worker")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws/voice-bot/{session_id}")
async def voice_bot_ws(websocket: WebSocket, session_id: str) -> None:
    """Real-time Pipecat audio WebSocket handler using SileroVADAnalyzer + STT -> LLM -> TTS."""
    await websocket.accept()
    session = await sync_to_async(VoiceBotSession.objects.filter(pk=session_id).first)()
    if session is None:
        await websocket.close(code=4004, reason="Unknown session_id")
        return

    def _mark_active() -> None:
        session.status = VoiceBotSession.Status.IN_MEETING
        session.started_at = session.started_at or timezone.now()
        session.append_log("Pipecat WebSocket connected.")
        session.save(update_fields=["status", "started_at", "connection_logs", "updated_at"])

    await sync_to_async(_mark_active)()
    transcript: list[dict[str, str]] = list(session.transcript or [])

    try:
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
        from pipecat.services.cartesia import CartesiaTTSService
        from pipecat.services.deepgram import DeepgramSTTService
        from pipecat.services.openai import OpenAILLMService
        from pipecat.transports.network.fastapi_websocket import (
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
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
        llm = OpenAILLMService(
            api_key=os.environ["OPENAI_API_KEY"],
            model=os.environ.get("PIPECAT_LLM_MODEL", "gpt-4o-mini"),
        )
        tts = CartesiaTTSService(
            api_key=os.environ["CARTESIA_API_KEY"],
            voice_id=os.environ.get("CARTESIA_VOICE_ID", "79a125e8-cd45-4c13-8a67-188112f4dd22"),
        )

        context = OpenAILLMContext(
            messages=[{"role": "system", "content": session.system_prompt or "You are a helpful AI assistant."}]
        )
        context_aggregator = llm.create_context_aggregator(context)

        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                context_aggregator.user(),
                llm,
                tts,
                transport.output(),
                context_aggregator.assistant(),
            ]
        )
        task = PipelineTask(
            pipeline,
            params=PipelineParams(allow_interruptions=True, enable_metrics=True),
        )

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(_transport, _client) -> None:
            captured = [
                {"role": m.get("role", ""), "text": str(m.get("content", ""))}
                for m in context.messages
                if m.get("role") in ("user", "assistant") and m.get("content")
            ]
            await sync_to_async(finalize_session_to_crm)(session, transcript=captured or transcript)
            await task.cancel()

        runner = PipelineRunner()
        await runner.run(task)

    except ImportError as exc:
        logger.warning("Pipecat dependencies not installed: %s", exc)
        try:
            while True:
                msg = await websocket.receive_text()
                transcript.append({"role": "user", "text": msg})
        except WebSocketDisconnect:
            await sync_to_async(finalize_session_to_crm)(session, transcript=transcript)
    except WebSocketDisconnect:
        await sync_to_async(finalize_session_to_crm)(session, transcript=transcript)
    except Exception as exc:
        logger.exception("Pipecat WebSocket error on session %s", session_id)

        def _mark_failed() -> None:
            session.status = VoiceBotSession.Status.FAILED
            session.error_message = str(exc)[:500]
            session.append_log(f"WebSocket error: {exc}")
            session.save(update_fields=["status", "error_message", "connection_logs", "updated_at"])

        await sync_to_async(_mark_failed)()
