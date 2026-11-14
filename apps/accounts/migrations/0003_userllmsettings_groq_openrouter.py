from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_userllmsettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="userllmsettings",
            name="groq_api_key_encrypted",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="userllmsettings",
            name="groq_api_key_last4",
            field=models.CharField(blank=True, max_length=4),
        ),
        migrations.AddField(
            model_name="userllmsettings",
            name="groq_model",
            field=models.CharField(default="qwen/qwen3.8-27b", max_length=128),
        ),
        migrations.AddField(
            model_name="userllmsettings",
            name="openrouter_api_key_encrypted",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="userllmsettings",
            name="openrouter_api_key_last4",
            field=models.CharField(blank=True, max_length=4),
        ),
        migrations.AddField(
            model_name="userllmsettings",
            name="openrouter_model",
            field=models.CharField(
                default="nvidia/nemotron-3.5-super-vl:free", max_length=128
            ),
        ),
        migrations.AlterField(
            model_name="userllmsettings",
            name="gemini_model",
            field=models.CharField(default="gemini-3.5-flash", max_length=128),
        ),
        migrations.AlterField(
            model_name="userllmsettings",
            name="openai_model",
            field=models.CharField(default="gpt-4o-mini", max_length=128),
        ),
        migrations.AlterField(
            model_name="userllmsettings",
            name="provider",
            field=models.CharField(
                choices=[
                    ("openai", "OpenAI"),
                    ("gemini", "Gemini"),
                    ("groq", "Groq"),
                    ("openrouter", "OpenRouter"),
                ],
                default="openai",
                max_length=16,
            ),
        ),
    ]
