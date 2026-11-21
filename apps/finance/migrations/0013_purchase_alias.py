# Alias map so distinct normalized titles can share insights/lookup history.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("finance", "0012_transaction_is_partial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PurchaseAlias",
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
                ("alias_key", models.CharField(db_index=True, max_length=255)),
                ("canonical_key", models.CharField(db_index=True, max_length=255)),
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
                        related_name="purchase_aliases",
                        to="finance.budgetprofile",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        fields=("profile", "alias_key"),
                        name="finance_purchase_alias_unique_profile_key",
                    )
                ],
            },
        ),
    ]
