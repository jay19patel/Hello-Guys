from django.urls import path

from hello_guy_ai.api import api
from hello_guy_ai.console_views import ai_knowledge, ai_memory, ai_models, ai_thread_detail, ai_threads, ai_triggers
from hello_guy_ai.views import ai_analytics, chat_page, run_stream, thread_stream

urlpatterns = [
    path("api/ai/", api.urls),
    path("api/ai/runs/<uuid:run_id>/stream", run_stream, name="hello_guy_ai_run_stream"),
    path("api/ai/public/threads/<uuid:thread_id>/events", thread_stream, name="hello_guy_ai_thread_stream"),
    path("ai/analytics/", ai_analytics, name="hello_guy_ai_analytics"),
    path("ai/models/", ai_models, name="hello_guy_ai_models"),
    path("ai/knowledge/", ai_knowledge, name="hello_guy_ai_knowledge"),
    path("ai/memory/", ai_memory, name="hello_guy_ai_memory"),
    path("ai/triggers/", ai_triggers, name="hello_guy_ai_triggers"),
    path("ai/threads/", ai_threads, name="hello_guy_ai_threads"),
    path("ai/threads/<uuid:thread_id>/", ai_thread_detail, name="hello_guy_ai_thread_detail"),
    path("chat/", chat_page, name="hello_guy_ai_chat"),
]
