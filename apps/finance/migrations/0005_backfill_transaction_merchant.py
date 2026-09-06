"""Data migration: best-effort backfill Transaction.merchant from title/note/receipt."""

from django.db import migrations


def backfill_merchant(apps, schema_editor):
    Transaction = apps.get_model("finance", "Transaction")
    for txn in Transaction.objects.filter(type="expense").iterator():
        if getattr(txn, "merchant", None):
            continue
        merchant = ""
        if txn.receipt_image:
            merchant = (txn.title or "").strip()
        elif txn.note and txn.title and txn.note.startswith(txn.title):
            merchant = (txn.title or "").strip()
        if merchant:
            Transaction.objects.filter(pk=txn.pk).update(merchant=merchant)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0004_transaction_merchant_and_purchase_prefs"),
    ]

    operations = [
        migrations.RunPython(backfill_merchant, noop_reverse),
    ]
