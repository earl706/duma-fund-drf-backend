# Internal wallet moves: to_source + fee on a single transfer_out row.

import django.db.models.deletion
from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0010_transaction_updated_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="to_source",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="incoming_transfers",
                to="finance.balancesource",
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="fee",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=14,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
    ]
