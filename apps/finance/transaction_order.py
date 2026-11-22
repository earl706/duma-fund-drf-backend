"""Same-day day_order moves and cross-day date_effective jumps."""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction as db_transaction
from django.db.models import Max

from .models import Transaction

# Safety cap so empty-ledger walks cannot spin forever.
_MAX_DAY_WALK = 3660


def _day_qs(profile_id, date_effective, *, exclude_id=None):
    qs = Transaction.objects.filter(
        profile_id=profile_id, date_effective=date_effective
    )
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    return qs


def _renumber_day(profile_id, date_effective, *, exclude_id=None):
    """Assign contiguous day_order 0..n-1 by current day_order, then id."""
    rows = list(
        _day_qs(profile_id, date_effective, exclude_id=exclude_id).order_by(
            "day_order", "id"
        )
    )
    patches = []
    for idx, row in enumerate(rows):
        if row.day_order != idx:
            before = {"day_order": row.day_order}
            row.day_order = idx
            row.save(update_fields=["day_order", "date_last_modified", "updated_at"])
            patches.append(
                {
                    "id": row.id,
                    "before": before,
                    "after": {"day_order": idx},
                }
            )
    return patches


def _snapshot_order_fields(row):
    return {
        "date_effective": row.date_effective.isoformat(),
        "day_order": row.day_order,
    }


def next_day_order(profile_id, date_effective):
    """Next append index for a new txn on that day."""
    agg = _day_qs(profile_id, date_effective).aggregate(m=Max("day_order"))
    m = agg["m"]
    return 0 if m is None else int(m) + 1


def move_transaction(row: Transaction, direction: str, *, user=None):
    """
    Move ``row`` within its day or across days.

    ``direction`` is model-order relative:
      - ``up``: toward default list start (newer date_effective / lower day_order)
      - ``down``: opposite

    Returns dict ``{moved: bool, patches: [...]}``.
    """
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")

    with db_transaction.atomic():
        row = Transaction.objects.select_for_update().filter(pk=row.pk).get()
        profile_id = row.profile_id
        day = row.date_effective
        same_day = list(
            _day_qs(profile_id, day).select_for_update().order_by("day_order", "id")
        )
        idx = next((i for i, r in enumerate(same_day) if r.id == row.id), None)
        if idx is None:
            return {"moved": False, "patches": []}

        # Same-day swap with visible neighbor.
        if direction == "up" and idx > 0:
            other = same_day[idx - 1]
            return _swap_day_order(row, other, user=user)
        if direction == "down" and idx < len(same_day) - 1:
            other = same_day[idx + 1]
            return _swap_day_order(row, other, user=user)

        # Cross-day calendar walk.
        step = timedelta(days=1) if direction == "up" else timedelta(days=-1)
        target_day = day
        target_rows = None
        for _ in range(_MAX_DAY_WALK):
            target_day = target_day + step
            target_rows = list(
                _day_qs(profile_id, target_day)
                .select_for_update()
                .order_by("day_order", "id")
            )
            if target_rows:
                break
        else:
            return {"moved": False, "patches": []}

        if not target_rows:
            return {"moved": False, "patches": []}

        patches = []
        before = _snapshot_order_fields(row)
        old_day = row.date_effective

        if direction == "up":
            # Land last on target day.
            new_order = max(r.day_order for r in target_rows) + 1
            row.date_effective = target_day
            row.day_order = new_order
            _touch(row, user=user)
            patches.append(
                {
                    "id": row.id,
                    "before": before,
                    "after": _snapshot_order_fields(row),
                }
            )
        else:
            # Land first: shift existing up, take 0.
            for r in reversed(target_rows):
                b = {"day_order": r.day_order}
                r.day_order = r.day_order + 1
                _touch(r, user=user)
                patches.append(
                    {
                        "id": r.id,
                        "before": b,
                        "after": {"day_order": r.day_order},
                    }
                )
            row.date_effective = target_day
            row.day_order = 0
            _touch(row, user=user)
            patches.append(
                {
                    "id": row.id,
                    "before": before,
                    "after": _snapshot_order_fields(row),
                }
            )

        # Close gaps on the vacated day.
        patches.extend(_renumber_day(profile_id, old_day, exclude_id=row.id))
        return {"moved": True, "patches": patches}


def _touch(row, *, user=None):
    update_fields = ["date_effective", "day_order", "date_last_modified", "updated_at"]
    if user is not None:
        row.updated_by = user
        update_fields.append("updated_by")
    row.save(update_fields=update_fields)


def _swap_day_order(a: Transaction, b: Transaction, *, user=None):
    before_a = _snapshot_order_fields(a)
    before_b = _snapshot_order_fields(b)
    a.day_order, b.day_order = b.day_order, a.day_order
    _touch(a, user=user)
    _touch(b, user=user)
    return {
        "moved": True,
        "patches": [
            {"id": a.id, "before": before_a, "after": _snapshot_order_fields(a)},
            {"id": b.id, "before": before_b, "after": _snapshot_order_fields(b)},
        ],
    }
