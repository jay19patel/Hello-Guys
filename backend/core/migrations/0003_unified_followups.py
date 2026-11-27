import django.db.models.deletion
from django.db import migrations, models
from django.utils.text import slugify


def migrate_crm_followups(apps, schema_editor):
    FollowUp = apps.get_model("crm", "FollowUp")
    GlobalFollowUp = apps.get_model("core", "GlobalFollowUp")
    FollowUpChannel = apps.get_model("core", "FollowUpChannel")

    channel_labels = {
        "whatsapp": "WhatsApp",
        "instagram": "Instagram",
        "call": "Phone Call",
        "email": "Email",
        "meeting": "Meeting",
    }

    for fu in FollowUp.objects.select_related("customer", "opportunity").all():
        existing = GlobalFollowUp.objects.filter(
            due_at=fu.due_at,
            entity_type="customer",
            entity_id=str(fu.customer_id),
        ).first()
        if existing:
            existing.customer_id = fu.customer_id
            existing.opportunity_id = fu.opportunity_id
            existing.save(update_fields=["customer", "opportunity"])
            continue

        ch_name = channel_labels.get(fu.channel, "General")
        ch, _ = FollowUpChannel.objects.get_or_create(
            slug=slugify(ch_name),
            defaults={"name": ch_name},
        )
        GlobalFollowUp.objects.create(
            title=(fu.note or "").strip()[:200] or f"Follow up with {fu.customer.name}",
            note=fu.note or "",
            channel=ch,
            due_at=fu.due_at,
            status=fu.status,
            assigned_to_id=fu.assigned_to_id,
            created_by_id=fu.created_by_id,
            customer_id=fu.customer_id,
            opportunity_id=fu.opportunity_id,
            related_label=fu.customer.name[:150],
            related_url=f"/crm/opportunities/{fu.opportunity_id}/" if fu.opportunity_id else f"/crm/customers/{fu.customer_id}/",
            entity_type="opportunity" if fu.opportunity_id else "customer",
            entity_id=str(fu.opportunity_id or fu.customer_id),
            completed_at=fu.completed_at,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_followupchannel_globalfollowup"),
        ("crm", "0006_meeting_attendee_details_meeting_last_synced_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="globalfollowup",
            name="customer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="follow_ups",
                to="crm.customer",
            ),
        ),
        migrations.AddField(
            model_name="globalfollowup",
            name="opportunity",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="follow_ups",
                to="crm.opportunity",
            ),
        ),
        migrations.RunPython(migrate_crm_followups, migrations.RunPython.noop),
    ]
