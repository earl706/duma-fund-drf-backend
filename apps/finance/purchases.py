"""Recurring purchase lookup, insights, preferences, and notifications."""

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsEmailVerified

from .models import PurchaseExclusion, PurchaseRegularMark
from .purchase_match import (
    build_insights,
    build_notifications,
    exclusion_pair,
    family_key,
    lookup_purchases,
    normalize_title,
)
from .seeds import ensure_finance_ready


class PurchaseLookupView(APIView):
    """GET ?q=&limit= — fuzzy purchase history for as-you-type hints."""

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        ensure_finance_ready(request.user)
        q = request.query_params.get("q", "")
        try:
            limit = int(request.query_params.get("limit", 5))
        except (TypeError, ValueError):
            limit = 5
        limit = max(1, min(limit, 15))
        matches = lookup_purchases(request.user, q, limit=limit)
        return Response({"matches": matches})


class PurchaseInsightsView(APIView):
    """GET — regular / lapsed / due-soon / recently seen aggregates."""

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        ensure_finance_ready(request.user)
        return Response(build_insights(request.user))


class PurchaseNotificationsView(APIView):
    """GET — informational in-app notifications derived from insights."""

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        ensure_finance_ready(request.user)
        return Response({"notifications": build_notifications(request.user)})


class PurchaseMarkRegularView(APIView):
    """POST {title} to mark regular; DELETE {title} to unmark."""

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def post(self, request):
        ensure_finance_ready(request.user)
        title = (request.data.get("title") or "").strip()
        if not title:
            raise ValidationError({"title": "Title is required."})
        fam = family_key(title)
        if not fam:
            raise ValidationError({"title": "Could not derive a product key."})
        mark, _created = PurchaseRegularMark.objects.update_or_create(
            owner=request.user,
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
        ensure_finance_ready(request.user)
        title = (
            request.data.get("title") or request.query_params.get("title") or ""
        ).strip()
        if not title:
            raise ValidationError({"title": "Title is required."})
        fam = family_key(title)
        deleted, _ = PurchaseRegularMark.objects.filter(
            owner=request.user, family_key=fam
        ).delete()
        return Response({"deleted": deleted > 0}, status=status.HTTP_200_OK)


class PurchaseExcludeView(APIView):
    """POST {query_title, matched_title} — mark as not the same product."""

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def post(self, request):
        ensure_finance_ready(request.user)
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
            # Fall back to family keys when normalized titles collide oddly
            a = family_key(query_title)
            b = family_key(matched_title)
        key_a, key_b = exclusion_pair(a, b)
        if key_a == key_b:
            raise ValidationError({"detail": "Titles resolve to the same key."})
        exclusion, created = PurchaseExclusion.objects.get_or_create(
            owner=request.user,
            key_a=key_a,
            key_b=key_b,
        )
        return Response(
            {"key_a": exclusion.key_a, "key_b": exclusion.key_b, "created": created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
