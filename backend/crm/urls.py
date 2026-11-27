from django.urls import path
from django.views.generic import RedirectView

from core import views as core_views
from crm import views

app_name = "crm"

urlpatterns = [
    path("", views.OverviewView.as_view(), name="overview"),
    path("opportunities/", views.OpportunityListView.as_view(), name="opportunities"),
    path("opportunities/new/", views.OpportunityCreateView.as_view(), name="opportunity-create"),
    path("opportunities/<int:pk>/", views.OpportunityDetailView.as_view(), name="opportunity"),
    path("opportunities/<int:pk>/stage/", views.OpportunityStageView.as_view(), name="opportunity-stage"),
    path("customers/", views.CustomerListView.as_view(), name="customers"),
    path("customers/new/", views.CustomerCreateView.as_view(), name="customer-create"),
    path("customers/<int:pk>/", views.CustomerDetailView.as_view(), name="customer"),
    path("emails/", views.EmailListView.as_view(), name="emails"),
    path("follow-ups/", RedirectView.as_view(pattern_name="core:follow-ups", query_string=True), name="follow-ups"),
    path("follow-ups/new/", core_views.FollowUpCreateView.as_view(), name="follow-up-create"),
    path("follow-ups/<int:pk>/action/", core_views.FollowUpActionView.as_view(), name="follow-up-action"),
    path("meetings/", views.MeetingListView.as_view(), name="meetings"),
    path("meetings/new/", views.MeetingCreateView.as_view(), name="meeting-create"),
    path("meetings/sync/", views.MeetingSyncView.as_view(), name="meetings-sync"),
    path("meetings/<int:pk>/", views.MeetingDetailView.as_view(), name="meeting"),
    path("meetings/<int:pk>/transcript/", views.MeetingTranscriptAPIView.as_view(), name="meeting-transcript-api"),
    path("inbox/", views.InboxView.as_view(), name="inbox"),
    path("inbox/<uuid:thread_id>/", views.InboxThreadView.as_view(), name="inbox-thread"),
    path("api/leads/", views.LeadCreateAPIView.as_view(), name="lead-create"),  # public website contact form
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("google/connect/", views.GoogleConnectView.as_view(), name="google-connect"),
    path("google/callback/", views.GoogleCallbackView.as_view(), name="google-callback"),
]
