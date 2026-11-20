from django.urls import path
from core.views import HubPortalView

app_name = 'core'

urlpatterns = [
    path('', HubPortalView.as_view(), name='portal'),
]

