# Track last editor on ledger headers; backfill from owner / profile owner.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_updated_by(apps, schema_editor):
    Transaction = apps.get_model("finance", "Transaction")
    for txn in Transaction.objects.select_related("owner", "profile").iterator():
        user_id = txn.owner_id or getattr(txn.profile, "owner_id", None)
        if user_id and txn.updated_by_id != user_id:
            txn.updated_by_id = user_id
            txn.save(update_fields=["updated_by_id"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("finance", "0009_payment_contacts"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="updated_transactions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(backfill_updated_by, noop_reverse),
    ]
