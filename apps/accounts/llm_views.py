"""Per-user Receipt AI provider / API key settings."""

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .llm_crypto import decrypt_secret, encrypt_secret, key_last4
from .models import UserLlmSettings, get_user_llm_settings

LLM_PROVIDERS = {
    UserLlmSettings.PROVIDER_OPENAI,
    UserLlmSettings.PROVIDER_GEMINI,
    UserLlmSettings.PROVIDER_GROQ,
    UserLlmSettings.PROVIDER_OPENROUTER,
}
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_GROQ_MODEL = "qwen/qwen3.8-27b"
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3.5-super-vl:free"

_PROVIDER_FIELDS = {
    UserLlmSettings.PROVIDER_OPENAI: {
        "encrypted": "openai_api_key_encrypted",
        "last4": "openai_api_key_last4",
        "model": "openai_model",
        "default_model": DEFAULT_OPENAI_MODEL,
    },
    UserLlmSettings.PROVIDER_GEMINI: {
        "encrypted": "gemini_api_key_encrypted",
        "last4": "gemini_api_key_last4",
        "model": "gemini_model",
        "default_model": DEFAULT_GEMINI_MODEL,
    },
    UserLlmSettings.PROVIDER_GROQ: {
        "encrypted": "groq_api_key_encrypted",
        "last4": "groq_api_key_last4",
        "model": "groq_model",
        "default_model": DEFAULT_GROQ_MODEL,
    },
    UserLlmSettings.PROVIDER_OPENROUTER: {
        "encrypted": "openrouter_api_key_encrypted",
        "last4": "openrouter_api_key_last4",
        "model": "openrouter_model",
        "default_model": DEFAULT_OPENROUTER_MODEL,
    },
}


def serialize_llm_settings(row: UserLlmSettings) -> dict:
    return {
        "provider": row.provider,
        "openai_model": row.openai_model or DEFAULT_OPENAI_MODEL,
        "gemini_model": row.gemini_model or DEFAULT_GEMINI_MODEL,
        "groq_model": row.groq_model or DEFAULT_GROQ_MODEL,
        "openrouter_model": row.openrouter_model or DEFAULT_OPENROUTER_MODEL,
        "openai_key_set": bool(row.openai_api_key_encrypted),
        "openai_key_hint": row.openai_api_key_last4 or "",
        "gemini_key_set": bool(row.gemini_api_key_encrypted),
        "gemini_key_hint": row.gemini_api_key_last4 or "",
        "groq_key_set": bool(row.groq_api_key_encrypted),
        "groq_key_hint": row.groq_api_key_last4 or "",
        "openrouter_key_set": bool(row.openrouter_api_key_encrypted),
        "openrouter_key_hint": row.openrouter_api_key_last4 or "",
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

    fields = _PROVIDER_FIELDS[provider]
    api_key = decrypt_secret(getattr(row, fields["encrypted"]))
    model = (getattr(row, fields["model"]) or fields["default_model"]).strip()

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
                    {
                        "provider": (
                            "Must be openai, gemini, groq, or openrouter."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            row.provider = provider

        model_defaults = {
            "openai_model": DEFAULT_OPENAI_MODEL,
            "gemini_model": DEFAULT_GEMINI_MODEL,
            "groq_model": DEFAULT_GROQ_MODEL,
            "openrouter_model": DEFAULT_OPENROUTER_MODEL,
        }
        for field, default in model_defaults.items():
            if field in data:
                model = str(data.get(field) or "").strip()
                setattr(row, field, model or default)

        key_fields = (
            ("openai_api_key", "openai_api_key_encrypted", "openai_api_key_last4"),
            ("gemini_api_key", "gemini_api_key_encrypted", "gemini_api_key_last4"),
            ("groq_api_key", "groq_api_key_encrypted", "groq_api_key_last4"),
            (
                "openrouter_api_key",
                "openrouter_api_key_encrypted",
                "openrouter_api_key_last4",
            ),
        )
        for raw_key, encrypted_attr, last4_attr in key_fields:
            if raw_key not in data:
                continue
            raw = str(data.get(raw_key) or "")
            if looks_like_masked_key(raw):
                continue
            key = raw.strip()
            setattr(row, encrypted_attr, encrypt_secret(key))
            setattr(row, last4_attr, key_last4(key))

        row.save()
        return Response(serialize_llm_settings(row))


_PROVIDER_KEY_FIELDS = {
    UserLlmSettings.PROVIDER_OPENAI: (
        "openai_api_key_encrypted",
        "openai_api_key_last4",
    ),
    UserLlmSettings.PROVIDER_GEMINI: (
        "gemini_api_key_encrypted",
        "gemini_api_key_last4",
    ),
    UserLlmSettings.PROVIDER_GROQ: (
        "groq_api_key_encrypted",
        "groq_api_key_last4",
    ),
    UserLlmSettings.PROVIDER_OPENROUTER: (
        "openrouter_api_key_encrypted",
        "openrouter_api_key_last4",
    ),
}


class LlmKeysClearView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request):
        row = get_user_llm_settings(request.user)
        raw_provider = request.query_params.get("provider")
        if raw_provider is not None:
            provider = str(raw_provider).strip().lower()
            if provider not in LLM_PROVIDERS:
                return Response(
                    {
                        "provider": (
                            "Must be openai, gemini, groq, or openrouter."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            fields = list(_PROVIDER_KEY_FIELDS[provider])
        else:
            fields = [
                field
                for pair in _PROVIDER_KEY_FIELDS.values()
                for field in pair
            ]

        for field in fields:
            setattr(row, field, "")
        row.save(update_fields=fields)
        return Response(serialize_llm_settings(row))
