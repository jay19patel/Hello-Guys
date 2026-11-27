from django.urls import path
from core.views import (
    FollowUpActionView,
    FollowUpChannelCreateView,
    FollowUpCreateView,
    FollowUpListView,
    HubPortalView,
    NotificationListView,
    NotificationMarkReadView,
    NotificationUnreadCountView,
    QuickSearchAPIView,
)

app_name = 'core'

urlpatterns = [
    path('', HubPortalView.as_view(), name='portal'),
    path('search/', QuickSearchAPIView.as_view(), name='search'),
    path('notifications/', NotificationListView.as_view(), name='notifications'),
    path('notifications/unread-count/', NotificationUnreadCountView.as_view(), name='notifications-unread-count'),
    path('notifications/read/', NotificationMarkReadView.as_view(), name='notifications-read'),
    path('follow-ups/', FollowUpListView.as_view(), name='follow-ups'),
    path('follow-ups/create/', FollowUpCreateView.as_view(), name='follow-up-create'),
    path('follow-ups/channels/create/', FollowUpChannelCreateView.as_view(), name='follow-up-channel-create'),
    path('follow-ups/<int:pk>/action/', FollowUpActionView.as_view(), name='follow-up-action'),
    # Legacy URL aliases
    path('reminders/', FollowUpListView.as_view(), name='reminders'),
    path('reminders/create/', FollowUpCreateView.as_view(), name='reminder-create'),
    path('reminders/channels/create/', FollowUpChannelCreateView.as_view(), name='reminder-channel-create'),
    path('reminders/<int:pk>/action/', FollowUpActionView.as_view(), name='reminder-action'),
]

