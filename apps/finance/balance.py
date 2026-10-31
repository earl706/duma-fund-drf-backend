"""Derived per-source and profile balance."""

from decimal import Decimal

from django.db.models import Case, Count, IntegerField, Sum, Value, When
from django.db.models.functions import Coalesce
from rest_framework.response import Response

from .models import BalanceSource, Transaction
from .scope import ProfileScopedAPIView
from .seeds import profile_starting_balance


TYPE_KEYS = ("income", "expense", "transfer_in", "transfer_out")


def _zero_totals():
    return {key: Decimal("0.00") for key in TYPE_KEYS}


def _typed_sums(qs):
    totals = _zero_totals()
    for row in qs.values("type").annotate(
        total=Coalesce(Sum("amount"), Value(Decimal("0.00")))
    ):
        if row["type"] in totals:
            totals[row["type"]] = row["total"] or Decimal("0.00")
    return totals


def _net_from_totals(starting, totals):
    return (
        starting
        + totals["income"]
        - totals["expense"]
        + totals["transfer_in"]
        - totals["transfer_out"]
    )


def _source_kind_order():
    return Case(
        When(kind="cash", then=0),
        When(kind="bank", then=1),
        When(kind="ewallet", then=2),
        default=3,
        output_field=IntegerField(),
    )


def compute_balance(profile):
    sources = list(
        BalanceSource.objects.filter(profile=profile)
        .annotate(_kind_order=_source_kind_order())
        .order_by("_kind_order", "name", "id")
    )
    txn_qs = Transaction.objects.filter(profile=profile)
    by_source = {}
    for row in txn_qs.values("source_id", "type").annotate(
        total=Coalesce(Sum("amount"), Value(Decimal("0.00")))
    ):
        sid = row["source_id"]
        bucket = by_source.setdefault(sid, _zero_totals())
        if row["type"] in bucket:
            bucket[row["type"]] = row["total"] or Decimal("0.00")

    txn_counts = {
        row["source_id"]: row["c"]
        for row in txn_qs.values("source_id").annotate(c=Count("id"))
    }

    source_rows = []
    profile_totals = _zero_totals()
    for src in sources:
        totals = by_source.get(src.id, _zero_totals())
        starting = src.starting_balance or Decimal("0.00")
        for key in TYPE_KEYS:
            profile_totals[key] += totals[key]
        source_rows.append(
            {
                "id": src.id,
                "uuid": str(src.uuid),
                "name": src.name,
                "kind": src.kind,
                "status": src.status,
                "is_system": src.is_system,
                "starting_balance": starting,
                "balance": _net_from_totals(starting, totals),
                "totals": totals,
                "transaction_count": int(txn_counts.get(src.id, 0)),
            }
        )

    starting = profile_starting_balance(profile) or Decimal("0.00")
    return {
        "starting_balance": starting,
        "balance": _net_from_totals(starting, profile_totals),
        "totals": profile_totals,
        "sources": source_rows,
    }


def _dec_str(value):
    if value is None:
        value = Decimal("0.00")
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return str(value.quantize(Decimal("0.01")))


class BalanceView(ProfileScopedAPIView):
    """GET derived profile + per-source balances."""

    def get(self, request):
        data = compute_balance(self.get_profile())
        return Response(
            {
                "starting_balance": _dec_str(data["starting_balance"]),
                "balance": _dec_str(data["balance"]),
                "totals": {k: _dec_str(v) for k, v in data["totals"].items()},
                "sources": [
                    {
                        **row,
                        "starting_balance": _dec_str(row["starting_balance"]),
                        "balance": _dec_str(row["balance"]),
                        "totals": {k: _dec_str(v) for k, v in row["totals"].items()},
                    }
                    for row in data["sources"]
                ],
            }
        )
