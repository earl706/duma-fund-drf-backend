import hashlib
import secrets

import pyotp
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
MFA_TOKEN_SALT = "app-mfa-pending"
MFA_TOKEN_MAX_AGE = 300  # 5 minutes
RECOVERY_CODE_COUNT = 8


# -----------------------------------------------------------------------------
# Pending-MFA signed tokens
# -----------------------------------------------------------------------------


def get_signer():
    return TimestampSigner(salt=MFA_TOKEN_SALT)


def create_mfa_token(user_id: int) -> str:
    return get_signer().sign(str(user_id))


def verify_mfa_token(token: str) -> int | None:
    try:
        return int(get_signer().unsign(token, max_age=MFA_TOKEN_MAX_AGE))
    except (BadSignature, SignatureExpired, ValueError):
        return None


# -----------------------------------------------------------------------------
# TOTP secrets & verification
# -----------------------------------------------------------------------------


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(email: str, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="DumaFund")


def verify_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


# -----------------------------------------------------------------------------
# Recovery codes
# -----------------------------------------------------------------------------


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def generate_recovery_codes() -> list[str]:
    return [secrets.token_hex(4) for _ in range(RECOVERY_CODE_COUNT)]


def hash_recovery_codes(codes: list[str]) -> list[str]:
    return [hash_recovery_code(c) for c in codes]


def verify_recovery_code(stored_hashes: list[str], code: str) -> tuple[bool, list[str]]:
    digest = hash_recovery_code(code.strip().lower())
    if digest not in stored_hashes:
        return False, stored_hashes
    return True, [h for h in stored_hashes if h != digest]
