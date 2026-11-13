# Partial receipt flag: logged expense lines may omit some receipt items.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0011_transaction_internal_transfer"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="is_partial",
            field=models.BooleanField(default=False),
        ),
    ]
