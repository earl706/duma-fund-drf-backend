"""Recurring purchase lookup, insights, preferences, and notifications."""

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import (
    PROFILE_ROLE_OWNER,
    PurchaseExclusion,
    PurchaseRegularMark,
)
from .purchase_match import (
    build_insights,
    build_notifications,
    exclusion_pair,
    family_key,
    lookup_purchases,
    normalize_title,
)
from .scope import ProfileScopedAPIView, require_role


class PurchaseLookupView(ProfileScopedAPIView):
    """GET ?q=&limit= — fuzzy purchase history for as-you-type hints."""

    def get(self, request):
        q = request.query_params.get("q", "")
        try:
            limit = int(request.query_params.get("limit", 5))
        except (TypeError, ValueError):
            limit = 5
        limit = max(1, min(limit, 15))
        matches = lookup_purchases(self.get_profile(), q, limit=limit)
        return Response({"matches": matches})


class PurchaseInsightsView(ProfileScopedAPIView):
    """GET — regular / lapsed / due-soon / recently seen aggregates."""

    def get(self, request):
        return Response(build_insights(self.get_profile()))


class PurchaseNotificationsView(ProfileScopedAPIView):
    """GET — informational in-app notifications derived from insights."""

    def get(self, request):
        return Response({"notifications": build_notifications(self.get_profile())})


class PurchaseMarkRegularView(ProfileScopedAPIView):
    """POST {title} to mark regular; DELETE {title} to unmark. Owner only."""

    def post(self, request):
        require_role(self.get_membership(), PROFILE_ROLE_OWNER)
        title = (request.data.get("title") or "").strip()
        if not title:
            raise ValidationError({"title": "Title is required."})
        fam = family_key(title)
        if not fam:
            raise ValidationError({"title": "Could not derive a product key."})
        mark, _created = PurchaseRegularMark.objects.update_or_create(
            owner=request.user,
            profile=self.get_profile(),
            family_key=fam,
            defaults={"display_title": title},
        )
        return Response(
            {
                "family_key": mark.family_key,
                "display_title": mark.display_title,
            },
            status=status.HTTP_201_CREATED,
        )

    def delete(self, request):
        require_role(self.get_membership(), PROFILE_ROLE_OWNER)
        title = (
            request.data.get("title") or request.query_params.get("title") or ""
        ).strip()
        if not title:
            raise ValidationError({"title": "Title is required."})
        fam = family_key(title)
        deleted, _ = PurchaseRegularMark.objects.filter(
            profile=self.get_profile(), family_key=fam
        ).delete()
        return Response({"deleted": deleted > 0}, status=status.HTTP_200_OK)


class PurchaseExcludeView(ProfileScopedAPIView):
    """POST {query_title, matched_title} — mark as not the same product. Owner only."""

    def post(self, request):
        require_role(self.get_membership(), PROFILE_ROLE_OWNER)
        query_title = (request.data.get("query_title") or "").strip()
        matched_title = (request.data.get("matched_title") or "").strip()
        if not query_title or not matched_title:
            raise ValidationError(
                {"detail": "query_title and matched_title are required."}
            )
        a = normalize_title(query_title)
        b = normalize_title(matched_title)
        if not a or not b:
            raise ValidationError({"detail": "Titles could not be normalized."})
        if a == b:
            a = family_key(query_title)
            b = family_key(matched_title)
        key_a, key_b = exclusion_pair(a, b)
        if key_a == key_b:
            raise ValidationError({"detail": "Titles resolve to the same key."})
        exclusion, created = PurchaseExclusion.objects.get_or_create(
            owner=request.user,
            profile=self.get_profile(),
            key_a=key_a,
            key_b=key_b,
        )
        return Response(
            {"key_a": exclusion.key_a, "key_b": exclusion.key_b, "created": created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
