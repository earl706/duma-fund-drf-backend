"""Derived per-source and profile balance (as-of today; future separate)."""

from decimal import Decimal

from django.db.models import Case, Count, IntegerField, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.response import Response

from .models import BalanceSource, Transaction
from .scope import ProfileScopedAPIView
from .seeds import profile_starting_balance


TYPE_KEYS = ("income", "expense", "transfer_in", "transfer_out")
INFLOW_TYPES = ("income", "transfer_in")
OUTFLOW_TYPES = ("expense", "transfer_out")


def _zero_totals():
    return {key: Decimal("0.00") for key in TYPE_KEYS}


def _add_total(bucket, key, amount):
    if amount is None:
        return
    bucket[key] = (bucket.get(key) or Decimal("0.00")) + (amount or Decimal("0.00"))


def _accumulate_effects(qs, by_source):
    """
    Apply ledger rows into per-source type buckets.
    Unpaired rows: debit/credit `source` by type as today.
    Internal transfer_out (to_source set): from gets transfer_out += amount+fee;
    destination gets transfer_in += amount.
    """
    unpaired = qs.filter(to_source__isnull=True)
    for row in unpaired.values("source_id", "type").annotate(
        total=Coalesce(Sum("amount"), Value(Decimal("0.00")))
    ):
        sid = row["source_id"]
        bucket = by_source.setdefault(sid, _zero_totals())
        if row["type"] in bucket:
            _add_total(bucket, row["type"], row["total"])

    internal = qs.filter(to_source__isnull=False, type="transfer_out")
    for row in internal.values("source_id", "to_source_id").annotate(
        principal=Coalesce(Sum("amount"), Value(Decimal("0.00"))),
        fees=Coalesce(Sum("fee"), Value(Decimal("0.00"))),
    ):
        principal = row["principal"] or Decimal("0.00")
        fees = row["fees"] or Decimal("0.00")
        from_bucket = by_source.setdefault(row["source_id"], _zero_totals())
        to_bucket = by_source.setdefault(row["to_source_id"], _zero_totals())
        _add_total(from_bucket, "transfer_out", principal + fees)
        _add_total(to_bucket, "transfer_in", principal)


def _typed_sums_from_effects(qs):
    """Profile-level type totals after dual-applying internal moves."""
    by_source = {}
    _accumulate_effects(qs, by_source)
    totals = _zero_totals()
    for bucket in by_source.values():
        for key in TYPE_KEYS:
            totals[key] += bucket[key]
    return totals


def _typed_sums(qs):
    """Backward-compatible name: includes internal dual-apply."""
    return _typed_sums_from_effects(qs)


def _net_from_totals(starting, totals):
    return (
        starting
        + totals["income"]
        - totals["expense"]
        + totals["transfer_in"]
        - totals["transfer_out"]
    )


def _flow_sum(totals, keys):
    return sum((totals[key] for key in keys), Decimal("0.00"))


def _source_kind_order():
    return Case(
        When(kind="cash", then=0),
        When(kind="bank", then=1),
        When(kind="ewallet", then=2),
        default=3,
        output_field=IntegerField(),
    )


def compute_balance(profile, as_of=None):
    """
    Available balance uses active transactions with date_effective <= as_of
    (default today). Future active rows contribute upcoming_in / upcoming_out
    and projected_balance (= available + upcoming_in − upcoming_out).
    """
    as_of = as_of or timezone.localdate()
    sources = list(
        BalanceSource.objects.filter(profile=profile)
        .annotate(_kind_order=_source_kind_order())
        .order_by("_kind_order", "name", "id")
    )
    txn_qs = Transaction.objects.filter(
        profile=profile,
        status="active",
        date_effective__lte=as_of,
    )
    by_source = {}
    _accumulate_effects(txn_qs, by_source)

    txn_counts = {}
    for row in txn_qs.values("source_id").annotate(c=Count("id")):
        txn_counts[row["source_id"]] = txn_counts.get(row["source_id"], 0) + int(
            row["c"]
        )
    for row in (
        txn_qs.filter(to_source__isnull=False)
        .values("to_source_id")
        .annotate(c=Count("id"))
    ):
        sid = row["to_source_id"]
        txn_counts[sid] = txn_counts.get(sid, 0) + int(row["c"])

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
    available = _net_from_totals(starting, profile_totals)

    upcoming_qs = Transaction.objects.filter(
        profile=profile,
        status="active",
        date_effective__gt=as_of,
    )
    upcoming_totals = _typed_sums(upcoming_qs)
    upcoming_in = _flow_sum(upcoming_totals, INFLOW_TYPES)
    upcoming_out = _flow_sum(upcoming_totals, OUTFLOW_TYPES)
    projected = available + upcoming_in - upcoming_out

    return {
        "as_of": as_of,
        "starting_balance": starting,
        "balance": available,
        "totals": profile_totals,
        "sources": source_rows,
        "upcoming_in": upcoming_in,
        "upcoming_out": upcoming_out,
        "upcoming_totals": upcoming_totals,
        "projected_balance": projected,
    }


def _dec_str(value):
    if value is None:
        value = Decimal("0.00")
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return str(value.quantize(Decimal("0.01")))


class BalanceView(ProfileScopedAPIView):
    """GET derived profile + per-source balances (as-of today)."""

    def get(self, request):
        data = compute_balance(self.get_profile())
        return Response(
            {
                "as_of": data["as_of"].isoformat(),
                "starting_balance": _dec_str(data["starting_balance"]),
                "balance": _dec_str(data["balance"]),
                "totals": {k: _dec_str(v) for k, v in data["totals"].items()},
                "upcoming_in": _dec_str(data["upcoming_in"]),
                "upcoming_out": _dec_str(data["upcoming_out"]),
                "upcoming_totals": {
                    k: _dec_str(v) for k, v in data["upcoming_totals"].items()
                },
                "projected_balance": _dec_str(data["projected_balance"]),
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
