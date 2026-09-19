"""Resolve the active budget profile from X-Budget-Profile-Id and enforce roles."""

from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from apps.accounts.permissions import IsEmailVerified

from .models import (
    PROFILE_ROLE_EDITOR,
    PROFILE_ROLE_OWNER,
    PROFILE_ROLE_VIEWER,
    BudgetProfileMembership,
)
from .seeds import ensure_profile_ready


PROFILE_HEADER = "X-Budget-Profile-Id"

LEDGER_WRITE_ROLES = (PROFILE_ROLE_OWNER, PROFILE_ROLE_EDITOR)
OWNER_ONLY_ROLES = (PROFILE_ROLE_OWNER,)
ANY_MEMBER_ROLES = (PROFILE_ROLE_OWNER, PROFILE_ROLE_EDITOR, PROFILE_ROLE_VIEWER)


def parse_profile_header(request):
    raw = request.headers.get(PROFILE_HEADER)
    if raw in (None, ""):
        raise ValidationError({"detail": f"{PROFILE_HEADER} header is required."})
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            {"detail": f"{PROFILE_HEADER} must be an integer."}
        ) from exc


def membership_for(user, profile_id):
    return (
        BudgetProfileMembership.objects.select_related("profile", "profile__owner")
        .filter(user=user, profile_id=profile_id)
        .first()
    )


def resolve_membership(request):
    cached = getattr(request, "_budget_membership", None)
    if cached is not None:
        return cached
    profile_id = parse_profile_header(request)
    membership = membership_for(request.user, profile_id)
    if membership is None:
        raise PermissionDenied("You do not have access to this profile.")
    ensure_profile_ready(membership.profile)
    request._budget_membership = membership
    request._budget_profile = membership.profile
    return membership


def resolve_profile(request):
    return resolve_membership(request).profile


def require_role(membership, *roles):
    if membership.role not in roles:
        raise PermissionDenied("You do not have permission to perform this action.")
    return membership


def user_profiles(user):
    return (
        BudgetProfileMembership.objects.filter(user=user)
        .select_related("profile", "profile__owner")
        .order_by("profile__name", "profile_id")
    )


class ProfileScopedAPIView(APIView):
    """Finance endpoint that requires an accepted membership via header."""

    permission_classes = [IsAuthenticated, IsEmailVerified]
    write_roles = LEDGER_WRITE_ROLES

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.user.is_authenticated:
            resolve_membership(request)

    def get_profile(self):
        return resolve_profile(self.request)

    def get_membership(self):
        return resolve_membership(self.request)

    def assert_write(self, *roles):
        require_role(self.get_membership(), *(roles or self.write_roles))


class ProfileScopedViewSet(ModelViewSet):
    """CRUD scoped to the active profile. Override write_roles per resource."""

    permission_classes = [IsAuthenticated, IsEmailVerified]
    write_roles = LEDGER_WRITE_ROLES

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.user.is_authenticated:
            resolve_membership(request)

    def get_profile(self):
        return resolve_profile(self.request)

    def get_membership(self):
        return resolve_membership(self.request)

    def get_queryset(self):
        return super().get_queryset().filter(profile=self.get_profile())

    def assert_write(self):
        require_role(self.get_membership(), *self.write_roles)

    def create(self, request, *args, **kwargs):
        self.assert_write()
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        self.assert_write()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self.assert_write()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self.assert_write()
        return super().destroy(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, profile=self.get_profile())
