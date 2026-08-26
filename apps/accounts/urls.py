from django.urls import path

from .auth_extra import (
    MfaDisableView,
    MfaDismissPromptView,
    MfaSetupConfirmView,
    MfaSetupView,
    MfaVerifyView,
    OAuthCallbackView,
    OAuthExchangeView,
    OAuthStartView,
    RememberAwareTokenRefreshView,
)
from .email_views import ChangeEmailView, ResendVerificationView, VerifyEmailView
from .views import EmailTokenObtainPairView, MeView, RegisterView

# -----------------------------------------------------------------------------
# Auth & session routes
# -----------------------------------------------------------------------------
urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", EmailTokenObtainPairView.as_view(), name="login"),
    path("refresh/", RememberAwareTokenRefreshView.as_view(), name="token-refresh"),
    path("email/verify/", VerifyEmailView.as_view(), name="email-verify"),
    path("email/resend/", ResendVerificationView.as_view(), name="email-resend"),
    path("email/change/", ChangeEmailView.as_view(), name="email-change"),
]

# -----------------------------------------------------------------------------
# User routes
# -----------------------------------------------------------------------------
urlpatterns += [
    path("me/", MeView.as_view(), name="me"),
]

# -----------------------------------------------------------------------------
# MFA routes
# -----------------------------------------------------------------------------
urlpatterns += [
    path("mfa/verify/", MfaVerifyView.as_view(), name="mfa-verify"),
    path("mfa/setup/", MfaSetupView.as_view(), name="mfa-setup"),
    path("mfa/setup/confirm/", MfaSetupConfirmView.as_view(), name="mfa-setup-confirm"),
    path("mfa/disable/", MfaDisableView.as_view(), name="mfa-disable"),
    path(
        "mfa/dismiss-prompt/", MfaDismissPromptView.as_view(), name="mfa-dismiss-prompt"
    ),
]

# -----------------------------------------------------------------------------
# OAuth routes
# -----------------------------------------------------------------------------
urlpatterns += [
    path("oauth/<str:provider>/start/", OAuthStartView.as_view(), name="oauth-start"),
    path(
        "oauth/<str:provider>/callback/",
        OAuthCallbackView.as_view(),
        name="oauth-callback",
    ),
    path("oauth/exchange/", OAuthExchangeView.as_view(), name="oauth-exchange"),
]
