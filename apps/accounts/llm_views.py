"""Per-user Receipt AI provider / API key settings."""

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .llm_crypto import decrypt_secret, encrypt_secret, key_last4
from .models import UserLlmSettings, get_user_llm_settings

LLM_PROVIDERS = {UserLlmSettings.PROVIDER_OPENAI, UserLlmSettings.PROVIDER_GEMINI}
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"


def serialize_llm_settings(row: UserLlmSettings) -> dict:
    return {
        "provider": row.provider,
        "openai_model": row.openai_model or DEFAULT_OPENAI_MODEL,
        "gemini_model": row.gemini_model or DEFAULT_GEMINI_MODEL,
        "openai_key_set": bool(row.openai_api_key_encrypted),
        "openai_key_hint": row.openai_api_key_last4 or "",
        "gemini_key_set": bool(row.gemini_api_key_encrypted),
        "gemini_key_hint": row.gemini_api_key_last4 or "",
    }


def looks_like_masked_key(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return True
    if text.startswith("•") or text.startswith("*"):
        return True
    return False


def active_llm_override_for_user(user) -> dict | None:
    """Decrypt stored credentials for receipt scan (no plaintext in API responses)."""
    row = get_user_llm_settings(user)

    provider = (row.provider or UserLlmSettings.PROVIDER_OPENAI).strip().lower()
    if provider not in LLM_PROVIDERS:
        provider = UserLlmSettings.PROVIDER_OPENAI

    if provider == UserLlmSettings.PROVIDER_GEMINI:
        api_key = decrypt_secret(row.gemini_api_key_encrypted)
        model = (row.gemini_model or DEFAULT_GEMINI_MODEL).strip()
    else:
        api_key = decrypt_secret(row.openai_api_key_encrypted)
        model = (row.openai_model or DEFAULT_OPENAI_MODEL).strip()

    if not api_key:
        # Still pass provider/model so env key uses the user's chosen model.
        return {"provider": provider, "api_key": None, "model": model or None}

    return {"provider": provider, "api_key": api_key, "model": model or None}


class LlmSettingsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        row = get_user_llm_settings(request.user)
        return Response(serialize_llm_settings(row))

    def patch(self, request):
        row = get_user_llm_settings(request.user)
        data = request.data if isinstance(request.data, dict) else {}

        provider = data.get("provider")
        if provider is not None:
            provider = str(provider).strip().lower()
            if provider not in LLM_PROVIDERS:
                return Response(
                    {"provider": "Must be openai or gemini."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            row.provider = provider

        if "openai_model" in data:
            model = str(data.get("openai_model") or "").strip()
            row.openai_model = model or DEFAULT_OPENAI_MODEL

        if "gemini_model" in data:
            model = str(data.get("gemini_model") or "").strip()
            row.gemini_model = model or DEFAULT_GEMINI_MODEL

        if "openai_api_key" in data:
            raw = str(data.get("openai_api_key") or "")
            if not looks_like_masked_key(raw):
                key = raw.strip()
                row.openai_api_key_encrypted = encrypt_secret(key)
                row.openai_api_key_last4 = key_last4(key)

        if "gemini_api_key" in data:
            raw = str(data.get("gemini_api_key") or "")
            if not looks_like_masked_key(raw):
                key = raw.strip()
                row.gemini_api_key_encrypted = encrypt_secret(key)
                row.gemini_api_key_last4 = key_last4(key)

        row.save()
        return Response(serialize_llm_settings(row))


class LlmKeysClearView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request):
        row = get_user_llm_settings(request.user)
        row.openai_api_key_encrypted = ""
        row.openai_api_key_last4 = ""
        row.gemini_api_key_encrypted = ""
        row.gemini_api_key_last4 = ""
        row.save(
            update_fields=[
                "openai_api_key_encrypted",
                "openai_api_key_last4",
                "gemini_api_key_encrypted",
                "gemini_api_key_last4",
            ]
        )
        return Response(serialize_llm_settings(row))
