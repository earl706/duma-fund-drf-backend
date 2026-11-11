from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserLlmSettings",
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
                    "provider",
                    models.CharField(
                        choices=[("openai", "OpenAI"), ("gemini", "Gemini")],
                        default="openai",
                        max_length=16,
                    ),
                ),
                ("openai_api_key_encrypted", models.TextField(blank=True)),
                ("openai_api_key_last4", models.CharField(blank=True, max_length=4)),
                ("openai_model", models.CharField(default="gpt-4o-mini", max_length=64)),
                ("gemini_api_key_encrypted", models.TextField(blank=True)),
                ("gemini_api_key_last4", models.CharField(blank=True, max_length=4)),
                (
                    "gemini_model",
                    models.CharField(default="gemini-3.5-flash", max_length=64),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="llm",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
