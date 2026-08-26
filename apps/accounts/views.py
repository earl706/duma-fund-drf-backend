from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .auth_extra import (
    create_mfa_token,
    issue_tokens,
    parse_remember,
    stash_mfa_remember,
)
from .email_verification import email_verified, send_verification_email
from .models import get_user_security
from .serializers import (
    EmailTokenObtainPairSerializer,
    RegisterSerializer,
    UserSerializer,
)

User = get_user_model()


# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------


class RegisterView(generics.CreateAPIView):

    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        send_verification_email(request, user, signup=True)
        return Response(
            {
                "detail": "Check your email to verify your account before signing in.",
                "email": user.email,
            },
            status=status.HTTP_201_CREATED,
        )


# -----------------------------------------------------------------------------
# Login (email + optional MFA)
# -----------------------------------------------------------------------------


class EmailTokenObtainPairView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.user
        if not email_verified(user):
            # Always return the verification response, even if mail delivery fails.
            send_verification_email(request, user, signup=False)
            return Response(
                {
                    "detail": "Verify your email before signing in.",
                    "email_verification_required": True,
                    "email": user.email,
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        remember = parse_remember(request.data.get("remember"), default=True)
        security = get_user_security(user)
        if security.mfa_enabled:
            mfa_token = create_mfa_token(user.id)
            stash_mfa_remember(mfa_token, remember)
            return Response({"mfa_required": True, "mfa_token": mfa_token})
        return Response(issue_tokens(user, remember=remember))


# -----------------------------------------------------------------------------
# Current user (me)
# -----------------------------------------------------------------------------


class MeView(APIView):

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
