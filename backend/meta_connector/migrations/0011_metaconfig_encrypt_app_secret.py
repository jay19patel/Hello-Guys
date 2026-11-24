from django.db import migrations, models


def encrypt_existing(apps, schema_editor):
    from meta_connector.utils import encrypt_token

    for config in apps.get_model("meta_connector", "MetaConfig").objects.exclude(app_secret_encrypted=""):
        config.app_secret_encrypted = encrypt_token(config.app_secret_encrypted)  # was plaintext until now
        config.save(update_fields=["app_secret_encrypted"])


def decrypt_existing(apps, schema_editor):
    from meta_connector.utils import decrypt_token

    for config in apps.get_model("meta_connector", "MetaConfig").objects.exclude(app_secret_encrypted=""):
        config.app_secret_encrypted = decrypt_token(config.app_secret_encrypted)
        config.save(update_fields=["app_secret_encrypted"])


class Migration(migrations.Migration):
    dependencies = [("meta_connector", "0010_ai_auto_reply")]

    operations = [
        migrations.RenameField("metaconfig", "app_secret", "app_secret_encrypted"),
        migrations.AlterField(
            model_name="metaconfig",
            name="app_secret_encrypted",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Meta App Secret (App Settings > Basic) used for HMAC SHA-256 signature verification",
            ),
        ),
        migrations.RunPython(encrypt_existing, decrypt_existing),
    ]
