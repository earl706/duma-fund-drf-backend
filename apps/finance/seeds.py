"""Default profile + category + Cash source seeds."""

from decimal import Decimal

from .models import (
    DEFAULT_CASH_SOURCE_NAME,
    DEFAULT_PROFILE_NAME,
    PROFILE_ROLE_OWNER,
    SOURCE_KIND_CASH,
    BalanceSource,
    BudgetProfile,
    BudgetProfileMembership,
    Category,
)


EXPENSE_SEEDS = [
    "Food",
    "Transport",
    "Housing",
    "Utilities",
    "Health",
    "Entertainment",
    "Shopping",
    "Other",
]

INCOME_SEEDS = [
    "Salary",
    "Freelance",
    "Gifts",
    "Other",
]


def ensure_owner_membership(profile):
    BudgetProfileMembership.objects.get_or_create(
        profile=profile,
        user=profile.owner,
        defaults={"role": PROFILE_ROLE_OWNER},
    )


def seed_cash_source_for_profile(profile, starting_balance=None):
    """Guarantee a system Cash source. Does not overwrite an existing starting amount."""
    existing = BalanceSource.objects.filter(
        profile=profile, kind=SOURCE_KIND_CASH
    ).first()
    if existing:
        return existing
    starting = (
        Decimal("0.00") if starting_balance is None else Decimal(str(starting_balance))
    )
    return BalanceSource.objects.create(
        owner=profile.owner,
        profile=profile,
        name=DEFAULT_CASH_SOURCE_NAME,
        kind=SOURCE_KIND_CASH,
        starting_balance=starting,
        is_system=True,
        status="active",
    )


def get_cash_source(profile):
    """Return the profile's Cash source, creating it if missing."""
    ensure_owner_membership(profile)
    return seed_cash_source_for_profile(profile)


def match_source_by_name(profile, name):
    """Case-insensitive name match; prefers active sources."""
    needle = (name or "").strip()
    if not needle:
        return None
    qs = BalanceSource.objects.filter(profile=profile, name__iexact=needle)
    return qs.filter(status="active").first() or qs.first()


def resolve_transaction_source(profile, source_id=None, merchant=""):
    """
    Pick a source for a new transaction.
    Explicit id wins; else match merchant to a source name; else Cash.
    """
    if source_id not in (None, ""):
        from rest_framework.exceptions import ValidationError

        try:
            pk = int(source_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"source": "Invalid balance source."}) from exc
        src = BalanceSource.objects.filter(pk=pk, profile=profile).first()
        if src is None:
            raise ValidationError({"source": "Invalid balance source."})
        return src
    matched = match_source_by_name(profile, merchant)
    if matched is not None:
        return matched
    return get_cash_source(profile)


def profile_starting_balance(profile):
    from django.db.models import Sum, Value
    from django.db.models.functions import Coalesce

    total = profile.sources.aggregate(
        total=Coalesce(Sum("starting_balance"), Value(Decimal("0.00")))
    )["total"]
    return (total or Decimal("0.00")).quantize(Decimal("0.01"))


def ensure_personal_profile(user):
    """Return the user's oldest owned profile, creating Personal if needed."""
    profile = BudgetProfile.objects.filter(owner=user).order_by("id").first()
    if profile:
        ensure_owner_membership(profile)
        seed_cash_source_for_profile(profile)
        return profile
    profile = BudgetProfile.objects.create(
        owner=user,
        name=DEFAULT_PROFILE_NAME,
    )
    ensure_owner_membership(profile)
    seed_cash_source_for_profile(profile)
    return profile


def seed_categories_for_profile(profile):
    """Create system root categories if the profile has none of that kind."""
    created = []
    owner = profile.owner
    for kind, names in (("expense", EXPENSE_SEEDS), ("income", INCOME_SEEDS)):
        if Category.objects.filter(profile=profile, kind=kind).exists():
            continue
        for name in names:
            created.append(
                Category.objects.create(
                    owner=owner,
                    profile=profile,
                    name=name,
                    kind=kind,
                    parent=None,
                    is_system=True,
                )
            )
    return created


def ensure_profile_ready(profile):
    ensure_owner_membership(profile)
    seed_categories_for_profile(profile)
    seed_cash_source_for_profile(profile)
    return profile


def ensure_finance_ready(user):
    """Guarantee the user has at least one profile with seeded categories."""
    profile = ensure_personal_profile(user)
    seed_categories_for_profile(profile)
    seed_cash_source_for_profile(profile)
    return profile


def get_default_expense_category(profile):
    ensure_profile_ready(profile)
    cat = (
        Category.objects.filter(
            profile=profile, kind="expense", name="Other", parent=None
        )
        .order_by("id")
        .first()
    )
    if cat:
        return cat
    return (
        Category.objects.filter(profile=profile, kind="expense", parent=None)
        .order_by("id")
        .first()
    )


def get_default_income_category(profile):
    ensure_profile_ready(profile)
    cat = (
        Category.objects.filter(
            profile=profile, kind="income", name="Other", parent=None
        )
        .order_by("id")
        .first()
    )
    if cat:
        return cat
    return (
        Category.objects.filter(profile=profile, kind="income", parent=None)
        .order_by("id")
        .first()
    )
