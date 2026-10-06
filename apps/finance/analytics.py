"""Spend / transaction activity and rolling-window breakdown for the dashboard."""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce, TruncDay, TruncMonth, TruncWeek
from django.utils import timezone
from rest_framework.response import Response

from .balance import compute_balance
from .models import Category, Transaction, TransactionItem
from .scope import ProfileScopedAPIView


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
OTHER_SLICE_NAME = "Other"
ROLLING_DAY_WINDOWS = frozenset({7, 28, 60, 120})
DEFAULT_ROLLING_DAYS = 28
TYPE_KEYS = ("income", "expense", "transfer_in", "transfer_out")
INFLOW_TYPES = ("income", "transfer_in")
OUTFLOW_TYPES = ("expense", "transfer_out")


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


def _parse_rolling_days(value):
    try:
        days = int(value)
    except (TypeError, ValueError):
        return DEFAULT_ROLLING_DAYS
    if days not in ROLLING_DAY_WINDOWS:
        return DEFAULT_ROLLING_DAYS
    return days


def _rolling_bounds(days, today=None):
    """Inclusive window of `days` calendar days ending today."""
    today = today or timezone.localdate()
    return today - timedelta(days=days - 1), today


def _empty_type_totals():
    return {key: Decimal("0.00") for key in TYPE_KEYS}


def _dec_str(value):
    if value is None:
        value = Decimal("0.00")
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return str(value.quantize(Decimal("0.01")))


def _is_other_slice(name):
    return (name or "").strip().casefold() == OTHER_SLICE_NAME.casefold()


def _pie_categories(ranked):
    """Top named slices plus one Other bucket (seeded Other + leftovers)."""
    named = [row for row in ranked if not _is_other_slice(row["name"])]
    leftover = [row for row in ranked if _is_other_slice(row["name"])]
    top = named[:TOP_CATEGORY_SLICES]
    rest = named[TOP_CATEGORY_SLICES:] + leftover
    other_total = sum((row["amount"] for row in rest), Decimal("0.00"))
    categories = [
        {
            "id": row["id"],
            "name": row["name"],
            "amount": _dec_str(row["amount"]),
        }
        for row in top
    ]
    if other_total > 0:
        categories.append(
            {
                "id": None,
                "name": OTHER_SLICE_NAME,
                "amount": _dec_str(other_total),
            }
        )
    return categories


class FinanceAnalyticsView(ProfileScopedAPIView):
    """
    GET /api/finance/analytics/?grain=day|week|month&include_archived=0|1&start=&end=

    Zero-filled points bucketed by Transaction.date_effective:
      money_in = income + transfer_in
      money_out = expense + transfer_out
      item_spend, txn_count, txn_spend (expense headers; back-compat)
    """

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
        default_start, default_end = _rolling_bounds(DEFAULT_ROLLING_DAYS, today)
        start = _parse_date(request.query_params.get("start"), default_start)
        end = _parse_date(request.query_params.get("end"), default_end)
        if start > end:
            start, end = end, start

        trunc = GRAIN_TRUNC[grain]
        profile = self.get_profile()

        items = TransactionItem.objects.filter(
            transaction__profile=profile,
            transaction__date_effective__gte=start,
            transaction__date_effective__lte=end,
        )
        txns = Transaction.objects.filter(
            profile=profile,
            date_effective__gte=start,
            date_effective__lte=end,
        )
        if not include_archived:
            items = items.filter(status="active")
            txns = txns.filter(status="active")

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

        type_rows = (
            txns.annotate(period=trunc("date_effective"))
            .values("period", "type")
            .annotate(
                txn_count=Count("id"),
                total=Coalesce(
                    Sum("amount"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                ),
            )
        )
        typed_map = defaultdict(_empty_type_totals)
        expense_counts = {}
        for row in type_rows:
            key = _period_start(_as_date(row["period"]), grain)
            if key is None:
                continue
            txn_type = row["type"]
            if txn_type in typed_map[key]:
                typed_map[key][txn_type] = row["total"] or Decimal("0.00")
            if txn_type == "expense":
                expense_counts[key] = row["txn_count"]

        points = []
        for period in _iter_periods(start, end, grain):
            typed = typed_map.get(period) or _empty_type_totals()
            expense = typed["expense"]
            money_in = sum((typed[key] for key in INFLOW_TYPES), Decimal("0.00"))
            money_out = sum((typed[key] for key in OUTFLOW_TYPES), Decimal("0.00"))
            spend = item_map.get(period, Decimal("0.00"))
            txn_count = expense_counts.get(period, 0)
            points.append(
                {
                    "period": period.isoformat(),
                    "item_spend": str(spend),
                    "txn_count": txn_count,
                    "txn_spend": _dec_str(expense),
                    "money_in": _dec_str(money_in),
                    "money_out": _dec_str(money_out),
                    # Back-compat aliases for existing dashboard labels
                    "list_count": txn_count,
                    "list_spend": _dec_str(expense),
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


class FinanceBreakdownView(ProfileScopedAPIView):
    """
    GET /api/finance/analytics/breakdown/?period=7|28|60|120&include_archived=0|1

    Rolling window of N calendar days ending today (inclusive):
      categories — top 5 named expense header categories + one Other
        (seeded Other and leftover names share that bucket; full amount in every tag)
      totals — window income / expense / transfer_in / transfer_out
      balance_composition — starting_balance vs window expense spend
    """

    def get(self, request):
        period = _parse_rolling_days(request.query_params.get("period"))

        include_archived = request.query_params.get("include_archived", "0") in (
            "1",
            "true",
            "True",
            "yes",
        )

        today = timezone.localdate()
        start, end = _rolling_bounds(period, today)
        profile = self.get_profile()

        expenses = Transaction.objects.filter(
            profile=profile,
            type="expense",
            date_effective__gte=start,
            date_effective__lte=end,
        )
        if not include_archived:
            expenses = expenses.filter(status="active")

        # Join M2M so each tagged category gets the full header amount.
        cat_rows = (
            expenses.filter(categories__isnull=False)
            .values(
                "categories__id",
                "categories__name",
                "categories__parent_id",
            )
            .annotate(
                amount=Coalesce(
                    Sum("amount"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            )
            .order_by("-amount")
        )

        # Expenses that only have primary FK (no M2M yet) still contribute.
        primary_only = (
            expenses.filter(categories__isnull=True, category__isnull=False)
            .values("category_id", "category__name", "category__parent_id")
            .annotate(
                amount=Coalesce(
                    Sum("amount"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            )
        )

        totals = {}
        meta = {}
        for row in cat_rows:
            cid = row["categories__id"]
            if cid is None:
                continue
            amount = row["amount"] or Decimal("0.00")
            totals[cid] = totals.get(cid, Decimal("0.00")) + amount
            meta[cid] = (row["categories__name"], row["categories__parent_id"])
        for row in primary_only:
            cid = row["category_id"]
            if cid is None:
                continue
            amount = row["amount"] or Decimal("0.00")
            totals[cid] = totals.get(cid, Decimal("0.00")) + amount
            meta[cid] = (row["category__name"], row["category__parent_id"])

        parent_ids = {parent for _, parent in meta.values() if parent}
        parents = {
            c.id: c.name
            for c in Category.objects.filter(profile=profile, id__in=parent_ids).only(
                "id", "name"
            )
        }

        ranked = []
        for cid, amount in totals.items():
            if amount <= 0:
                continue
            name, parent_id = meta.get(cid, ("Uncategorized", None))
            name = name or "Uncategorized"
            if parent_id and parent_id in parents:
                name = f"{parents[parent_id]} › {name}"
            ranked.append({"id": cid, "name": name, "amount": amount})
        ranked.sort(key=lambda r: r["amount"], reverse=True)
        categories = _pie_categories(ranked)

        # Sum of pie slices may exceed spent when txns have multiple tags.
        tagged_spend = sum((r["amount"] for r in ranked), Decimal("0.00"))

        type_qs = Transaction.objects.filter(
            profile=profile,
            date_effective__gte=start,
            date_effective__lte=end,
        )
        if not include_archived:
            type_qs = type_qs.filter(status="active")
        typed = {
            "income": Decimal("0.00"),
            "expense": Decimal("0.00"),
            "transfer_in": Decimal("0.00"),
            "transfer_out": Decimal("0.00"),
        }
        for row in type_qs.values("type").annotate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        ):
            if row["type"] in typed:
                typed[row["type"]] = row["total"] or Decimal("0.00")
        spent = typed["expense"]

        bal = compute_balance(profile)
        starting = bal["starting_balance"] or Decimal("0.00")

        return Response(
            {
                "period": period,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "include_archived": include_archived,
                "categories": categories,
                "item_spend": _dec_str(tagged_spend),
                "totals": {key: _dec_str(val) for key, val in typed.items()},
                "balance_composition": {
                    "starting_balance": _dec_str(starting),
                    "spent": _dec_str(spent),
                },
            }
        )
