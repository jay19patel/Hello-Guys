from django.urls import path

from hello_guy_ai.api import api
from hello_guy_ai.views import chat_page, run_stream, thread_stream

urlpatterns = [
    path("api/ai/", api.urls),
    path("api/ai/runs/<uuid:run_id>/stream", run_stream, name="hello_guy_ai_run_stream"),
    path("api/ai/public/threads/<uuid:thread_id>/events", thread_stream, name="hello_guy_ai_thread_stream"),
    path("chat/", chat_page, name="hello_guy_ai_chat"),
]
