from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)

from .models import get_user_security

User = get_user_model()


# -----------------------------------------------------------------------------
# User serializer
# -----------------------------------------------------------------------------


class UserSerializer(serializers.ModelSerializer):
    mfa_enabled = serializers.SerializerMethodField()
    show_mfa_prompt = serializers.SerializerMethodField()
    email_verified = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "uuid",
            "email",
            "full_name",
            "avatar_url",
            "timezone",
            "mfa_enabled",
            "show_mfa_prompt",
            "email_verified",
        ]
        read_only_fields = [
            "id",
            "uuid",
            "email",
            "mfa_enabled",
            "show_mfa_prompt",
            "email_verified",
        ]

    def get_mfa_enabled(self, obj):
        return get_user_security(obj).mfa_enabled

    def get_show_mfa_prompt(self, obj):
        security = get_user_security(obj)
        return not security.mfa_enabled and not security.mfa_prompt_dismissed

    def get_email_verified(self, obj):
        from .email_verification import email_verified

        return email_verified(obj)


# -----------------------------------------------------------------------------
# Registration serializer
# -----------------------------------------------------------------------------


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, min_length=8, style={"input_type": "password"}
    )

    class Meta:
        model = User
        fields = ["email", "full_name", "password", "timezone"]

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


# -----------------------------------------------------------------------------
# JWT login serializer
# -----------------------------------------------------------------------------


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["email"] = user.email
        return token

    def validate(self, attrs):
        super().validate(attrs)
        return attrs


class RememberAwareTokenRefreshSerializer(TokenRefreshSerializer):
    """Rotate refresh tokens while preserving the remember lifetime claim."""

    def validate(self, attrs):
        from datetime import timedelta

        from django.conf import settings
        from rest_framework_simplejwt.settings import api_settings
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken(attrs["refresh"])
        remember_raw = refresh.get("remember", True)
        if isinstance(remember_raw, str):
            remember = remember_raw.strip().lower() in ("1", "true", "yes", "on")
        else:
            remember = True if remember_raw is None else bool(remember_raw)

        data = {"access": str(refresh.access_token), "remember": remember}

        if api_settings.ROTATE_REFRESH_TOKENS:
            if api_settings.BLACKLIST_AFTER_ROTATION:
                try:
                    refresh.blacklist()
                except AttributeError:
                    pass
            refresh.set_jti()
            refresh.set_iat()
            refresh["remember"] = remember
            days = (
                max(1, int(getattr(settings, "JWT_REFRESH_DAYS", 90)))
                if remember
                else max(1, int(getattr(settings, "JWT_REFRESH_DAYS_SHORT", 7)))
            )
            refresh.set_exp(lifetime=timedelta(days=days))
            data["refresh"] = str(refresh)

        return data
