# Per-day display order for transactions sharing date_effective.

from django.db import migrations, models


def seed_day_order(apps, schema_editor):
    Transaction = apps.get_model("finance", "Transaction")
    qs = Transaction.objects.order_by(
        "profile_id", "date_effective", "-created_at", "id"
    )
    current_key = None
    index = 0
    batch = []
    for row in qs.iterator(chunk_size=500):
        key = (row.profile_id, row.date_effective)
        if key != current_key:
            current_key = key
            index = 0
        if row.day_order != index:
            row.day_order = index
            batch.append(row)
        index += 1
        if len(batch) >= 200:
            Transaction.objects.bulk_update(batch, ["day_order"])
            batch = []
    if batch:
        Transaction.objects.bulk_update(batch, ["day_order"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0013_purchase_alias"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="day_order",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterModelOptions(
            name="transaction",
            options={"ordering": ["-date_effective", "day_order", "-created_at"]},
        ),
        migrations.RunPython(seed_day_order, noop_reverse),
    ]
