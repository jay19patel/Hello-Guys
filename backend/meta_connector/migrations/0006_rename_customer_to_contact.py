# Hand-written so existing rows are kept: rename, never delete + create.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('meta_connector', '0005_platformaccount_token_checked_at_and_more'),
    ]

    operations = [
        migrations.RenameModel(old_name='Customer', new_name='Contact'),
        migrations.AlterModelOptions(
            name='contact',
            options={'ordering': ['-last_message_at', '-first_seen'], 'verbose_name': 'Contact', 'verbose_name_plural': 'Contacts'},
        ),
        migrations.AlterField(
            model_name='contact',
            name='platform_account',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contacts', to='meta_connector.platformaccount'),
        ),
        # Index only (no data): drop it before the rename, re-create it on the new field name below.
        migrations.RemoveIndex(model_name='message', name='meta_connec_platfor_e446dd_idx'),
        migrations.RenameField(model_name='message', old_name='customer', new_name='contact'),
        migrations.RenameField(model_name='autodmlog', old_name='customer', new_name='contact'),
        migrations.AddIndex(
            model_name='message',
            index=models.Index(fields=['platform_account', 'contact', 'created_at'], name='meta_connec_platfor_59baec_idx'),
        ),
    ]
