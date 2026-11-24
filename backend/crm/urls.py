from django.urls import path

from crm import views

app_name = "crm"

urlpatterns = [
    path("", views.OverviewView.as_view(), name="overview"),
    path("opportunities/", views.OpportunityListView.as_view(), name="opportunities"),
    path("opportunities/new/", views.OpportunityCreateView.as_view(), name="opportunity-create"),
    path("opportunities/<int:pk>/", views.OpportunityDetailView.as_view(), name="opportunity"),
    path("customers/", views.CustomerListView.as_view(), name="customers"),
    path("customers/new/", views.CustomerCreateView.as_view(), name="customer-create"),
    path("customers/<int:pk>/", views.CustomerDetailView.as_view(), name="customer"),
    path("meetings/", views.MeetingListView.as_view(), name="meetings"),
    path("meetings/new/", views.MeetingCreateView.as_view(), name="meeting-create"),
    path("meetings/<int:pk>/", views.MeetingDetailView.as_view(), name="meeting"),
    path("inbox/", views.InboxView.as_view(), name="inbox"),
    path("inbox/<uuid:thread_id>/", views.InboxThreadView.as_view(), name="inbox-thread"),
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("google/connect/", views.GoogleConnectView.as_view(), name="google-connect"),
    path("google/callback/", views.GoogleCallbackView.as_view(), name="google-callback"),
]
