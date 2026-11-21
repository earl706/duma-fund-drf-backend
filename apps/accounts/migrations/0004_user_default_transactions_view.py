from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_userllmsettings_groq_openrouter"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="default_transactions_view",
            field=models.CharField(
                choices=[("list", "List"), ("calendar", "Calendar")],
                default="list",
                max_length=16,
            ),
        ),
    ]
