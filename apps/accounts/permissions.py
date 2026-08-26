from rest_framework import permissions

from .email_verification import email_verified


class IsEmailVerified(permissions.BasePermission):
    message = "Verify your email address before using the app."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return True
        return email_verified(user)
