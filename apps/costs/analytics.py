"""Spend / list activity time series for the dashboard."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDay, TruncMonth, TruncWeek
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsEmailVerified

from .models import CostItem, CostList


GRAIN_TRUNC = {
    "day": TruncDay,
    "week": TruncWeek,
    "month": TruncMonth,
}

ITEM_LINE = ExpressionWrapper(
    F("cost") * F("quantity"),
    output_field=DecimalField(max_digits=14, decimal_places=2),
)

LIST_LINE = ExpressionWrapper(
    F("items__cost") * F("items__quantity"),
    output_field=DecimalField(max_digits=14, decimal_places=2),
)


def _parse_date(value, fallback):
    if not value:
        return fallback
    try:
        return timezone.datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return fallback


def _as_date(value):
    if value is None:
        return None
    if hasattr(value, "date") and callable(value.date):
        return value.date()
    return value


def _period_start(d, grain):
    if grain == "week":
        return d - timedelta(days=d.weekday())
    if grain == "month":
        return d.replace(day=1)
    return d


def _next_period(d, grain):
    if grain == "week":
        return d + timedelta(days=7)
    if grain == "month":
        if d.month == 12:
            return d.replace(year=d.year + 1, month=1, day=1)
        return d.replace(month=d.month + 1, day=1)
    return d + timedelta(days=1)


def _iter_periods(start, end, grain):
    cursor = _period_start(start, grain)
    last = _period_start(end, grain)
    while cursor <= last:
        yield cursor
        cursor = _next_period(cursor, grain)


# -----------------------------------------------------------------------------
# Analytics API
# -----------------------------------------------------------------------------
class CostAnalyticsView(APIView):
    """
    GET /api/costs/analytics/?grain=day|week|month&include_archived=0|1&start=&end=

    Zero-filled points for the window (default: last 30 days inclusive):
      period, item_spend, list_count, list_spend
    """

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        grain = request.query_params.get("grain", "day")
        if grain not in GRAIN_TRUNC:
            grain = "day"

        include_archived = request.query_params.get("include_archived", "0") in (
            "1",
            "true",
            "True",
            "yes",
        )

        today = timezone.localdate()
        default_start = today - timedelta(days=29)
        start = _parse_date(request.query_params.get("start"), default_start)
        end = _parse_date(request.query_params.get("end"), today)
        if start > end:
            start, end = end, start

        trunc = GRAIN_TRUNC[grain]
        owner = request.user

        items = CostItem.objects.filter(
            owner=owner, date_effective__gte=start, date_effective__lte=end
        )
        lists = CostList.objects.filter(
            owner=owner, date_effective__gte=start, date_effective__lte=end
        )
        if not include_archived:
            items = items.filter(status="active")
            lists = lists.filter(status="active")

        item_rows = (
            items.annotate(period=trunc("date_effective"))
            .values("period")
            .annotate(
                item_spend=Coalesce(
                    Sum(ITEM_LINE),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            )
        )
        item_map = {}
        for row in item_rows:
            key = _period_start(_as_date(row["period"]), grain)
            if key is not None:
                item_map[key] = row["item_spend"]

        if include_archived:
            list_spend_sum = Sum(LIST_LINE)
        else:
            list_spend_sum = Sum(LIST_LINE, filter=Q(items__status="active"))
        list_qs = lists.annotate(
            total_cost=Coalesce(
                list_spend_sum,
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )
        list_map = {}
        for cost_list in list_qs:
            period = _period_start(cost_list.date_effective, grain)
            bucket = list_map.setdefault(
                period, {"list_count": 0, "list_spend": Decimal("0.00")}
            )
            bucket["list_count"] += 1
            bucket["list_spend"] += cost_list.total_cost or Decimal("0.00")

        points = []
        for period in _iter_periods(start, end, grain):
            lists_bucket = list_map.get(period, {})
            spend = item_map.get(period, Decimal("0.00"))
            points.append(
                {
                    "period": period.isoformat(),
                    "item_spend": str(spend),
                    "list_count": lists_bucket.get("list_count", 0),
                    "list_spend": str(lists_bucket.get("list_spend", Decimal("0.00"))),
                }
            )

        return Response(
            {
                "grain": grain,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "include_archived": include_archived,
                "points": points,
            }
        )
