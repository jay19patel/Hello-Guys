from django.urls import path

from voice_agent import views

app_name = "voice_agent"

urlpatterns = [
    path("start", views.VoiceBotStartAPIView.as_view(), name="start"),
    path("stop", views.VoiceBotStopAPIView.as_view(), name="stop"),
    path("status/<uuid:session_id>", views.VoiceBotStatusAPIView.as_view(), name="status"),
]
