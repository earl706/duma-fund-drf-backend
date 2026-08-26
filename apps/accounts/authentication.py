from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

from .email_verification import email_verified


class JWTAuthenticationWithEmailVerified(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if user and not email_verified(user):
            raise InvalidToken("Email address is not verified.")
        return user
