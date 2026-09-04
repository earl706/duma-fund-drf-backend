from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from .models import (
    UNIT_CHOICES,
    Category,
    Transaction,
    TransactionItem,
    UserFinance,
)


LINE_TOTAL = ExpressionWrapper(
    F("cost") * F("quantity"),
    output_field=DecimalField(max_digits=14, decimal_places=2),
)


def annotate_transaction_amount(qs):
    return qs.annotate(
        items_total=Coalesce(
            Sum(
                ExpressionWrapper(
                    F("items__cost") * F("items__quantity"),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            ),
            Value(None),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )


def sync_expense_amount(txn):
    """Set expense amount from line items when any exist."""
    if txn.type != "expense":
        return txn
    total = txn.items.aggregate(
        total=Coalesce(
            Sum(LINE_TOTAL),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )["total"]
    if txn.items.exists():
        if txn.amount != total:
            Transaction.objects.filter(pk=txn.pk).update(amount=total)
            txn.amount = total
    return txn


# -----------------------------------------------------------------------------
# Category
# -----------------------------------------------------------------------------
class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = [
            "id",
            "uuid",
            "name",
            "kind",
            "parent",
            "is_system",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "uuid", "is_system", "created_at", "updated_at"]

    def validate(self, attrs):
        owner = self.context["request"].user
        parent = attrs.get("parent", getattr(self.instance, "parent", None))
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        name = attrs.get("name", getattr(self.instance, "name", None))

        if parent is not None:
            if parent.owner_id != owner.id:
                raise ValidationError({"parent": "Invalid parent category."})
            if parent.parent_id is not None:
                raise ValidationError(
                    {"parent": "Categories can only nest one level deep."}
                )
            if kind and parent.kind != kind:
                raise ValidationError({"kind": "Must match parent kind."})
            if not kind:
                attrs["kind"] = parent.kind

        if name and kind:
            qs = Category.objects.filter(
                owner=owner,
                parent=parent,
                kind=kind,
                name__iexact=name.strip(),
            )
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(
                    {"name": "A category with this name already exists."}
                )

        return attrs

    def create(self, validated_data):
        validated_data["is_system"] = False
        return super().create(validated_data)


class CategoryReassignDeleteSerializer(serializers.Serializer):
    target_category_id = serializers.IntegerField()


# -----------------------------------------------------------------------------
# Transaction items
# -----------------------------------------------------------------------------
class TransactionItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = TransactionItem
        fields = [
            "id",
            "uuid",
            "transaction",
            "title",
            "status",
            "cost",
            "quantity",
            "unit",
            "category",
            "date_created",
            "date_effective",
            "date_last_modified",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "uuid",
            "transaction",
            "date_last_modified",
            "created_at",
            "updated_at",
        ]

    def validate_category(self, category):
        request = self.context["request"]
        if category.owner_id != request.user.id:
            raise ValidationError("Invalid category.")
        if category.kind != "expense":
            raise ValidationError("Line items require an expense category.")
        return category


class DraftTransactionItemSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    cost = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0")
    )
    quantity = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0")
    )
    unit = serializers.ChoiceField(choices=UNIT_CHOICES, default="pcs")
    category_id = serializers.IntegerField()
    date_effective = serializers.DateField(required=False)


# -----------------------------------------------------------------------------
# Transactions
# -----------------------------------------------------------------------------
class TransactionSerializer(serializers.ModelSerializer):
    receipt_image = serializers.ImageField(read_only=True)
    items_total = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True, required=False, allow_null=True
    )
    item_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Transaction
        fields = [
            "id",
            "uuid",
            "type",
            "amount",
            "title",
            "note",
            "category",
            "receipt_image",
            "status",
            "date_created",
            "date_effective",
            "date_last_modified",
            "items_total",
            "item_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "uuid",
            "date_last_modified",
            "receipt_image",
            "items_total",
            "item_count",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        request = self.context["request"]
        txn_type = attrs.get("type", getattr(self.instance, "type", None))
        category = attrs.get("category", getattr(self.instance, "category", None))

        if txn_type in ("income", "expense"):
            if category is None:
                raise ValidationError(
                    {"category": "Category is required for income and expense."}
                )
            if category.owner_id != request.user.id:
                raise ValidationError({"category": "Invalid category."})
            expected_kind = "income" if txn_type == "income" else "expense"
            if category.kind != expected_kind:
                raise ValidationError(
                    {"category": f"Category kind must be {expected_kind}."}
                )
        elif txn_type in ("transfer_in", "transfer_out"):
            # Always clear category on transfers (partial updates often omit it).
            attrs["category"] = None

        amount = attrs.get("amount", getattr(self.instance, "amount", None))
        if amount is not None and amount < 0:
            raise ValidationError({"amount": "Amount must be zero or positive."})

        return attrs


class CommitReceiptSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, allow_blank=True, required=False)
    note = serializers.CharField(allow_blank=True, required=False, default="")
    category_id = serializers.IntegerField()
    date_effective = serializers.DateField(required=False)
    items = DraftTransactionItemSerializer(many=True, allow_empty=False)


# -----------------------------------------------------------------------------
# Balance / starting balance
# -----------------------------------------------------------------------------
class StartingBalanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserFinance
        fields = ["starting_balance"]


class BalanceSerializer(serializers.Serializer):
    starting_balance = serializers.DecimalField(max_digits=14, decimal_places=2)
    balance = serializers.DecimalField(max_digits=14, decimal_places=2)
    totals = serializers.DictField(
        child=serializers.DecimalField(max_digits=14, decimal_places=2)
    )
