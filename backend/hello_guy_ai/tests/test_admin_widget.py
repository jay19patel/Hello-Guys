import pytest
from django.conf import settings

pytestmark = pytest.mark.django_db
CHAT_LINK = '<a href="/chat/">AI Chat</a>'


def test_app_is_above_admin_so_template_override_wins():
    apps = list(settings.INSTALLED_APPS)
    hello_idx = next(i for i, a in enumerate(apps) if "hello_guy_ai" in a)
    admin_idx = next(i for i, a in enumerate(apps) if "django.contrib.admin" in a)
    assert hello_idx < admin_idx


def test_admin_links_to_chat_page_without_popup(client, django_user_model):
    client.force_login(django_user_model.objects.create_superuser("root", "r@example.com", "pass12345"))
    for url in ("/admin/", "/admin/hello_guy_ai/thread/"):
        html = client.get(url).content.decode()
        assert CHAT_LINK in html, url
        assert "hg-widget" not in html and "widget.js" not in html  # no corner popup
        assert ("NJTechStudio CMS" in html or "Django administration" in html)


def test_full_page_chat(client, user, staff):
    assert client.get("/chat/").status_code == 302  # login required
    client.force_login(user)
    html = client.get("/chat/?thread=abc").content.decode()
    assert "hello_guy_ai/client.js" in html and "Admin ↗" not in html
    client.force_login(staff)
    assert "Admin ↗" in client.get("/chat/").content.decode()


@pytest.mark.parametrize("name", ["client.js", "chat-view.js", "markdown.js", "chat.css"])
def test_static_assets_resolve(name):
    from django.contrib.staticfiles import finders

    assert finders.find(f"hello_guy_ai/{name}")
