import pytest
from django.urls import reverse

from cms.models import Project
from crm.models import Customer, Opportunity
from meta_connector.models import PlatformAccount
from meta_connector.roles import VIEWER, set_role

pytestmark = pytest.mark.django_db


def test_hub_needs_a_dashboard_role(client, django_user_model):
    assert '/meta/login/' in client.get(reverse('core:portal')).url
    user = django_user_model.objects.create_user('nobody')
    client.force_login(user)
    assert client.get(reverse('core:portal')).status_code == 403


def test_hub_shows_each_module(client, django_user_model):
    user = django_user_model.objects.create_user('viewer')
    set_role(user, VIEWER)
    client.force_login(user)
    PlatformAccount.objects.create(platform='whatsapp', display_name='WA', phone_number_id='wa_1')
    Opportunity.objects.create(customer=Customer.objects.create(name='John'), title='Website')
    Project.objects.create(title='Portal', slug='portal', description='Web portal', is_active=True, featured=True)
    response = client.get(reverse('core:portal'))
    assert response.status_code == 200
    for text in ('Meta Connector', 'CMS Studio', 'Open CRM', '/crm/', '/dashboard/', '1 New'):
        assert text in response.content.decode()
