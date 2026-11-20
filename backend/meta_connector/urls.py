from django.urls import path
from meta_connector.views import (
    meta_webhook_view,
    DashboardIndexView,
    DashboardOverviewAPIView,
    ContactListAPIView,
    MessageListAPIView,
    SendMessageAPIView,
)

app_name = 'meta_connector'

urlpatterns = [
    # Meta Webhook (GET for verification, POST for receiving events)
    path('webhook/', meta_webhook_view, name='meta-webhook'),

    # Unified REST APIs
    path('contacts/', ContactListAPIView.as_view(), name='contact-list'),
    path('messages/', MessageListAPIView.as_view(), name='message-list'),
    path('send/', SendMessageAPIView.as_view(), name='send-message'),

    # Dashboard endpoints inside meta_connector
    path('dashboard/', DashboardIndexView.as_view(), name='dashboard-view'),
    path('dashboard/api/overview/', DashboardOverviewAPIView.as_view(), name='dashboard-overview'),
]
