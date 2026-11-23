import os

from django.db import migrations, models

from hello_guy_ai.crypto import encrypt as encrypt_token


def copy_env_keys(apps, schema_editor):
    # Carry over keys that were configured via env so existing models keep working.
    for llm in apps.get_model("hello_guy_ai", "LLMModel").objects.exclude(api_key_env=""):
        llm.api_key = encrypt_token(os.environ.get(llm.api_key_env, ""))
        llm.save(update_fields=["api_key"])


class Migration(migrations.Migration):
    dependencies = [("hello_guy_ai", "0003_v2_document_kb_required")]

    operations = [
        migrations.AddField(model_name="llmmodel", name="api_key", field=models.TextField(blank=True)),
        migrations.RunPython(copy_env_keys, migrations.RunPython.noop),
        migrations.RemoveField(model_name="llmmodel", name="api_key_env"),
    ]
