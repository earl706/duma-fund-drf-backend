"""Title normalization and fuzzy matching for recurring purchase awareness."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Iterable

from django.db.models import Count
from django.utils import timezone

from .models import (
    PurchaseExclusion,
    PurchaseRegularMark,
    Transaction,
    TransactionItem,
)


SIZE_TOKEN_RE = re.compile(
    r"""
    (?:^|\s)
    (?:
        \d+(?:[.,]\d+)?\s*(?:kg|g|l|ml|pcs?|pc|pack|pk)
        | x\s*\d+
        | \d+\s*x\s*\d+
    )
    (?=\s|$|[^\w])
    """,
    re.IGNORECASE | re.VERBOSE,
)

PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
SPACE_RE = re.compile(r"\s+")

REGULAR_LOOKBACK_DAYS = 30
MIN_AUTO_REGULAR_COUNT = 2
DUE_SOON_RATIO = 0.8
TOKEN_OVERLAP_THRESHOLD = 0.55


def normalize_title(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip().lower()
    text = PUNCT_RE.sub(" ", text)
    text = SPACE_RE.sub(" ", text).strip()
    return text


def family_key(value: str | None) -> str:
    """Size/pack-stripped key; strip size tokens before punctuation so 1.5L survives."""
    if not value:
        return ""
    text = value.strip().lower()
    text = SIZE_TOKEN_RE.sub(" ", text)
    text = PUNCT_RE.sub(" ", text)
    text = SPACE_RE.sub(" ", text).strip()
    return text or normalize_title(value)


def _tokens(norm: str) -> set[str]:
    return {t for t in norm.split() if t}


def token_overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / max(len(ta), len(tb))


def exclusion_pair(key_a: str, key_b: str) -> tuple[str, str]:
    a, b = sorted([key_a, key_b])
    return a, b


def is_excluded(exclusions: set[tuple[str, str]], key_a: str, key_b: str) -> bool:
    if not key_a or not key_b or key_a == key_b:
        return False
    return exclusion_pair(key_a, key_b) in exclusions


@dataclass
class PurchaseEvent:
    title: str
    normalized_title: str
    family_key: str
    cost: Decimal
    quantity: Decimal
    unit: str
    merchant: str
    date_effective: date
    transaction_id: int
    item_id: int | None = None
    category_id: int | None = None
    is_header_only: bool = False


@dataclass
class MatchStats:
    display_title: str
    normalized_title: str
    family_key: str
    status: str  # seen | regular | lapsed | related
    purchase_count: int
    avg_price: Decimal | None
    typical_price: Decimal | None
    typical_qty: Decimal | None
    usual_interval_days: float | None
    last_merchant: str
    last_purchased_at: date | None
    days_since_last: int | None
    last_transaction_id: int | None
    recent: list[dict] = field(default_factory=list)
    related: list[dict] = field(default_factory=list)
    match_kind: str = "exact"  # exact | fuzzy | related
    score: float = 0.0


def load_exclusions(profile) -> set[tuple[str, str]]:
    return {
        (row.key_a, row.key_b)
        for row in PurchaseExclusion.objects.filter(profile=profile).only(
            "key_a", "key_b"
        )
    }


def load_regular_marks(profile) -> dict[str, str]:
    return {
        row.family_key: row.display_title
        for row in PurchaseRegularMark.objects.filter(profile=profile).only(
            "family_key", "display_title"
        )
    }


def iter_purchase_events(profile) -> list[PurchaseEvent]:
    """Active expense line items + header-only expenses (no archived)."""
    items = (
        TransactionItem.objects.filter(
            status="active",
            transaction__profile=profile,
            transaction__type="expense",
            transaction__status="active",
        )
        .select_related("transaction")
        .order_by("-transaction__date_effective", "-id")
    )

    events: list[PurchaseEvent] = []
    for item in items:
        txn = item.transaction
        merchant = (txn.merchant or "").strip() or "Unknown"
        events.append(
            PurchaseEvent(
                title=item.title,
                normalized_title=normalize_title(item.title),
                family_key=family_key(item.title),
                cost=item.cost,
                quantity=item.quantity,
                unit=item.unit or "pcs",
                merchant=merchant,
                date_effective=txn.date_effective,
                transaction_id=txn.id,
                item_id=item.id,
                category_id=txn.category_id,
                is_header_only=False,
            )
        )

    header_only = (
        Transaction.objects.filter(
            profile=profile,
            type="expense",
            status="active",
        )
        .annotate(item_count=Count("items"))
        .filter(item_count=0)
        .order_by("-date_effective", "-id")
    )
    for txn in header_only:
        title = (txn.title or "").strip()
        if not title or title.lower() == "untitled":
            continue
        merchant = (txn.merchant or "").strip() or "Unknown"
        events.append(
            PurchaseEvent(
                title=title,
                normalized_title=normalize_title(title),
                family_key=family_key(title),
                cost=txn.amount,
                quantity=Decimal("1.00"),
                unit="pcs",
                merchant=merchant,
                date_effective=txn.date_effective,
                transaction_id=txn.id,
                item_id=None,
                category_id=txn.category_id,
                is_header_only=True,
            )
        )

    return events


def _mode_prefer_recent(values: list, dates: list[date]):
    if not values:
        return None
    counts = Counter(values)
    top = counts.most_common(1)[0][1]
    candidates = [v for v, c in counts.items() if c == top]
    if len(candidates) == 1:
        return candidates[0]
    # Prefer most recent among tied modes
    for i in range(len(values) - 1, -1, -1):
        if values[i] in candidates:
            return values[i]
    return candidates[0]


def _usual_interval_days(dates: list[date]) -> float | None:
    uniq = sorted(set(dates))
    if len(uniq) < 2:
        return None
    gaps = [(uniq[i] - uniq[i - 1]).days for i in range(1, len(uniq))]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return None
    return float(median(gaps))


def _decimal_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01")))


def build_stats_for_events(
    events: list[PurchaseEvent],
    *,
    display_title: str | None = None,
    status_override: str | None = None,
    manually_regular: bool = False,
    today: date | None = None,
    match_kind: str = "exact",
    score: float = 1.0,
) -> MatchStats | None:
    if not events:
        return None
    today = today or timezone.localdate()
    # Newest first for recent trail
    ordered = sorted(
        events, key=lambda e: (e.date_effective, e.transaction_id), reverse=True
    )
    display = display_title or ordered[0].title
    norm = ordered[0].normalized_title
    fam = ordered[0].family_key

    prices = [e.cost for e in ordered]
    qtys = [e.quantity for e in ordered]
    dates = [e.date_effective for e in ordered]
    avg_price = sum(prices) / Decimal(len(prices)) if prices else None
    typical_price = _mode_prefer_recent(prices, dates)
    typical_qty = _mode_prefer_recent(qtys, dates)
    interval = _usual_interval_days(dates)
    last = ordered[0]
    days_since = (today - last.date_effective).days

    lookback_start = today - timedelta(days=REGULAR_LOOKBACK_DAYS)
    recent_window_count = sum(1 for e in ordered if e.date_effective >= lookback_start)
    auto_regular = recent_window_count >= MIN_AUTO_REGULAR_COUNT
    was_regular = auto_regular or manually_regular

    if status_override:
        status = status_override
    elif was_regular:
        threshold = max(2 * (interval or 0), float(REGULAR_LOOKBACK_DAYS))
        if days_since > threshold:
            status = "lapsed"
        else:
            status = "regular"
    else:
        status = "seen"

    recent = [
        {
            "title": e.title,
            "cost": _decimal_str(e.cost),
            "quantity": _decimal_str(e.quantity),
            "unit": e.unit,
            "merchant": e.merchant,
            "date_effective": e.date_effective.isoformat(),
            "transaction_id": e.transaction_id,
            "item_id": e.item_id,
        }
        for e in ordered[:3]
    ]

    return MatchStats(
        display_title=display,
        normalized_title=norm,
        family_key=fam,
        status=status,
        purchase_count=len(ordered),
        avg_price=(
            avg_price.quantize(Decimal("0.01")) if avg_price is not None else None
        ),
        typical_price=typical_price,
        typical_qty=typical_qty,
        usual_interval_days=interval,
        last_merchant=last.merchant,
        last_purchased_at=last.date_effective,
        days_since_last=days_since,
        last_transaction_id=last.transaction_id,
        recent=recent,
        related=[],
        match_kind=match_kind,
        score=score,
    )


def stats_to_dict(stats: MatchStats) -> dict:
    return {
        "display_title": stats.display_title,
        "normalized_title": stats.normalized_title,
        "family_key": stats.family_key,
        "status": stats.status,
        "match_kind": stats.match_kind,
        "score": round(stats.score, 3),
        "purchase_count": stats.purchase_count,
        "avg_price": _decimal_str(stats.avg_price),
        "typical_price": _decimal_str(stats.typical_price),
        "typical_qty": _decimal_str(stats.typical_qty),
        "usual_interval_days": stats.usual_interval_days,
        "last_merchant": stats.last_merchant,
        "last_purchased_at": (
            stats.last_purchased_at.isoformat() if stats.last_purchased_at else None
        ),
        "days_since_last": stats.days_since_last,
        "last_transaction_id": stats.last_transaction_id,
        "recent": stats.recent,
        "related": stats.related,
    }


def group_events_by_normalized(
    events: Iterable[PurchaseEvent],
) -> dict[str, list[PurchaseEvent]]:
    groups: dict[str, list[PurchaseEvent]] = defaultdict(list)
    for event in events:
        if event.normalized_title:
            groups[event.normalized_title].append(event)
    return groups


def group_events_by_family(
    events: Iterable[PurchaseEvent],
) -> dict[str, list[PurchaseEvent]]:
    groups: dict[str, list[PurchaseEvent]] = defaultdict(list)
    for event in events:
        if event.family_key:
            groups[event.family_key].append(event)
    return groups


def score_query_against_title(
    query_norm: str, query_family: str, title_norm: str, title_family: str
):
    if not query_norm or not title_norm:
        return 0.0, "none"
    if query_norm == title_norm:
        return 1.0, "exact"
    overlap = token_overlap(query_norm, title_norm)
    if overlap >= TOKEN_OVERLAP_THRESHOLD:
        return 0.7 + 0.25 * overlap, "fuzzy"
    if query_family and title_family and query_family == title_family:
        return 0.45, "related"
    if query_norm in title_norm or title_norm in query_norm:
        return 0.55, "fuzzy"
    return 0.0, "none"


def lookup_purchases(profile, query: str, limit: int = 5) -> list[dict]:
    query = (query or "").strip()
    if len(query) < 2:
        return []

    query_norm = normalize_title(query)
    query_fam = family_key(query)
    exclusions = load_exclusions(profile)
    regular_marks = load_regular_marks(profile)
    events = iter_purchase_events(profile)
    by_norm = group_events_by_normalized(events)
    by_family = group_events_by_family(events)

    scored: list[tuple[float, str, str, list[PurchaseEvent]]] = []
    for norm, group in by_norm.items():
        if is_excluded(exclusions, query_norm, norm):
            continue
        fam = group[0].family_key
        if is_excluded(exclusions, query_fam, fam):
            continue
        score, kind = score_query_against_title(query_norm, query_fam, norm, fam)
        if score <= 0:
            continue
        scored.append((score, kind, norm, group))

    scored.sort(key=lambda row: (-row[0], -len(row[3])))
    results: list[dict] = []
    today = timezone.localdate()

    for score, kind, norm, group in scored[:limit]:
        fam = group[0].family_key
        manually = fam in regular_marks
        stats = build_stats_for_events(
            group,
            display_title=group[0].title,
            manually_regular=manually,
            today=today,
            match_kind=kind,
            score=score,
        )
        if not stats:
            continue

        # Related siblings: same family, different normalized title
        related = []
        for sibling in by_family.get(fam, []):
            if sibling.normalized_title == norm:
                continue
            if is_excluded(exclusions, norm, sibling.normalized_title):
                continue
            related.append(sibling)
        if related:
            # Group related by their normalized titles
            related_groups = group_events_by_normalized(related)
            related_stats = []
            for r_norm, r_group in list(related_groups.items())[:5]:
                r_stats = build_stats_for_events(
                    r_group,
                    manually_regular=r_group[0].family_key in regular_marks,
                    today=today,
                    match_kind="related",
                    score=0.45,
                    status_override="related",
                )
                if r_stats:
                    related_stats.append(
                        {
                            "display_title": r_stats.display_title,
                            "normalized_title": r_stats.normalized_title,
                            "purchase_count": r_stats.purchase_count,
                            "typical_price": _decimal_str(r_stats.typical_price),
                            "last_purchased_at": (
                                r_stats.last_purchased_at.isoformat()
                                if r_stats.last_purchased_at
                                else None
                            ),
                            "last_transaction_id": r_stats.last_transaction_id,
                        }
                    )
            stats.related = related_stats

        results.append(stats_to_dict(stats))

    return results


def build_insights(profile) -> dict:
    regular_marks = load_regular_marks(profile)
    events = iter_purchase_events(profile)
    by_norm = group_events_by_normalized(events)
    today = timezone.localdate()

    regular, lapsed, due_soon, recently_seen = [], [], [], []

    for norm, group in by_norm.items():
        fam = group[0].family_key
        # Skip if excluded against itself somehow — N/A
        manually = fam in regular_marks
        stats = build_stats_for_events(
            group,
            manually_regular=manually,
            today=today,
        )
        if not stats:
            continue
        payload = stats_to_dict(stats)

        if stats.status == "regular":
            regular.append(payload)
            if (
                stats.usual_interval_days
                and stats.days_since_last is not None
                and stats.days_since_last >= DUE_SOON_RATIO * stats.usual_interval_days
            ):
                due_soon.append(payload)
        elif stats.status == "lapsed":
            lapsed.append(payload)
        else:
            recently_seen.append(payload)

    # Include manually marked families with no purchases yet (edge) — skip

    def sort_key(row):
        return (
            -(row.get("purchase_count") or 0),
            row.get("display_title") or "",
        )

    regular.sort(key=sort_key)
    lapsed.sort(
        key=lambda r: (-(r.get("days_since_last") or 0), r.get("display_title") or "")
    )
    due_soon.sort(
        key=lambda r: (-(r.get("days_since_last") or 0), r.get("display_title") or "")
    )
    recently_seen.sort(
        key=lambda r: (r.get("last_purchased_at") or "", r.get("display_title") or ""),
        reverse=True,
    )

    return {
        "regular": regular,
        "lapsed": lapsed,
        "due_soon": due_soon,
        "recently_seen": recently_seen[:20],
    }


def build_notifications(profile) -> list[dict]:
    insights = build_insights(profile)
    notes = []
    for row in insights["due_soon"]:
        interval = row.get("usual_interval_days")
        days = row.get("days_since_last")
        cycle = f"~{int(round(interval))}d cycle" if interval else "usual cycle"
        last = f"last {days}d ago" if days is not None else "recently"
        notes.append(
            {
                "id": f"due-{row['normalized_title']}",
                "kind": "due_soon",
                "title": row["display_title"],
                "message": f"Usually due: {row['display_title']} ({cycle}, {last})",
                "transaction_id": row.get("last_transaction_id"),
                "normalized_title": row["normalized_title"],
            }
        )
    for row in insights["lapsed"]:
        days = row.get("days_since_last")
        last = f"last bought {days}d ago" if days is not None else "no recent buy"
        notes.append(
            {
                "id": f"lapsed-{row['normalized_title']}",
                "kind": "lapsed",
                "title": row["display_title"],
                "message": f"Lapsed: {row['display_title']} ({last})",
                "transaction_id": row.get("last_transaction_id"),
                "normalized_title": row["normalized_title"],
            }
        )
    return notes
