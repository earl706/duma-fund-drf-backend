import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings

# -----------------------------------------------------------------------------
# Supported providers
# -----------------------------------------------------------------------------
PROVIDERS = {"google", "github"}


# -----------------------------------------------------------------------------
# OAuth state (CSRF) cache helpers
# -----------------------------------------------------------------------------


def _state_key(state: str) -> str:
    return f"oauth-state:{state}"


def create_oauth_state(cache, provider: str) -> str:
    state = secrets.token_urlsafe(32)
    cache.set(_state_key(state), provider, timeout=600)
    return state


def pop_oauth_state(cache, state: str) -> str | None:
    key = _state_key(state)
    provider = cache.get(key)
    if provider:
        cache.delete(key)
    return provider


# -----------------------------------------------------------------------------
# Authorization URLs
# -----------------------------------------------------------------------------


def authorize_url(provider: str, state: str) -> str:
    if provider == "google":
        params = {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "redirect_uri": settings.OAUTH_REDIRECT_URI.format(provider="google"),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    if provider == "github":
        params = {
            "client_id": settings.GITHUB_OAUTH_CLIENT_ID,
            "redirect_uri": settings.OAUTH_REDIRECT_URI.format(provider="github"),
            "scope": "user:email",
            "state": state,
        }
        return f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    raise ValueError(f"Unknown provider: {provider}")


# -----------------------------------------------------------------------------
# Code-for-profile exchange
# -----------------------------------------------------------------------------


def exchange_code(provider: str, code: str) -> dict:
    if provider == "google":
        return _exchange_google(code)
    if provider == "github":
        return _exchange_github(code)
    raise ValueError(f"Unknown provider: {provider}")


# -----------------------------------------------------------------------------
# Provider-specific token & profile fetching
# -----------------------------------------------------------------------------


def _exchange_google(code: str) -> dict:
    redirect_uri = settings.OAUTH_REDIRECT_URI.format(provider="google")
    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    token_resp.raise_for_status()
    access = token_resp.json()["access_token"]
    profile = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access}"},
        timeout=15,
    )
    profile.raise_for_status()
    data = profile.json()
    return {
        "provider": "google",
        "provider_id": data["sub"],
        "email": data.get("email", "").lower(),
        "full_name": data.get("name", ""),
        "avatar_url": data.get("picture", ""),
    }


def _exchange_github(code: str) -> dict:
    redirect_uri = settings.OAUTH_REDIRECT_URI.format(provider="github")
    token_resp = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": settings.GITHUB_OAUTH_CLIENT_ID,
            "client_secret": settings.GITHUB_OAUTH_CLIENT_SECRET,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=15,
    )
    token_resp.raise_for_status()
    access = token_resp.json()["access_token"]
    user_resp = requests.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {access}", "Accept": "application/json"},
        timeout=15,
    )
    user_resp.raise_for_status()
    user = user_resp.json()
    email = user.get("email")
    if not email:
        emails_resp = requests.get(
            "https://api.github.com/user/emails",
            headers={"Authorization": f"Bearer {access}", "Accept": "application/json"},
            timeout=15,
        )
        emails_resp.raise_for_status()
        primary = next((e for e in emails_resp.json() if e.get("primary")), None)
        email = primary["email"] if primary else ""
    return {
        "provider": "github",
        "provider_id": str(user["id"]),
        "email": email.lower(),
        "full_name": user.get("name") or user.get("login", ""),
        "avatar_url": user.get("avatar_url", ""),
    }
