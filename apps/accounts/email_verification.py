import logging

from allauth.account.models import (
    EmailAddress,
    EmailConfirmation,
    EmailConfirmationHMAC,
)
from allauth.account.utils import send_email_confirmation
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()


def email_verified(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return EmailAddress.objects.filter(user=user, verified=True).exists()


def ensure_primary_email_address(user, *, verified: bool) -> EmailAddress:
    address, _ = EmailAddress.objects.get_or_create(
        user=user,
        email=user.email.lower(),
        defaults={"primary": True, "verified": verified},
    )
    updates = []
    if not address.primary:
        address.primary = True
        updates.append("primary")
    if verified and not address.verified:
        address.verified = True
        updates.append("verified")
    if updates:
        address.save(update_fields=updates)
    return address


def mark_email_verified(user, email: str | None = None) -> None:
    email = (email or user.email).lower()
    ensure_primary_email_address(user, verified=True)
    EmailAddress.objects.filter(user=user).exclude(email__iexact=email).update(
        primary=False
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email__iexact=email,
        defaults={"email": email, "verified": True, "primary": True},
    )
    if user.email.lower() != email:
        user.email = email
        user.save(update_fields=["email"])


def send_verification_email(request, user, *, signup: bool = False) -> bool:
    """Send confirmation email. Returns True if queued/sent, False on failure."""
    ensure_primary_email_address(user, verified=False)
    try:
        send_email_confirmation(request, user, signup=signup, email=user.email)
        return True
    except Exception:
        logger.exception(
            "Failed to send verification email to %s (signup=%s)",
            user.email,
            signup,
        )
        return False


def confirm_email_key(key: str, request) -> tuple[User | None, str | None]:
    confirmation = EmailConfirmationHMAC.from_key(key)
    if confirmation is None:
        confirmation = (
            EmailConfirmation.objects.filter(key=key)
            .select_related("email_address__user")
            .first()
        )
    if confirmation is None:
        return None, "invalid_or_expired"
    email_address = confirmation.email_address
    user = email_address.user
    confirmation.confirm(request=request)
    email_address.refresh_from_db()
    if not email_address.verified:
        return None, "invalid_or_expired"
    email_address.set_as_primary(conditional=False)
    if user.email.lower() != email_address.email.lower():
        user.email = email_address.email
        user.save(update_fields=["email"])
    return user, None
