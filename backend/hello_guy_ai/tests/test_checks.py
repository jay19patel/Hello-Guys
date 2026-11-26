import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

from hello_guy_ai import crypto
from hello_guy_ai.checks import check_encryption_key


def test_missing_encryption_key_fails_deploy_check(settings):
    settings.FIELD_ENCRYPTION_KEY = ""
    assert [e.id for e in check_encryption_key(None)] == ["hello_guy_ai.E001"]


def test_encryption_key_set_passes(settings):
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()
    assert check_encryption_key(None) == []


def test_non_fernet_key_fails_deploy_check_and_encryption_in_production(settings):
    settings.FIELD_ENCRYPTION_KEY = "replace-with-fernet-key"
    settings.DEBUG = False
    assert [e.id for e in check_encryption_key(None)] == ["hello_guy_ai.E001"]
    with pytest.raises(ImproperlyConfigured):
        crypto.encrypt("secret")
    settings.DEBUG = True  # dev keeps the old derived-key behaviour
    assert crypto.decrypt(crypto.encrypt("secret")) == "secret"


def test_one_llm_call_fits_inside_the_run_time_limit():
    from hello_guy_ai.conf import DEFAULTS

    worst_call = DEFAULTS["LLM_TIMEOUT_SECONDS"] * (DEFAULTS["LLM_NUM_RETRIES"] + 1)
    assert worst_call < DEFAULTS["RUN_TIME_LIMIT_SECONDS"]
