from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from ninja import Schema
from pydantic import Field


class AssistantOut(Schema):
    id: str
    name: str
    description: str = ""
    kind: str


class ThreadIn(Schema):
    assistant_id: str = Field(max_length=64)
    name: str = Field(default="", max_length=255)


class ThreadPatch(Schema):
    name: str = Field(max_length=255)


class QuestionOut(Schema):
    key: str
    tool: str
    prompt: str
    options: list[str]


class ActiveRunOut(Schema):
    id: UUID
    status: str
    stream_url: str
    questions: list[QuestionOut] = []


class ThreadOut(Schema):
    id: UUID
    name: str
    assistant_id: str
    created_at: datetime
    updated_at: datetime


class ThreadDetailOut(ThreadOut):
    active_run: ActiveRunOut | None = None


class MessageIn(Schema):
    content: str = Field(min_length=1, max_length=8000)


class MessageOut(Schema):
    id: int
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    name: str = ""
    run_id: UUID | None = None
    created_at: datetime


class RunAccepted(Schema):
    run_id: UUID
    message_id: int | None = None
    stream_url: str


class ResumeIn(Schema):
    answers: dict[str, str]


class FeedbackIn(Schema):
    rating: Literal["up", "down", "none"]
    comment: str = Field(default="", max_length=500)


class FeedbackOut(Schema):
    rating: str | None
    memory_id: int | None = None


class StopOut(Schema):
    status: str


class RunOut(Schema):
    id: UUID
    status: str
    model: str
    error: str
    iterations: int
    prompt_tokens: int
    completion_tokens: int
    feedback_rating: str
    created_at: datetime
    finished_at: datetime | None = None


class KnowledgeBaseOut(Schema):
    slug: str
    name: str
    description: str
    enabled: bool


class DocumentOut(Schema):
    id: int
    knowledge_base: str
    title: str
    status: str
    chunk_count: int
    error: str
    created_at: datetime

    @staticmethod
    def resolve_knowledge_base(obj) -> str:
        return obj.knowledge_base.slug if obj.knowledge_base_id else ""


class ErrorOut(Schema):
    message: str
