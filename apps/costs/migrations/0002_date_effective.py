from decimal import Decimal

from django.db.models import F

from django.db import migrations, models
import apps.costs.models


def copy_date_created_to_effective(apps, schema_editor):
    CostList = apps.get_model("costs", "CostList")
    CostItem = apps.get_model("costs", "CostItem")
    CostList.objects.all().update(date_effective=F("date_created"))
    CostItem.objects.all().update(date_effective=F("date_created"))


class Migration(migrations.Migration):

    dependencies = [
        ("costs", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="costlist",
            name="date_effective",
            field=models.DateField(default=apps.costs.models.today),
        ),
        migrations.AddField(
            model_name="costitem",
            name="date_effective",
            field=models.DateField(default=apps.costs.models.today),
        ),
        migrations.RunPython(copy_date_created_to_effective, migrations.RunPython.noop),
    ]
