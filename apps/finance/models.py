from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.common.models import OwnedModel, TimeStampedModel


def today():
    return timezone.localdate()


STATUS_CHOICES = [
    ("active", "Active"),
    ("archived", "Archived"),
]

UNIT_CHOICES = [
    ("pcs", "pcs"),
    ("kg", "kg"),
    ("g", "g"),
    ("L", "L"),
    ("mL", "mL"),
]

CATEGORY_KIND_CHOICES = [
    ("expense", "Expense"),
    ("income", "Income"),
]

TRANSACTION_TYPE_CHOICES = [
    ("income", "Income"),
    ("expense", "Expense"),
    ("transfer_in", "Transfer in"),
    ("transfer_out", "Transfer out"),
]

PROFILE_ROLE_OWNER = "owner"
PROFILE_ROLE_EDITOR = "editor"
PROFILE_ROLE_VIEWER = "viewer"

PROFILE_ROLE_CHOICES = [
    (PROFILE_ROLE_OWNER, "Owner"),
    (PROFILE_ROLE_EDITOR, "Editor"),
    (PROFILE_ROLE_VIEWER, "Viewer"),
]

INVITE_ROLE_CHOICES = [
    (PROFILE_ROLE_EDITOR, "Editor"),
    (PROFILE_ROLE_VIEWER, "Viewer"),
]

INVITE_STATUS_PENDING = "pending"
INVITE_STATUS_ACCEPTED = "accepted"
INVITE_STATUS_DECLINED = "declined"
INVITE_STATUS_REVOKED = "revoked"

INVITE_STATUS_CHOICES = [
    (INVITE_STATUS_PENDING, "Pending"),
    (INVITE_STATUS_ACCEPTED, "Accepted"),
    (INVITE_STATUS_DECLINED, "Declined"),
    (INVITE_STATUS_REVOKED, "Revoked"),
]

DEFAULT_PROFILE_NAME = "Personal"

# Expense headers may carry multiple unordered category labels (incl. primary).
MAX_EXPENSE_CATEGORIES = 5


# -----------------------------------------------------------------------------
# Budget profile (ledger) + sharing
# -----------------------------------------------------------------------------
class BudgetProfile(TimeStampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_budget_profiles",
    )
    name = models.CharField(max_length=100)
    starting_balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    class Meta(TimeStampedModel.Meta):
        ordering = ["name", "id"]

    def __str__(self):
        return f"{self.name} ({self.owner_id})"


class BudgetProfileMembership(TimeStampedModel):
    profile = models.ForeignKey(
        BudgetProfile,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="budget_profile_memberships",
    )
    role = models.CharField(max_length=16, choices=PROFILE_ROLE_CHOICES)

    class Meta(TimeStampedModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "user"],
                name="finance_membership_unique_user",
            )
        ]

    def __str__(self):
        return f"{self.user_id}:{self.role}@{self.profile_id}"


class BudgetProfileInvite(TimeStampedModel):
    profile = models.ForeignKey(
        BudgetProfile,
        on_delete=models.CASCADE,
        related_name="invites",
    )
    invited_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="budget_profile_invites",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_budget_profile_invites",
    )
    role = models.CharField(max_length=16, choices=INVITE_ROLE_CHOICES)
    status = models.CharField(
        max_length=16,
        choices=INVITE_STATUS_CHOICES,
        default=INVITE_STATUS_PENDING,
    )

    class Meta(TimeStampedModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "invited_user"],
                condition=Q(status=INVITE_STATUS_PENDING),
                name="finance_invite_unique_pending",
            )
        ]

    def __str__(self):
        return f"invite:{self.invited_user_id}@{self.profile_id}:{self.status}"


# -----------------------------------------------------------------------------
# Nested expense / income categories
# -----------------------------------------------------------------------------
class Category(OwnedModel):
    profile = models.ForeignKey(
        BudgetProfile,
        on_delete=models.CASCADE,
        related_name="categories",
    )
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=CATEGORY_KIND_CHOICES)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    is_system = models.BooleanField(default=False)

    class Meta(OwnedModel.Meta):
        verbose_name_plural = "categories"
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "parent", "name", "kind"],
                name="finance_category_unique_name_per_parent_profile",
            )
        ]

    def __str__(self):
        return f"{self.kind}:{self.name}"


# -----------------------------------------------------------------------------
# Ledger transaction (header)
# -----------------------------------------------------------------------------
class Transaction(OwnedModel):
    profile = models.ForeignKey(
        BudgetProfile,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    type = models.CharField(max_length=20, choices=TRANSACTION_TYPE_CHOICES)
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    title = models.CharField(max_length=255, blank=True)
    merchant = models.CharField(max_length=255, blank=True, default="")
    note = models.TextField(blank=True)
    # Primary category: required for income/expense; null for transfers.
    # For expenses, also mirrored in `categories` (M2M, max MAX_EXPENSE_CATEGORIES).
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transactions",
    )
    categories = models.ManyToManyField(
        Category,
        blank=True,
        related_name="tagged_transactions",
    )
    receipt_image = models.ImageField(
        upload_to="receipts/%Y/%m/", blank=True, null=True
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    date_created = models.DateField(default=today)
    date_effective = models.DateField(default=today)
    date_last_modified = models.DateField(auto_now=True)

    class Meta(OwnedModel.Meta):
        ordering = ["-date_effective", "-created_at"]

    def __str__(self):
        return f"{self.type} {self.amount} ({self.title or self.pk})"


# -----------------------------------------------------------------------------
# Expense line items (former CostItem)
# -----------------------------------------------------------------------------
class TransactionItem(OwnedModel):
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name="items",
    )
    title = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    unit = models.CharField(max_length=8, choices=UNIT_CHOICES, default="pcs")
    date_created = models.DateField(default=today)
    date_last_modified = models.DateField(auto_now=True)

    class Meta(OwnedModel.Meta):
        pass

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.transaction_id:
            self.transaction.save()

    def delete(self, *args, **kwargs):
        transaction = self.transaction
        super().delete(*args, **kwargs)
        transaction.save()


# -----------------------------------------------------------------------------
# Recurring purchase preferences (manual regular + exclusions)
# -----------------------------------------------------------------------------
class PurchaseRegularMark(OwnedModel):
    """User-pinned staple; family_key from purchase_match.family_key(title)."""

    profile = models.ForeignKey(
        BudgetProfile,
        on_delete=models.CASCADE,
        related_name="purchase_regular_marks",
    )
    family_key = models.CharField(max_length=255, db_index=True)
    display_title = models.CharField(max_length=255)

    class Meta(OwnedModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "family_key"],
                name="finance_purchase_regular_unique_profile_key",
            )
        ]

    def __str__(self):
        return f"regular:{self.display_title}"


class PurchaseExclusion(OwnedModel):
    """Bidirectional 'not the same product' pair (key_a <= key_b)."""

    profile = models.ForeignKey(
        BudgetProfile,
        on_delete=models.CASCADE,
        related_name="purchase_exclusions",
    )
    key_a = models.CharField(max_length=255, db_index=True)
    key_b = models.CharField(max_length=255, db_index=True)

    class Meta(OwnedModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "key_a", "key_b"],
                name="finance_purchase_exclusion_unique_pair",
            )
        ]

    def __str__(self):
        return f"exclude:{self.key_a}|{self.key_b}"
