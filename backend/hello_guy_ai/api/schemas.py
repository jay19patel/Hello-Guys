from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from ninja import Schema
from pydantic import Field


class ToolInfoOut(Schema):
    name: str
    description: str = ""
    requires_confirmation: bool = False


class AssistantOut(Schema):
    id: str
    name: str
    description: str = ""
    model: str = ""
    tools: list[ToolInfoOut] = []
    suggestions: list[str] = []


class ThreadIn(Schema):
    assistant_id: str = Field(max_length=64)
    name: str = Field(default="", max_length=255)


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


class ThreadRenameIn(Schema):
    name: str = Field(min_length=1, max_length=255)


class ThreadDetailOut(ThreadOut):
    active_run: ActiveRunOut | None = None


class MessageIn(Schema):
    content: str = Field(min_length=1, max_length=8000)
    timezone: str = Field(default="", max_length=64, description="Browser IANA timezone, e.g. Asia/Kolkata")


class MessageOut(Schema):
    id: int
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    name: str = ""
    run_id: UUID | None = None
    created_at: datetime
    # Only on user messages (one per run): lets a reloaded chat show how that run ended.
    run_status: str | None = None
    run_error: str = ""

    @staticmethod
    def resolve_run_status(obj) -> str | None:
        return obj.run.status if obj.role == "user" and obj.run_id else None

    @staticmethod
    def resolve_run_error(obj) -> str:
        if obj.role == "user" and obj.run_id and obj.run.status == "failed":
            return obj.run.public_error
        return ""


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


class ErrorOut(Schema):
    message: str


class NotificationOut(Schema):
    id: int
    title: str
    body: str
    url: str
    is_read: bool
    read_at: datetime | None
    created_at: datetime


class MarkReadIn(Schema):
    ids: list[int] | None = Field(default=None, description="Omit to mark all unread as read")


class CountOut(Schema):
    count: int


class ReminderOut(Schema):
    id: int
    message: str
    due_at: datetime
    recurrence: str
    timezone: str
    agent_id: str
    status: str


class HealthOut(Schema):
    database: bool
    redis: bool


# Public website chat schemas
class PublicAgentOut(Schema):
    name: str
    description: str
    suggestions: list[str]


class PublicThreadOut(Schema):
    thread_id: UUID


class PublicMessageIn(Schema):
    content: str = Field(min_length=1, max_length=2000)
