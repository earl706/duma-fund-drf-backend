"""Default category seeds and helpers."""

from .models import Category, UserFinance

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


def ensure_user_finance(user):
    obj, _ = UserFinance.objects.get_or_create(user=user)
    return obj


def seed_categories_for_user(user):
    """Create system root categories if the user has none of that kind."""
    created = []
    for kind, names in (("expense", EXPENSE_SEEDS), ("income", INCOME_SEEDS)):
        if Category.objects.filter(owner=user, kind=kind).exists():
            continue
        for name in names:
            created.append(
                Category.objects.create(
                    owner=user,
                    name=name,
                    kind=kind,
                    parent=None,
                    is_system=True,
                )
            )
    return created


def ensure_finance_ready(user):
    ensure_user_finance(user)
    seed_categories_for_user(user)


def get_default_expense_category(user):
    ensure_finance_ready(user)
    cat = (
        Category.objects.filter(owner=user, kind="expense", name="Other", parent=None)
        .order_by("id")
        .first()
    )
    if cat:
        return cat
    return (
        Category.objects.filter(owner=user, kind="expense", parent=None)
        .order_by("id")
        .first()
    )
