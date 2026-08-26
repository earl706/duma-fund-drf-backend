import base64
import io
import secrets
from datetime import timedelta

import qrcode
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from .mfa import (
    MFA_TOKEN_MAX_AGE,
    create_mfa_token,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_codes,
    provisioning_uri,
    verify_mfa_token,
    verify_recovery_code,
    verify_totp,
)
from .models import UserSecurity, get_user_security
from .oauth import (
    PROVIDERS,
    authorize_url,
    create_oauth_state,
    exchange_code,
    pop_oauth_state,
)
from .email_verification import mark_email_verified
from .serializers import RememberAwareTokenRefreshSerializer, UserSerializer

User = get_user_model()


# -----------------------------------------------------------------------------
# Token & security helpers
# -----------------------------------------------------------------------------


def get_security(user) -> UserSecurity:
    return get_user_security(user)


def parse_remember(value, default=True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def refresh_lifetime_days(remember: bool) -> int:
    if remember:
        return max(1, int(getattr(settings, "JWT_REFRESH_DAYS", 90)))
    return max(1, int(getattr(settings, "JWT_REFRESH_DAYS_SHORT", 7)))


def issue_tokens(user, *, remember=True) -> dict:
    """Issue access + refresh; refresh lifetime is 90d (remember) or 7d."""
    remember = bool(remember)
    refresh = RefreshToken.for_user(user)
    refresh["remember"] = remember
    refresh.set_exp(lifetime=timedelta(days=refresh_lifetime_days(remember)))
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": UserSerializer(user).data,
        "remember": remember,
    }


def stash_mfa_remember(mfa_token: str, remember: bool) -> None:
    if not mfa_token:
        return
    cache.set(
        f"mfa-remember:{mfa_token}",
        bool(remember),
        timeout=MFA_TOKEN_MAX_AGE,
    )


def pop_mfa_remember(mfa_token: str, default=True) -> bool:
    if not mfa_token:
        return default
    key = f"mfa-remember:{mfa_token}"
    val = cache.get(key)
    if val is None:
        return default
    cache.delete(key)
    return bool(val)


def store_oauth_result(payload: dict) -> str:
    code = secrets.token_urlsafe(32)
    cache.set(f"oauth-result:{code}", payload, timeout=120)
    return code


class RememberAwareTokenRefreshView(TokenRefreshView):
    serializer_class = RememberAwareTokenRefreshSerializer


# -----------------------------------------------------------------------------
# MFA verification & management views
# -----------------------------------------------------------------------------


class MfaVerifyView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        mfa_token = request.data.get("mfa_token")
        code = (request.data.get("code") or "").strip()
        recovery_code = (request.data.get("recovery_code") or "").strip()
        user_id = verify_mfa_token(mfa_token)
        if not user_id:
            return Response({"detail": "Invalid or expired MFA session."}, status=400)
        user = get_object_or_404(User, pk=user_id)
        security = get_security(user)
        if not security.mfa_enabled:
            return Response({"detail": "MFA is not enabled."}, status=400)
        if recovery_code:
            ok, remaining = verify_recovery_code(
                security.recovery_code_hashes, recovery_code
            )
            if not ok:
                return Response({"detail": "Invalid recovery code."}, status=400)
            security.recovery_code_hashes = remaining
            security.save(update_fields=["recovery_code_hashes"])
        elif not verify_totp(security.totp_secret, code):
            return Response({"detail": "Invalid authentication code."}, status=400)
        remember = pop_mfa_remember(mfa_token, default=True)
        if "remember" in request.data:
            remember = parse_remember(request.data.get("remember"), default=remember)
        return Response(issue_tokens(user, remember=remember))


class MfaSetupView(APIView):

    def get(self, request):
        security = get_security(request.user)
        if security.mfa_enabled:
            return Response({"detail": "MFA is already enabled."}, status=400)
        secret = generate_totp_secret()
        security.totp_secret = secret
        security.save(update_fields=["totp_secret"])
        uri = provisioning_uri(request.user.email, secret)
        qr = qrcode.make(uri)
        buf = io.BytesIO()
        qr.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
        return Response({"secret": secret, "otpauth_uri": uri, "qr_png_base64": qr_b64})


class MfaSetupConfirmView(APIView):

    def post(self, request):
        code = (request.data.get("code") or "").strip()
        security = get_security(request.user)
        if security.mfa_enabled:
            return Response({"detail": "MFA is already enabled."}, status=400)
        if not security.totp_secret or not verify_totp(security.totp_secret, code):
            return Response({"detail": "Invalid authentication code."}, status=400)
        plain_codes = generate_recovery_codes()
        security.mfa_enabled = True
        security.recovery_code_hashes = hash_recovery_codes(plain_codes)
        security.save(update_fields=["mfa_enabled", "recovery_code_hashes"])
        return Response({"recovery_codes": plain_codes})


class MfaDisableView(APIView):

    def post(self, request):
        code = (request.data.get("code") or "").strip()
        security = get_security(request.user)
        if not security.mfa_enabled:
            return Response({"detail": "MFA is not enabled."}, status=400)
        if not verify_totp(security.totp_secret, code):
            return Response({"detail": "Invalid authentication code."}, status=400)
        security.mfa_enabled = False
        security.totp_secret = ""
        security.recovery_code_hashes = []
        security.save(
            update_fields=["mfa_enabled", "totp_secret", "recovery_code_hashes"]
        )
        return Response({"detail": "MFA disabled."})


class MfaDismissPromptView(APIView):

    def post(self, request):
        security = get_security(request.user)
        security.mfa_prompt_dismissed = True
        security.save(update_fields=["mfa_prompt_dismissed"])
        return Response({"detail": "Prompt dismissed."})


# -----------------------------------------------------------------------------
# OAuth flow views
# -----------------------------------------------------------------------------


class OAuthStartView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, provider):
        if provider not in PROVIDERS:
            return Response({"detail": "Unknown provider."}, status=404)
        state = create_oauth_state(cache, provider)
        return HttpResponseRedirect(authorize_url(provider, state))


class OAuthCallbackView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, provider):
        from django.conf import settings

        error = request.GET.get("error")
        if error:
            return HttpResponseRedirect(
                f"{settings.FRONTEND_URL}/oauth/callback?error={error}"
            )
        state = request.GET.get("state", "")
        code = request.GET.get("code", "")
        if pop_oauth_state(cache, state) != provider:
            return HttpResponseRedirect(
                f"{settings.FRONTEND_URL}/oauth/callback?error=invalid_state"
            )
        try:
            profile = exchange_code(provider, code)
        except Exception:
            return HttpResponseRedirect(
                f"{settings.FRONTEND_URL}/oauth/callback?error=oauth_failed"
            )
        if not profile.get("email"):
            return HttpResponseRedirect(
                f"{settings.FRONTEND_URL}/oauth/callback?error=no_email"
            )
        user = resolve_oauth_user(profile)
        mark_email_verified(user, profile["email"])
        security = get_security(user)
        if security.mfa_enabled:
            mfa_token = create_mfa_token(user.id)
            stash_mfa_remember(mfa_token, True)
            payload = {"mfa_required": True, "mfa_token": mfa_token}
        else:
            payload = issue_tokens(user, remember=True)
        exchange_code_val = store_oauth_result(payload)
        return HttpResponseRedirect(
            f"{settings.FRONTEND_URL}/oauth/callback?code={exchange_code_val}"
        )


class OAuthExchangeView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        code = request.data.get("code", "")
        key = f"oauth-result:{code}"
        payload = cache.get(key)
        if not payload:
            return Response({"detail": "Invalid or expired OAuth code."}, status=400)
        cache.delete(key)
        return Response(payload)


# -----------------------------------------------------------------------------
# OAuth user resolution
# -----------------------------------------------------------------------------


def resolve_oauth_user(profile: dict) -> User:
    provider = profile["provider"]
    provider_id = profile["provider_id"]
    id_field = "google_id" if provider == "google" else "github_id"
    user = User.objects.filter(**{id_field: provider_id}).first()
    if user:
        return user
    user = User.objects.filter(email=profile["email"]).first()
    if user:
        setattr(user, id_field, provider_id)
        if profile.get("avatar_url") and not user.avatar_url:
            user.avatar_url = profile["avatar_url"]
        if profile.get("full_name") and not user.full_name:
            user.full_name = profile["full_name"]
        user.save()
        return user
    user = User(
        email=profile["email"],
        full_name=profile.get("full_name", ""),
        avatar_url=profile.get("avatar_url", ""),
        **{id_field: provider_id},
    )
    user.set_unusable_password()
    user.save()
    mark_email_verified(user, profile["email"])
    return user
