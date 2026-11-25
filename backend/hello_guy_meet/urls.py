from django.urls import path

from hello_guy_meet import views

app_name = "meet"

urlpatterns = [
    path("", views.MeetDashboardView.as_view(), name="dashboard"),
    path("calendar/", views.MeetCalendarView.as_view(), name="calendar"),
    path("mom/", views.MeetMinutesListView.as_view(), name="mom-list"),
    path("settings/", views.MeetSettingsView.as_view(), name="settings"),
    path("settings/test/", views.MeetSettingsTestView.as_view(), name="settings-test"),
    path("room/<str:room_code>/", views.MeetRoomView.as_view(), name="room"),
    path("api/rooms/<str:room_code>/toggle-ai/", views.MeetToggleAIView.as_view(), name="room-toggle-ai"),
    path("api/rooms/<str:room_code>/end/", views.MeetEndAPIView.as_view(), name="room-end"),
]
