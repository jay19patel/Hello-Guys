from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from django.contrib.auth import views as auth_views
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.throttling import ScopedRateThrottle
from meta_connector.views import (
    AccountDetailView, ApprovalActionView, ApprovalQueueView, ContactDetailView,
    DashboardIndexView, SettingsView, WebhookActionsView,
)


class ThrottledObtainAuthToken(ObtainAuthToken):
    """Username/password -> API token, rate limited per IP (password guessing)."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/token/', ThrottledObtainAuthToken.as_view(), name='api_token_auth'),

    # Meta Connector Dashboard
    path('dashboard/login/', auth_views.LoginView.as_view(template_name='dashboard/login.html', redirect_authenticated_user=True), name='dashboard-login'),
    path('dashboard/logout/', auth_views.LogoutView.as_view(), name='dashboard-logout'),
    path('dashboard/settings/', SettingsView.as_view(), name='dashboard-settings'),
    path('dashboard/webhooks/', WebhookActionsView.as_view(), name='dashboard-webhooks'),
    path('dashboard/accounts/<int:pk>/', AccountDetailView.as_view(), name='dashboard-account'),
    path('dashboard/insights/', RedirectView.as_view(pattern_name='dashboard', query_string=True), name='dashboard-insights'),
    path('dashboard/contacts/<int:pk>/', ContactDetailView.as_view(), name='dashboard-contact'),
    path('dashboard/approvals/', ApprovalQueueView.as_view(), name='dashboard-approvals'),
    path('dashboard/approvals/<int:pk>/action/', ApprovalActionView.as_view(), name='dashboard-approval-action'),
    path('dashboard/', DashboardIndexView.as_view(), name='dashboard'),

    # CMS content is managed via Django Admin (/admin/cms/)
    # Legacy /cms/ redirect to admin CMS section
    path('cms/', RedirectView.as_view(url='/admin/cms/', permanent=False)),

    # APIs (read-only for frontend, write for inquiries)
    path('api/meta/', include('meta_connector.urls', namespace='meta_connector')),
    path('api/cms/', include('cms.urls', namespace='cms')),
    path('crm/', include('crm.urls', namespace='crm')),

    # Hello Guy AI — chat UI + REST API + SSE stream
    path('', include('hello_guy_ai.urls')),
]
