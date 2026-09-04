"""Migrate CostList/CostItem data into Transaction/TransactionItem."""

from decimal import Decimal

from django.db import migrations
from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce


def forwards(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    CostList = apps.get_model("costs", "CostList")
    CostItem = apps.get_model("costs", "CostItem")
    Category = apps.get_model("finance", "Category")
    Transaction = apps.get_model("finance", "Transaction")
    TransactionItem = apps.get_model("finance", "TransactionItem")
    UserFinance = apps.get_model("finance", "UserFinance")

    expense_seeds = [
        "Food",
        "Transport",
        "Housing",
        "Utilities",
        "Health",
        "Entertainment",
        "Shopping",
        "Other",
    ]
    income_seeds = ["Salary", "Freelance", "Gifts", "Other"]

    line_total = ExpressionWrapper(
        F("cost") * F("quantity"),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )

    for user in User.objects.all():
        UserFinance.objects.get_or_create(
            user=user, defaults={"starting_balance": Decimal("0.00")}
        )

        if not Category.objects.filter(owner=user, kind="expense").exists():
            for name in expense_seeds:
                Category.objects.create(
                    owner=user,
                    name=name,
                    kind="expense",
                    parent=None,
                    is_system=True,
                )
        if not Category.objects.filter(owner=user, kind="income").exists():
            for name in income_seeds:
                Category.objects.create(
                    owner=user,
                    name=name,
                    kind="income",
                    parent=None,
                    is_system=True,
                )

        other = (
            Category.objects.filter(
                owner=user, kind="expense", name="Other", parent=None
            )
            .order_by("id")
            .first()
        )
        if other is None:
            other = (
                Category.objects.filter(owner=user, kind="expense", parent=None)
                .order_by("id")
                .first()
            )
        if other is None:
            continue

        for cost_list in CostList.objects.filter(owner=user).iterator():
            items = list(CostItem.objects.filter(cost_list=cost_list))
            total = Decimal("0.00")
            for item in items:
                total += (item.cost or Decimal("0")) * (item.quantity or Decimal("0"))

            txn = Transaction.objects.create(
                owner=user,
                type="expense",
                amount=total,
                title=cost_list.title or "Untitled",
                note=cost_list.description or "",
                category_id=other.id,
                status=cost_list.status or "active",
                date_created=cost_list.date_created,
                date_effective=getattr(cost_list, "date_effective", None)
                or cost_list.date_created,
                receipt_image=cost_list.receipt_image,
                uuid=cost_list.uuid,
                created_at=cost_list.created_at,
                updated_at=cost_list.updated_at,
            )
            # uuid is unique — CostList and Transaction both have uuid; copying may
            # conflict if we generate new ones. Prefer new uuid on Transaction.
            # Actually we set uuid=cost_list.uuid which could be fine since different tables.
            for item in items:
                TransactionItem.objects.create(
                    owner=user,
                    transaction=txn,
                    title=item.title,
                    status=item.status or "active",
                    cost=item.cost,
                    quantity=item.quantity,
                    unit=getattr(item, "unit", None) or "pcs",
                    category_id=other.id,
                    date_created=item.date_created,
                    date_effective=getattr(item, "date_effective", None)
                    or item.date_created,
                    uuid=item.uuid,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )


def backwards(apps, schema_editor):
    Transaction = apps.get_model("finance", "Transaction")
    TransactionItem = apps.get_model("finance", "TransactionItem")
    TransactionItem.objects.all().delete()
    Transaction.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0001_initial"),
        ("costs", "0005_costlist_receipt_image"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
