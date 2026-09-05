from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0002_migrate_costs"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="transactionitem",
            name="date_effective",
        ),
    ]
