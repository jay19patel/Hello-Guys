from hello_guy_ai.checks import check_encryption_key


def test_missing_encryption_key_fails_deploy_check(settings):
    settings.FIELD_ENCRYPTION_KEY = ""
    assert [e.id for e in check_encryption_key(None)] == ["hello_guy_ai.E001"]


def test_encryption_key_set_passes(settings):
    settings.FIELD_ENCRYPTION_KEY = "x" * 44
    assert check_encryption_key(None) == []
