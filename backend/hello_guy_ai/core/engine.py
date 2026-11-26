"""Agent loop executed inside the Dramatiq worker (Flow: flow.lib.agent + Flow Run lifecycle).

  start : build prompt -> stream LLM -> tools? run them, loop : answer -> succeeded
  tool needs approval (and run is not auto_approve) -> save pending question -> PAUSED
  resume: approve = run tool | deny = record denial, end turn | other text = feedback to LLM, loop
  stop  : Redis flag checked while streaming -> STOPPED (partial text kept)

Every status change is a compare-and-set on the previous status, so a late worker,
a double resume or a stop racing a finish can never overwrite each other.
"""

import json
import logging
from contextlib import AbstractContextManager
from decimal import Decimal
from typing import Any

import litellm
from django.db.models import F
from django.db.models.functions import Coalesce
from django.utils import timezone

from hello_guy_ai.core import events
from hello_guy_ai.core.prompt import PromptBuilder
from hello_guy_ai.core.runtime import AgentSpec, ToolContext, ToolError, tool_context
from hello_guy_ai.models import Message, Run
from hello_guy_ai.services.agents import resolve_agent
from hello_guy_ai.services.rag import RagService

logger = logging.getLogger(__name__)
litellm.suppress_debug_info = True  # no "Give Feedback / Get Help" banner on every provider error

STOP_CHECK_EVERY = 8  # chunks between Redis stop-flag checks
DENIED = json.dumps({"status": "denied", "message": "The user denied this action. Do not retry it."})


OUT_OF_STEPS = "I ran out of steps before I could finish. Please try again, or ask for a smaller part of the task."


class RunStopped(Exception):
    pass


class AgentRunner:
    def __init__(self, run: Run, publisher: events.RunEventPublisher | None = None, rag: RagService | None = None):
        self.run = run
        self.thread = run.thread
        self.user = self.thread.created_by
        self.events = publisher or events.RunEventPublisher(run.id)
        self._rag = rag
        self.spec: AgentSpec | None = None
        self._partial = ""

    # ---- entry points -------------------------------------------------------

    def execute(self) -> None:
        self._guarded(lambda: self._loop(self._prompt()))

    def resume(self) -> None:
        self._guarded(self._resume)

    def _guarded(self, body) -> None:
        if not self._claim():
            logger.warning("Run %s not claimable (status=%s)", self.run.pk, self.run.status)
            return
        self.events.publish("status", {"status": Run.Status.RUNNING})
        try:
            if events.stop_requested(self.run.pk):
                raise RunStopped
            # Re-resolve every segment: permissions or agent config may have changed since queueing.
            self.spec = resolve_agent(self.thread.assistant_id, self.user)
            Run.objects.filter(pk=self.run.pk).update(model=self.spec.model, config_snapshot=self.spec.snapshot())
            body()
        except RunStopped:
            self._stopped()
        except BaseException as exc:  # BaseException: Dramatiq time limits raise Interrupt
            logger.exception("Run %s failed", self.run.pk)
            self._fail(exc)
            if not isinstance(exc, Exception):
                raise

    # ---- loop -----------------------------------------------------------------

    def _loop(self, messages: list[dict]) -> None:
        spec = self.spec
        schemas = [t.schema() for t in spec.tools]
        while True:
            iteration = Run.objects.filter(pk=self.run.pk).values_list("iterations", flat=True).first() or 0
            # Last round gets no tools, forcing an answer instead of an endless tool loop.
            final = iteration >= spec.max_iterations
            reply = self._stream(messages, [] if final else schemas)
            Run.objects.filter(pk=self.run.pk).update(iterations=F("iterations") + 1)
            saved = self._save_assistant(reply, final=final)
            messages.append(saved.to_llm())
            if not saved.tool_calls:
                return self._finish(saved)

            pending: list[dict] = []
            with self._ctx():
                for call in saved.tool_calls:
                    question = self._handle_call(call, messages)
                    if question:
                        pending.append(question)
            if pending:
                return self._pause(pending)

    def _handle_call(self, call: dict, messages: list[dict]) -> dict | None:
        """Run one tool call, or return a pending question if it needs approval."""
        name = call["function"]["name"]
        raw = call["function"].get("arguments") or "{}"
        tool = self.spec.tool_map.get(name)
        if tool is None:
            content = f"Error: unknown tool '{name}'."
        else:
            try:
                args = tool.parse_args(raw)
            except ToolError as exc:
                content = f"Error: {exc}"
            else:
                if tool.needs_confirmation(args) and not self.run.auto_approve:
                    self.events.publish("tool_start", {"id": call["id"], "name": name, "pending": True})
                    return {"key": call["id"], "tool": name, "prompt": tool.describe_call(args), "arguments": raw}
                content = self._run_tool(call["id"], tool, args)
        messages.append(self._save_tool(call["id"], name, content).to_llm())
        return None

    def _run_tool(self, call_id: str, tool, args: dict) -> str:
        self.events.publish("tool_start", {"id": call_id, "name": tool.name, "args": json.loads(json.dumps(args, default=str))})  # step label in UI
        result = tool.call(args)
        self.events.publish("tool_end", {"id": call_id, "name": tool.name, "error": result.startswith("Error:")})
        return result

    # ---- resume -----------------------------------------------------------------

    def _resume(self) -> None:
        questions = self.run.questions or []
        answers = self.run.answers or {}
        denied = False
        with self._ctx():
            for q in questions:
                answer = str(answers.get(q["key"], "deny")).strip()
                if answer.lower() == "approve":
                    content = self._approved(q)
                elif answer.lower() == "deny":
                    content, denied = DENIED, True
                    self.events.publish("tool_end", {"id": q["key"], "name": q["tool"], "denied": True})
                else:
                    content = json.dumps(
                        {
                            "status": "redirect",
                            "message": "Not executed.",
                            "user_feedback": answer,
                            "instruction": "The user wants changes. Adjust your approach and try again.",
                        }
                    )
                    self.events.publish("tool_end", {"id": q["key"], "name": q["tool"], "redirected": True})
                self._save_tool(q["key"], q["tool"], content)
        Run.objects.filter(pk=self.run.pk).update(questions=None, answers=None)
        if denied:  # the user rejected an action: end the turn without another model call
            return self._finish(None)
        self._loop(self._prompt())

    def _approved(self, q: dict) -> str:
        tool = self.spec.tool_map.get(q["tool"])
        if tool is None:
            return f"Error: tool '{q['tool']}' is no longer available to this agent."
        try:
            args = tool.parse_args(q["arguments"])
        except ToolError as exc:
            return f"Error: {exc}"
        return self._run_tool(q["key"], tool, args)

    # ---- LLM -------------------------------------------------------------------

    def _stream(self, messages: list[dict], tools: list[dict]) -> Any:
        params: dict[str, Any] = {
            "model": self.spec.model,
            "messages": messages,
            "stream": True,
            **self.spec.llm_kwargs,
        }
        if tools:
            params["tools"] = tools
        chunks = []
        self._partial = ""
        for i, chunk in enumerate(litellm.completion(**params)):
            chunks.append(chunk)
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                self._partial += delta
                self.events.publish("token", {"text": delta})
            if i % STOP_CHECK_EVERY == 0 and events.stop_requested(self.run.pk):
                raise RunStopped
        response = litellm.stream_chunk_builder(chunks, messages=messages)
        self._add_usage(getattr(response, "usage", None))
        self._partial = ""
        return response.choices[0].message

    def _prompt(self) -> list[dict]:
        builder = PromptBuilder(
            self.thread,
            self.spec,
            self.user,
            rag=self._rag,
            on_sources=lambda s: self.events.publish("sources", {"sources": s}),
            on_usage=self._add_usage,
        )
        return builder.build()

    # ---- persistence & status ----------------------------------------------------

    def _save_assistant(self, reply: Any, final: bool = False) -> Message:
        """final: the tool-less last round. A model that still asks for tools is ignored: answer with what it said."""
        tool_calls = [] if final else [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
            }
            for tc in (reply.tool_calls or [])
        ]
        content = reply.content or (OUT_OF_STEPS if final else "")
        return Message.objects.create(
            thread=self.thread,
            run=self.run,
            role=Message.Role.ASSISTANT,
            content=content,
            tool_calls=tool_calls or None,
        )

    def _save_tool(self, call_id: str, name: str, content: str) -> Message:
        return Message.objects.create(
            thread=self.thread, run=self.run, role=Message.Role.TOOL, content=content, tool_call_id=call_id, name=name
        )

    def _claim(self) -> bool:
        claimed = Run.objects.filter(pk=self.run.pk, status=Run.Status.QUEUED).update(
            status=Run.Status.RUNNING,
            started_at=Coalesce(F("started_at"), timezone.now()),
            last_active_at=timezone.now(),
        )
        self.run.refresh_from_db()
        return bool(claimed)

    def _finish(self, message: Message | None) -> None:
        if self._transition(Run.Status.SUCCEEDED, finished_at=timezone.now()):
            payload = {"message_id": message.pk, "content": message.content} if message else {"denied": True}
            self.events.publish("done", payload)

    def _pause(self, questions: list[dict]) -> None:
        if self._transition(Run.Status.PAUSED, questions=questions):
            self.events.publish("paused", {"questions": public_questions(questions)})

    def _stopped(self) -> None:
        if self._partial:
            Message.objects.create(thread=self.thread, run=self.run, role=Message.Role.ASSISTANT, content=self._partial)
        events.get_redis().delete(events.stop_key(self.run.pk))
        if self._transition(Run.Status.STOPPED, error="Stopped by user", finished_at=timezone.now()):
            self.events.publish("stopped", {})

    def _fail(self, exc: BaseException) -> None:
        error = f"{type(exc).__name__}: {exc}"[:2000]
        if self._transition(Run.Status.FAILED, error=error, finished_at=timezone.now()):
            self.events.publish("error", {"message": Run(error=error).public_error})

    def _transition(self, status: str, **fields: Any) -> bool:
        done = Run.objects.filter(pk=self.run.pk, status=Run.Status.RUNNING).update(status=status, **fields)
        self.run.status = status if done else self.run.status
        return bool(done)

    def _add_usage(self, usage: Any) -> None:
        if not usage:
            return
        prompt, completion = usage.prompt_tokens or 0, usage.completion_tokens or 0
        try:
            cost = sum(litellm.cost_per_token(model=self.spec.model, prompt_tokens=prompt, completion_tokens=completion))
        except Exception:  # local / unpriced model
            logger.debug("No price for model %s", self.spec.model, exc_info=True)
            cost = 0
        Run.objects.filter(pk=self.run.pk).update(
            prompt_tokens=F("prompt_tokens") + prompt,
            completion_tokens=F("completion_tokens") + completion,
            cost_usd=F("cost_usd") + Decimal(str(round(cost, 6))),
        )

    def _ctx(self) -> AbstractContextManager:
        return tool_context(
            ToolContext(
                user=self.user,
                agent_key=self.spec.key,
                run_id=self.run.pk,
                thread_id=self.thread.pk,
                timezone=self.thread.timezone,
            )
        )


def public_questions(questions: list[dict]) -> list[dict]:
    """What the browser sees: never the raw arguments blob, only the human summary."""
    return [
        {"key": q["key"], "tool": q["tool"], "prompt": q["prompt"], "options": ["approve", "deny"]} for q in questions
    ]
