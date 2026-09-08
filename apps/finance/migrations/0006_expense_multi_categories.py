# Expense header multi-categories; drop per-line item category.

import django.db.models.deletion
from django.db import migrations, models


def forwards_rollup_categories(apps, schema_editor):
    Transaction = apps.get_model("finance", "Transaction")
    TransactionItem = apps.get_model("finance", "TransactionItem")
    Through = Transaction.categories.through

    for txn in Transaction.objects.filter(type="expense").iterator():
        ordered = []
        seen = set()

        def add(cid):
            if cid is None or cid in seen:
                return
            seen.add(cid)
            ordered.append(cid)

        add(txn.category_id)
        for cid in (
            TransactionItem.objects.filter(transaction_id=txn.pk)
            .order_by("id")
            .values_list("category_id", flat=True)
        ):
            add(cid)

        ordered = ordered[:5]
        if not ordered:
            continue

        if txn.category_id is None or txn.category_id not in seen:
            Transaction.objects.filter(pk=txn.pk).update(category_id=ordered[0])

        Through.objects.bulk_create(
            [Through(transaction_id=txn.pk, category_id=cid) for cid in ordered],
            ignore_conflicts=True,
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0005_backfill_transaction_merchant"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="categories",
            field=models.ManyToManyField(
                blank=True,
                related_name="tagged_transactions",
                to="finance.category",
            ),
        ),
        migrations.RunPython(forwards_rollup_categories, noop_reverse),
        migrations.RemoveField(
            model_name="transactionitem",
            name="category",
        ),
    ]
