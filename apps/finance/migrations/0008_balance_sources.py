# Per-profile balance sources: seed Cash, attach transactions, drop profile starting_balance.

import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def forwards_sources(apps, schema_editor):
    BudgetProfile = apps.get_model("finance", "BudgetProfile")
    BalanceSource = apps.get_model("finance", "BalanceSource")
    Transaction = apps.get_model("finance", "Transaction")

    cash_by_profile = {}
    for profile in BudgetProfile.objects.all().iterator():
        starting = profile.starting_balance or Decimal("0.00")
        cash = BalanceSource.objects.create(
            owner_id=profile.owner_id,
            profile_id=profile.id,
            name="Cash",
            kind="cash",
            starting_balance=starting,
            is_system=True,
            status="active",
        )
        cash_by_profile[profile.id] = cash.id

    for txn in Transaction.objects.all().iterator():
        cash_id = cash_by_profile.get(txn.profile_id)
        if cash_id is None:
            continue
        Transaction.objects.filter(pk=txn.pk).update(source_id=cash_id)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("finance", "0007_budget_profiles"),
    ]

    operations = [
        migrations.CreateModel(
            name="BalanceSource",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "uuid",
                    models.UUIDField(
                        db_index=True, default=uuid.uuid4, editable=False, unique=True
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("cash", "Cash"),
                            ("bank", "Bank"),
                            ("ewallet", "E-wallet"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "starting_balance",
                    models.DecimalField(
                        decimal_places=2, default=Decimal("0.00"), max_digits=14
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Active"), ("archived", "Archived")],
                        default="active",
                        max_length=20,
                    ),
                ),
                ("is_system", models.BooleanField(default=False)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(class)ss",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sources",
                        to="finance.budgetprofile",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "abstract": False,
            },
        ),
        migrations.AddConstraint(
            model_name="balancesource",
            constraint=models.UniqueConstraint(
                fields=("profile", "name"),
                name="finance_source_unique_name_per_profile",
            ),
        ),
        migrations.AddConstraint(
            model_name="balancesource",
            constraint=models.UniqueConstraint(
                condition=models.Q(("kind", "cash")),
                fields=("profile",),
                name="finance_source_one_cash_per_profile",
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="source",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="transactions",
                to="finance.balancesource",
            ),
        ),
        migrations.RunPython(forwards_sources, noop_reverse),
        migrations.AlterField(
            model_name="transaction",
            name="source",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="transactions",
                to="finance.balancesource",
            ),
        ),
        migrations.RemoveField(
            model_name="budgetprofile",
            name="starting_balance",
        ),
    ]
