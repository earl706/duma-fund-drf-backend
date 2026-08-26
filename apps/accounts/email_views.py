from django.contrib.auth import authenticate, get_user_model
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from allauth.account.utils import send_email_confirmation

from .email_verification import (
    confirm_email_key,
    email_verified,
    send_verification_email,
)
from .serializers import UserSerializer

User = get_user_model()


class ResendVerificationThrottle(AnonRateThrottle):
    rate = "5/hour"


class VerifyEmailView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        key = (request.data.get("key") or "").strip()
        if not key:
            return Response({"detail": "Confirmation key is required."}, status=400)
        user, error = confirm_email_key(key, request)
        if error:
            return Response(
                {"detail": "Invalid or expired confirmation link."}, status=400
            )
        return Response(
            {
                "detail": "Email verified. You can sign in now.",
                "user": UserSerializer(user).data,
            }
        )


class ResendVerificationView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ResendVerificationThrottle]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""
        if not email:
            return Response({"detail": "Email is required."}, status=400)

        user = User.objects.filter(email__iexact=email).first()
        if user and not email_verified(user):
            if password:
                authed = authenticate(request, email=email, password=password)
                if authed is None:
                    return Response(
                        {"detail": "Invalid email or password."}, status=400
                    )
                user = authed
            send_verification_email(request, user, signup=False)

        return Response(
            {
                "detail": "If an unverified account exists for that email, a confirmation message was sent."
            }
        )


class ChangeEmailView(APIView):
    def post(self, request):
        new_email = (request.data.get("email") or "").strip().lower()
        if not new_email:
            return Response({"detail": "New email is required."}, status=400)
        if new_email == request.user.email.lower():
            return Response(
                {"detail": "That is already your email address."}, status=400
            )
        if (
            User.objects.filter(email__iexact=new_email)
            .exclude(pk=request.user.pk)
            .exists()
        ):
            return Response({"detail": "That email is already in use."}, status=400)

        from allauth.account.models import EmailAddress

        EmailAddress.objects.filter(user=request.user, email__iexact=new_email).delete()
        EmailAddress.objects.create(
            user=request.user,
            email=new_email,
            primary=False,
            verified=False,
        )
        send_email_confirmation(request, request.user, signup=False, email=new_email)
        return Response(
            {
                "detail": "Confirmation sent to your new email address.",
                "pending_email": new_email,
            }
        )
