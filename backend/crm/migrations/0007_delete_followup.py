from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_unified_followups"),
        ("crm", "0006_meeting_attendee_details_meeting_last_synced_at_and_more"),
    ]

    operations = [
        migrations.DeleteModel(
            name="FollowUp",
        ),
    ]
