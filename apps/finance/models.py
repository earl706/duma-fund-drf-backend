from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.common.models import OwnedModel


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


# -----------------------------------------------------------------------------
# Per-user finance settings (starting balance)
# -----------------------------------------------------------------------------
class UserFinance(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="finance",
    )
    starting_balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Finance({self.user_id})"


# -----------------------------------------------------------------------------
# Nested expense / income categories
# -----------------------------------------------------------------------------
class Category(OwnedModel):
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
                fields=["owner", "parent", "name", "kind"],
                name="finance_category_unique_name_per_parent",
            )
        ]

    def __str__(self):
        return f"{self.kind}:{self.name}"


# -----------------------------------------------------------------------------
# Ledger transaction (header)
# -----------------------------------------------------------------------------
class Transaction(OwnedModel):
    type = models.CharField(max_length=20, choices=TRANSACTION_TYPE_CHOICES)
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    title = models.CharField(max_length=255, blank=True)
    note = models.TextField(blank=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transactions",
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
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="transaction_items",
    )
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
