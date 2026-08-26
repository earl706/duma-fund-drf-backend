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
