from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from django.contrib.auth import views as auth_views
from rest_framework.authtoken.views import obtain_auth_token
from core.views import HubPortalView
from meta_connector.views import DashboardIndexView, settings_view, webhook_actions_view, account_detail_view, contact_detail_view

urlpatterns = [
    # Root workspace launcher / module hub
    path('', HubPortalView.as_view(), name='hub-portal'),

    path('admin/', admin.site.urls),
    path('api/auth/token/', obtain_auth_token, name='api_token_auth'),

    # Meta Connector Dashboard
    path('dashboard/login/', auth_views.LoginView.as_view(template_name='dashboard/login.html', redirect_authenticated_user=True), name='dashboard-login'),
    path('dashboard/logout/', auth_views.LogoutView.as_view(), name='dashboard-logout'),
    path('dashboard/settings/', settings_view, name='dashboard-settings'),
    path('dashboard/webhooks/', webhook_actions_view, name='dashboard-webhooks'),
    path('dashboard/accounts/<int:pk>/', account_detail_view, name='dashboard-account'),
    path('dashboard/insights/', RedirectView.as_view(pattern_name='dashboard', query_string=True), name='dashboard-insights'),
    path('dashboard/contacts/<int:pk>/', contact_detail_view, name='dashboard-contact'),
    path('dashboard/', DashboardIndexView.as_view(), name='dashboard'),

    # CMS content is managed via Django Admin (/admin/cms/)
    # Legacy /cms/ redirect to admin CMS section
    path('cms/', RedirectView.as_view(url='/admin/cms/', permanent=False)),

    # APIs (read-only for frontend, write for inquiries)
    path('api/meta/', include('meta_connector.urls', namespace='meta_connector')),
    path('api/cms/', include('cms.urls', namespace='cms')),

    # Hello Guy AI — chat UI + REST API + SSE stream
    path('', include('hello_guy_ai.urls')),
]
