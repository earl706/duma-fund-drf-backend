# Profile payee directory: bank / e-wallet details + optional QR image.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("finance", "0008_balance_sources"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentContact",
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
                ("display_name", models.CharField(max_length=100)),
                (
                    "channel",
                    models.CharField(
                        choices=[("bank", "Bank"), ("ewallet", "E-wallet")],
                        max_length=20,
                    ),
                ),
                ("account_name", models.CharField(blank=True, max_length=255)),
                ("account_number", models.CharField(blank=True, max_length=64)),
                ("note", models.TextField(blank=True)),
                (
                    "qr_image",
                    models.ImageField(blank=True, null=True, upload_to="payees/%Y/%m/"),
                ),
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
                        related_name="payees",
                        to="finance.budgetprofile",
                    ),
                ),
            ],
            options={
                "ordering": ["display_name", "id"],
                "abstract": False,
            },
        ),
    ]
