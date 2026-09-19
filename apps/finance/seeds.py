"""Default profile + category seeds."""

from decimal import Decimal

from .models import (
    DEFAULT_PROFILE_NAME,
    PROFILE_ROLE_OWNER,
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


def ensure_personal_profile(user):
    """Return the user's oldest owned profile, creating Personal if needed."""
    profile = BudgetProfile.objects.filter(owner=user).order_by("id").first()
    if profile:
        ensure_owner_membership(profile)
        return profile
    profile = BudgetProfile.objects.create(
        owner=user,
        name=DEFAULT_PROFILE_NAME,
        starting_balance=Decimal("0.00"),
    )
    ensure_owner_membership(profile)
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
    return profile


def ensure_finance_ready(user):
    """Guarantee the user has at least one profile with seeded categories."""
    profile = ensure_personal_profile(user)
    seed_categories_for_profile(profile)
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
