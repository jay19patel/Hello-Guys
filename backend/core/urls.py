from django.urls import path
from core.views import (
    FollowUpChannelCreateView,
    GlobalFollowUpActionView,
    GlobalFollowUpCreateView,
    GlobalFollowUpListView,
    HubPortalView,
    NotificationListView,
    NotificationMarkReadView,
    NotificationUnreadCountView,
)

app_name = 'core'

urlpatterns = [
    path('', HubPortalView.as_view(), name='portal'),
    path('notifications/', NotificationListView.as_view(), name='notifications'),
    path('notifications/unread-count/', NotificationUnreadCountView.as_view(), name='notifications-unread-count'),
    path('notifications/read/', NotificationMarkReadView.as_view(), name='notifications-read'),
    path('follow-ups/', GlobalFollowUpListView.as_view(), name='follow-ups'),
    path('follow-ups/create/', GlobalFollowUpCreateView.as_view(), name='follow-up-create'),
    path('follow-ups/channels/create/', FollowUpChannelCreateView.as_view(), name='follow-up-channel-create'),
    path('follow-ups/<int:pk>/action/', GlobalFollowUpActionView.as_view(), name='follow-up-action'),
]

