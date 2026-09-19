"""Budget profile CRUD, members, and accept-to-join invites."""

from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.db import transaction as db_transaction
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.accounts.permissions import IsEmailVerified

from .models import (
    INVITE_STATUS_ACCEPTED,
    INVITE_STATUS_DECLINED,
    INVITE_STATUS_PENDING,
    INVITE_STATUS_REVOKED,
    PROFILE_ROLE_EDITOR,
    PROFILE_ROLE_OWNER,
    PROFILE_ROLE_VIEWER,
    BudgetProfile,
    BudgetProfileInvite,
    BudgetProfileMembership,
)
from .seeds import ensure_finance_ready, seed_categories_for_profile

User = get_user_model()

NO_ACCOUNT_MSG = "No DumaFund account for that email."
MEMBER_ROLES = (PROFILE_ROLE_EDITOR, PROFILE_ROLE_VIEWER)


class InviteCreateThrottle(UserRateThrottle):
    rate = "30/hour"


def _user_payload(user):
    if user is None:
        return None
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name or "",
    }


def serialize_profile(profile, *, role, include_members=False):
    data = {
        "id": profile.id,
        "uuid": str(profile.uuid),
        "name": profile.name,
        "starting_balance": str(profile.starting_balance),
        "role": role,
        "is_owner": role == PROFILE_ROLE_OWNER,
        "owner": _user_payload(profile.owner),
        "member_count": profile.memberships.count(),
        "created_at": profile.created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": profile.updated_at.isoformat().replace("+00:00", "Z"),
    }
    if include_members:
        data["members"] = [
            {
                "user": _user_payload(m.user),
                "role": m.role,
                "created_at": m.created_at.isoformat().replace("+00:00", "Z"),
            }
            for m in profile.memberships.select_related("user").order_by("id")
        ]
        data["pending_invites"] = [
            serialize_invite(inv)
            for inv in profile.invites.filter(status=INVITE_STATUS_PENDING)
            .select_related("invited_user", "invited_by", "profile", "profile__owner")
            .order_by("-created_at")
        ]
    return data


def serialize_invite(invite):
    return {
        "id": invite.id,
        "uuid": str(invite.uuid),
        "role": invite.role,
        "status": invite.status,
        "profile": {
            "id": invite.profile_id,
            "name": invite.profile.name,
            "owner": _user_payload(invite.profile.owner),
        },
        "invited_user": _user_payload(invite.invited_user),
        "invited_by": _user_payload(invite.invited_by),
        "created_at": invite.created_at.isoformat().replace("+00:00", "Z"),
    }


def _owned_or_404(user, profile_id):
    try:
        return BudgetProfile.objects.select_related("owner").get(
            pk=profile_id, owner=user
        )
    except BudgetProfile.DoesNotExist as exc:
        raise NotFound() from exc


def _member_profile_or_404(user, profile_id):
    membership = (
        BudgetProfileMembership.objects.select_related("profile", "profile__owner")
        .filter(user=user, profile_id=profile_id)
        .first()
    )
    if membership is None:
        raise NotFound()
    return membership


class ProfileListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        ensure_finance_ready(request.user)
        memberships = (
            BudgetProfileMembership.objects.filter(user=request.user)
            .select_related("profile", "profile__owner")
            .order_by("profile__name", "profile_id")
        )
        pending_count = BudgetProfileInvite.objects.filter(
            invited_user=request.user, status=INVITE_STATUS_PENDING
        ).count()
        results = [
            serialize_profile(m.profile, role=m.role) for m in memberships
        ]
        return Response(
            {"results": results, "pending_invite_count": pending_count}
        )

    def post(self, request):
        ensure_finance_ready(request.user)
        name = (request.data.get("name") or "").strip()
        if not name:
            raise ValidationError({"name": "Name is required."})
        raw_start = request.data.get("starting_balance", "0.00")
        try:
            starting = Decimal(str(raw_start or "0.00"))
        except (InvalidOperation, TypeError) as exc:
            raise ValidationError({"starting_balance": "Invalid amount."}) from exc
        with db_transaction.atomic():
            profile = BudgetProfile.objects.create(
                owner=request.user,
                name=name[:100],
                starting_balance=starting,
            )
            BudgetProfileMembership.objects.create(
                profile=profile, user=request.user, role=PROFILE_ROLE_OWNER
            )
            seed_categories_for_profile(profile)
        return Response(
            serialize_profile(profile, role=PROFILE_ROLE_OWNER),
            status=status.HTTP_201_CREATED,
        )


class ProfileDetailView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request, pk):
        membership = _member_profile_or_404(request.user, pk)
        include = membership.role == PROFILE_ROLE_OWNER
        return Response(
            serialize_profile(
                membership.profile, role=membership.role, include_members=include
            )
        )

    def patch(self, request, pk):
        profile = _owned_or_404(request.user, pk)
        name = request.data.get("name")
        if name is not None:
            name = str(name).strip()
            if not name:
                raise ValidationError({"name": "Name is required."})
            profile.name = name[:100]
        if "starting_balance" in request.data:
            try:
                profile.starting_balance = Decimal(
                    str(request.data.get("starting_balance") or "0.00")
                )
            except (InvalidOperation, TypeError) as exc:
                raise ValidationError({"starting_balance": "Invalid amount."}) from exc
        profile.save()
        return Response(serialize_profile(profile, role=PROFILE_ROLE_OWNER))

    def delete(self, request, pk):
        profile = _owned_or_404(request.user, pk)
        owned_count = BudgetProfile.objects.filter(owner=request.user).count()
        if owned_count <= 1:
            raise ValidationError(
                {"detail": "You cannot delete your last owned profile."}
            )
        profile.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProfileMemberListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]
    throttle_classes = [InviteCreateThrottle]

    def get_throttles(self):
        if self.request.method == "POST":
            return super().get_throttles()
        return []

    def get(self, request, pk):
        membership = _member_profile_or_404(request.user, pk)
        profile = membership.profile
        members = [
            {
                "user": _user_payload(m.user),
                "role": m.role,
                "created_at": m.created_at.isoformat().replace("+00:00", "Z"),
            }
            for m in profile.memberships.select_related("user").order_by("id")
        ]
        pending = []
        if membership.role == PROFILE_ROLE_OWNER:
            pending = [
                serialize_invite(inv)
                for inv in profile.invites.filter(status=INVITE_STATUS_PENDING)
                .select_related(
                    "invited_user", "invited_by", "profile", "profile__owner"
                )
                .order_by("-created_at")
            ]
        return Response({"results": members, "pending_invites": pending})

    def post(self, request, pk):
        profile = _owned_or_404(request.user, pk)
        email = (request.data.get("email") or "").strip().lower()
        role = (request.data.get("role") or "").strip()
        if not email:
            raise ValidationError({"email": "Email is required."})
        if role not in MEMBER_ROLES:
            raise ValidationError({"role": "Role must be editor or viewer."})
        invitee = User.objects.filter(email__iexact=email).first()
        if invitee is None:
            raise ValidationError({"email": NO_ACCOUNT_MSG})
        if invitee.id == profile.owner_id or invitee.id == request.user.id:
            raise ValidationError({"email": "That user already owns this profile."})
        if BudgetProfileMembership.objects.filter(
            profile=profile, user=invitee
        ).exists():
            raise ValidationError({"email": "That user is already a member."})
        if BudgetProfileInvite.objects.filter(
            profile=profile,
            invited_user=invitee,
            status=INVITE_STATUS_PENDING,
        ).exists():
            raise ValidationError({"email": "An invite is already pending for that user."})
        invite = BudgetProfileInvite.objects.create(
            profile=profile,
            invited_user=invitee,
            invited_by=request.user,
            role=role,
            status=INVITE_STATUS_PENDING,
        )
        return Response(serialize_invite(invite), status=status.HTTP_201_CREATED)


class ProfileMemberDetailView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def patch(self, request, pk, user_id):
        profile = _owned_or_404(request.user, pk)
        if user_id == profile.owner_id:
            raise ValidationError({"detail": "The owner role cannot be changed."})
        role = (request.data.get("role") or "").strip()
        if role not in MEMBER_ROLES:
            raise ValidationError({"role": "Role must be editor or viewer."})
        membership = BudgetProfileMembership.objects.filter(
            profile=profile, user_id=user_id
        ).first()
        if membership is None:
            raise NotFound()
        if membership.role == PROFILE_ROLE_OWNER:
            raise ValidationError({"detail": "The owner role cannot be changed."})
        membership.role = role
        membership.save(update_fields=["role", "updated_at"])
        return Response(
            {
                "user": _user_payload(membership.user),
                "role": membership.role,
            }
        )

    def delete(self, request, pk, user_id):
        membership = _member_profile_or_404(request.user, pk)
        profile = membership.profile
        target = BudgetProfileMembership.objects.filter(
            profile=profile, user_id=user_id
        ).first()
        if target is None:
            raise NotFound()
        is_self = user_id == request.user.id
        if is_self:
            if target.role == PROFILE_ROLE_OWNER:
                raise ValidationError({"detail": "The owner cannot leave this profile."})
            target.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        if membership.role != PROFILE_ROLE_OWNER:
            raise PermissionDenied("Only the owner can remove other members.")
        if target.role == PROFILE_ROLE_OWNER:
            raise ValidationError({"detail": "The owner cannot be removed."})
        target.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProfileInviteRevokeView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def delete(self, request, pk, invite_id):
        profile = _owned_or_404(request.user, pk)
        invite = BudgetProfileInvite.objects.filter(
            pk=invite_id, profile=profile, status=INVITE_STATUS_PENDING
        ).first()
        if invite is None:
            raise NotFound()
        invite.status = INVITE_STATUS_REVOKED
        invite.save(update_fields=["status", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class MyInviteListView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        invites = (
            BudgetProfileInvite.objects.filter(
                invited_user=request.user, status=INVITE_STATUS_PENDING
            )
            .select_related("profile", "profile__owner", "invited_by", "invited_user")
            .order_by("-created_at")
        )
        return Response({"results": [serialize_invite(inv) for inv in invites]})


class MyInviteAcceptView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def post(self, request, pk):
        invite = (
            BudgetProfileInvite.objects.select_related("profile", "profile__owner")
            .filter(
                pk=pk,
                invited_user=request.user,
                status=INVITE_STATUS_PENDING,
            )
            .first()
        )
        if invite is None:
            raise NotFound()
        with db_transaction.atomic():
            BudgetProfileMembership.objects.get_or_create(
                profile=invite.profile,
                user=request.user,
                defaults={"role": invite.role},
            )
            invite.status = INVITE_STATUS_ACCEPTED
            invite.save(update_fields=["status", "updated_at"])
        membership = BudgetProfileMembership.objects.get(
            profile=invite.profile, user=request.user
        )
        return Response(
            serialize_profile(invite.profile, role=membership.role),
            status=status.HTTP_200_OK,
        )


class MyInviteDeclineView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def post(self, request, pk):
        invite = BudgetProfileInvite.objects.filter(
            pk=pk,
            invited_user=request.user,
            status=INVITE_STATUS_PENDING,
        ).first()
        if invite is None:
            raise NotFound()
        invite.status = INVITE_STATUS_DECLINED
        invite.save(update_fields=["status", "updated_at"])
        return Response({"id": invite.id, "status": invite.status})
