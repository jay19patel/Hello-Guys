import django.db.models.deletion
from django.db import migrations, models


def drop_orphans(apps, schema_editor):
    # Documents whose assistant_id was empty in v1 have no KB; they cannot be searched anyway.
    apps.get_model("hello_guy_ai", "Document").objects.filter(knowledge_base__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("hello_guy_ai", "0002_v2_flow_architecture")]

    operations = [
        migrations.RunPython(drop_orphans, migrations.RunPython.noop),
        migrations.RemoveField(model_name="document", name="assistant_id"),
        migrations.AlterField(
            model_name="document",
            name="knowledge_base",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="documents",
                to="hello_guy_ai.knowledgebase",
            ),
        ),
    ]
