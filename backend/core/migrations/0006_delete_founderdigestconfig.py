from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_founderdigestconfig'),
    ]

    operations = [
        migrations.DeleteModel(
            name='FounderDigestConfig',
        ),
    ]
