import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models

# -----------------------------------------------------------------------------
# User manager
# -----------------------------------------------------------------------------


class UserManager(BaseUserManager):

    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra)


# -----------------------------------------------------------------------------
# Custom user model
# -----------------------------------------------------------------------------


class User(AbstractUser):

    username = None
    email = models.EmailField(unique=True)
    uuid = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, db_index=True
    )
    full_name = models.CharField(max_length=255, blank=True)
    avatar_url = models.URLField(blank=True)
    timezone = models.CharField(max_length=64, default="UTC")
    google_id = models.CharField(max_length=64, blank=True, null=True, unique=True)
    github_id = models.CharField(max_length=64, blank=True, null=True, unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


# -----------------------------------------------------------------------------
# MFA / security state
# -----------------------------------------------------------------------------


class UserSecurity(models.Model):

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="security")
    totp_secret = models.CharField(max_length=64, blank=True)
    mfa_enabled = models.BooleanField(default=False)
    mfa_prompt_dismissed = models.BooleanField(default=False)
    recovery_code_hashes = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"Security<{self.user.email}>"


def get_user_security(user) -> "UserSecurity":
    security, _ = UserSecurity.objects.get_or_create(user=user)
    return security


# -----------------------------------------------------------------------------
# Receipt AI (per-user LLM credentials)
# -----------------------------------------------------------------------------


class UserLlmSettings(models.Model):

    PROVIDER_OPENAI = "openai"
    PROVIDER_GEMINI = "gemini"
    PROVIDER_GROQ = "groq"
    PROVIDER_OPENROUTER = "openrouter"
    PROVIDER_CHOICES = (
        (PROVIDER_OPENAI, "OpenAI"),
        (PROVIDER_GEMINI, "Gemini"),
        (PROVIDER_GROQ, "Groq"),
        (PROVIDER_OPENROUTER, "OpenRouter"),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="llm")
    provider = models.CharField(
        max_length=16, choices=PROVIDER_CHOICES, default=PROVIDER_OPENAI
    )
    openai_api_key_encrypted = models.TextField(blank=True)
    openai_api_key_last4 = models.CharField(max_length=4, blank=True)
    openai_model = models.CharField(max_length=128, default="gpt-4o-mini")
    gemini_api_key_encrypted = models.TextField(blank=True)
    gemini_api_key_last4 = models.CharField(max_length=4, blank=True)
    gemini_model = models.CharField(max_length=128, default="gemini-3.5-flash")
    groq_api_key_encrypted = models.TextField(blank=True)
    groq_api_key_last4 = models.CharField(max_length=4, blank=True)
    groq_model = models.CharField(max_length=128, default="qwen/qwen3.8-27b")
    openrouter_api_key_encrypted = models.TextField(blank=True)
    openrouter_api_key_last4 = models.CharField(max_length=4, blank=True)
    openrouter_model = models.CharField(
        max_length=128, default="nvidia/nemotron-3.5-super-vl:free"
    )

    def __str__(self):
        return f"LLM<{self.user.email}>"


def get_user_llm_settings(user) -> "UserLlmSettings":
    settings_row, _ = UserLlmSettings.objects.get_or_create(user=user)
    return settings_row
