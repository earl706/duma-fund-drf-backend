"""Spend / transaction activity and period breakdown for the dashboard."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce, TruncDay, TruncMonth, TruncWeek
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsEmailVerified

from .balance import compute_balance
from .models import Category, Transaction, TransactionItem
from .seeds import ensure_finance_ready


GRAIN_TRUNC = {
    "day": TruncDay,
    "week": TruncWeek,
    "month": TruncMonth,
}

ITEM_LINE = ExpressionWrapper(
    F("cost") * F("quantity"),
    output_field=DecimalField(max_digits=14, decimal_places=2),
)

TOP_CATEGORY_SLICES = 5


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


def _current_period_bounds(grain, today=None):
    today = today or timezone.localdate()
    start = _period_start(today, grain)
    return start, today


def _dec_str(value):
    if value is None:
        value = Decimal("0.00")
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return str(value.quantize(Decimal("0.01")))


class FinanceAnalyticsView(APIView):
    """
    GET /api/finance/analytics/?grain=day|week|month&include_archived=0|1&start=&end=

    Zero-filled points: period, item_spend, txn_count, txn_spend (expense headers).
    item_spend and txn_spend both bucket by Transaction.date_effective.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        ensure_finance_ready(request.user)
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

        items = TransactionItem.objects.filter(
            owner=owner,
            transaction__date_effective__gte=start,
            transaction__date_effective__lte=end,
        )
        expenses = Transaction.objects.filter(
            owner=owner,
            type="expense",
            date_effective__gte=start,
            date_effective__lte=end,
        )
        if not include_archived:
            items = items.filter(status="active")
            expenses = expenses.filter(status="active")

        item_rows = (
            items.annotate(period=trunc("transaction__date_effective"))
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

        txn_rows = (
            expenses.annotate(period=trunc("date_effective"))
            .values("period")
            .annotate(
                txn_count=Count("id"),
                txn_spend=Coalesce(
                    Sum("amount"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                ),
            )
        )
        txn_map = {}
        for row in txn_rows:
            key = _period_start(_as_date(row["period"]), grain)
            if key is not None:
                txn_map[key] = row

        points = []
        for period in _iter_periods(start, end, grain):
            bucket = txn_map.get(period, {})
            spend = item_map.get(period, Decimal("0.00"))
            points.append(
                {
                    "period": period.isoformat(),
                    "item_spend": str(spend),
                    "txn_count": bucket.get("txn_count", 0),
                    "txn_spend": str(bucket.get("txn_spend", Decimal("0.00"))),
                    # Back-compat aliases for existing dashboard labels
                    "list_count": bucket.get("txn_count", 0),
                    "list_spend": str(bucket.get("txn_spend", Decimal("0.00"))),
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


class FinanceBreakdownView(APIView):
    """
    GET /api/finance/analytics/breakdown/?period=day|week|month&include_archived=0|1

    Current calendar period (today / this week Mon–today / this month 1st–today):
      categories — top 5 line-item categories + Other
      balance_composition — starting_balance vs period expense spend
    """

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        ensure_finance_ready(request.user)
        period = request.query_params.get("period", "week")
        if period not in ("day", "week", "month"):
            period = "week"

        include_archived = request.query_params.get("include_archived", "0") in (
            "1",
            "true",
            "True",
            "yes",
        )

        today = timezone.localdate()
        start, end = _current_period_bounds(period, today)
        owner = request.user

        items = TransactionItem.objects.filter(
            owner=owner,
            transaction__type="expense",
            transaction__date_effective__gte=start,
            transaction__date_effective__lte=end,
        )
        expenses = Transaction.objects.filter(
            owner=owner,
            type="expense",
            date_effective__gte=start,
            date_effective__lte=end,
        )
        if not include_archived:
            items = items.filter(status="active", transaction__status="active")
            expenses = expenses.filter(status="active")

        cat_rows = (
            items.values("category_id", "category__name", "category__parent_id")
            .annotate(
                amount=Coalesce(
                    Sum(ITEM_LINE),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            )
            .order_by("-amount")
        )

        parent_ids = {
            row["category__parent_id"] for row in cat_rows if row["category__parent_id"]
        }
        parents = {
            c.id: c.name
            for c in Category.objects.filter(owner=owner, id__in=parent_ids).only(
                "id", "name"
            )
        }

        ranked = []
        for row in cat_rows:
            amount = row["amount"] or Decimal("0.00")
            if amount <= 0:
                continue
            name = row["category__name"] or "Uncategorized"
            parent_id = row["category__parent_id"]
            if parent_id and parent_id in parents:
                name = f"{parents[parent_id]} › {name}"
            ranked.append(
                {
                    "id": row["category_id"],
                    "name": name,
                    "amount": amount,
                }
            )

        top = ranked[:TOP_CATEGORY_SLICES]
        rest = ranked[TOP_CATEGORY_SLICES:]
        other_total = sum((r["amount"] for r in rest), Decimal("0.00"))
        categories = [
            {
                "id": r["id"],
                "name": r["name"],
                "amount": _dec_str(r["amount"]),
            }
            for r in top
        ]
        if other_total > 0:
            categories.append(
                {
                    "id": None,
                    "name": "Other",
                    "amount": _dec_str(other_total),
                }
            )

        item_spend = sum((r["amount"] for r in ranked), Decimal("0.00"))
        spent = expenses.aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )["total"]

        bal = compute_balance(owner)
        starting = bal["starting_balance"] or Decimal("0.00")

        return Response(
            {
                "period": period,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "include_archived": include_archived,
                "categories": categories,
                "item_spend": _dec_str(item_spend),
                "balance_composition": {
                    "starting_balance": _dec_str(starting),
                    "spent": _dec_str(spent),
                },
            }
        )
