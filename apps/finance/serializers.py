from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from .models import (
    MAX_EXPENSE_CATEGORIES,
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


def _ordered_unique_ids(ids):
    ordered = []
    seen = set()
    for raw in ids:
        if raw is None or raw == "":
            continue
        cid = int(raw)
        if cid in seen:
            continue
        seen.add(cid)
        ordered.append(cid)
    return ordered


def resolve_expense_category_ids(primary_id, category_ids):
    """
    Build ordered expense category id list (primary first), capped at MAX.
    `category_ids` may be None (omit → primary only) or a list.
    """
    if category_ids is None:
        if primary_id is None:
            return []
        return [int(primary_id)]

    ordered = _ordered_unique_ids(category_ids)
    if primary_id is not None:
        pid = int(primary_id)
        ordered = [pid] + [c for c in ordered if c != pid]
    if len(ordered) > MAX_EXPENSE_CATEGORIES:
        raise ValidationError(
            {
                "categories": (
                    f"At most {MAX_EXPENSE_CATEGORIES} categories allowed on an expense."
                )
            }
        )
    return ordered


def apply_expense_categories(txn, category_ids, *, owner):
    """Validate expense Category rows and set primary FK + M2M tags."""
    if not category_ids:
        raise ValidationError(
            {"categories": "At least one expense category is required."}
        )
    cats = list(
        Category.objects.filter(owner=owner, kind="expense", pk__in=category_ids)
    )
    by_id = {c.id: c for c in cats}
    missing = [cid for cid in category_ids if cid not in by_id]
    if missing:
        raise ValidationError({"categories": f"Invalid category id(s): {missing}."})
    ordered_cats = [by_id[cid] for cid in category_ids]
    txn.category = ordered_cats[0]
    Transaction.objects.filter(pk=txn.pk).update(category_id=ordered_cats[0].id)
    txn.categories.set(ordered_cats)


def clear_expense_categories(txn):
    txn.categories.clear()


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
            "date_created",
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


class DraftTransactionItemSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    cost = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0")
    )
    quantity = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0")
    )
    unit = serializers.ChoiceField(choices=UNIT_CHOICES, default="pcs")
    # Accepted for OCR rollup only; not stored on TransactionItem.
    category_id = serializers.IntegerField(required=False, allow_null=True)


# -----------------------------------------------------------------------------
# Transactions
# -----------------------------------------------------------------------------
class TransactionSerializer(serializers.ModelSerializer):
    receipt_image = serializers.ImageField(read_only=True)
    items_total = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True, required=False, allow_null=True
    )
    item_count = serializers.IntegerField(read_only=True, required=False)
    categories = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Category.objects.all(), required=False
    )

    class Meta:
        model = Transaction
        fields = [
            "id",
            "uuid",
            "type",
            "amount",
            "title",
            "merchant",
            "note",
            "category",
            "categories",
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and getattr(request, "user", None):
            self.fields["categories"].child_relation.queryset = Category.objects.filter(
                owner=request.user
            )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.type == "expense":
            # Prefer M2M order with primary first; fall back to primary FK alone.
            tag_ids = list(instance.categories.values_list("id", flat=True))
            primary = instance.category_id
            if primary:
                ordered = [primary] + [i for i in tag_ids if i != primary]
            else:
                ordered = tag_ids
            data["categories"] = ordered
            if primary and primary not in ordered:
                data["categories"] = [primary] + ordered
        else:
            data["categories"] = []
        return data

    def validate(self, attrs):
        request = self.context["request"]
        txn_type = attrs.get("type", getattr(self.instance, "type", None))
        category = attrs.get("category", serializers.empty)
        if category is serializers.empty:
            category = getattr(self.instance, "category", None)
        categories_provided = "categories" in attrs
        categories = attrs.get("categories") if categories_provided else None

        if txn_type == "expense":
            primary_id = category.id if category is not None else None
            if categories_provided:
                raw_ids = [
                    c.id if isinstance(c, Category) else c for c in (categories or [])
                ]
                resolved = resolve_expense_category_ids(primary_id, raw_ids)
                if not resolved:
                    raise ValidationError(
                        {"categories": "At least one expense category is required."}
                    )
                cats = list(
                    Category.objects.filter(
                        owner=request.user, kind="expense", pk__in=resolved
                    )
                )
                by_id = {c.id: c for c in cats}
                if len(by_id) != len(resolved):
                    raise ValidationError(
                        {
                            "categories": (
                                "All categories must be valid expense categories."
                            )
                        }
                    )
                attrs["_expense_category_ids"] = resolved
                attrs["category"] = by_id[resolved[0]]
            elif "category" in attrs:
                if category is None:
                    raise ValidationError(
                        {"category": "Category is required for expense transactions."}
                    )
                if category.owner_id != request.user.id or category.kind != "expense":
                    raise ValidationError(
                        {"category": "Category kind must be expense."}
                    )
                attrs["_expense_primary_id"] = category.id
            elif not self.instance:
                raise ValidationError(
                    {"category": "Category is required for expense transactions."}
                )
            attrs.pop("categories", None)

        elif txn_type == "income":
            if category is None:
                raise ValidationError(
                    {"category": "Category is required for income transactions."}
                )
            if category.owner_id != request.user.id:
                raise ValidationError({"category": "Invalid category."})
            if category.kind != "income":
                raise ValidationError({"category": "Category kind must be income."})
            attrs.pop("categories", None)
            attrs["_clear_categories"] = True

        elif txn_type in ("transfer_in", "transfer_out"):
            attrs["category"] = None
            attrs.pop("categories", None)
            attrs["_clear_categories"] = True

        amount = attrs.get("amount", getattr(self.instance, "amount", None))
        if amount is not None and amount < 0:
            raise ValidationError({"amount": "Amount must be zero or positive."})

        return attrs

    def create(self, validated_data):
        expense_ids = validated_data.pop("_expense_category_ids", None)
        primary_only = validated_data.pop("_expense_primary_id", None)
        validated_data.pop("_clear_categories", None)
        validated_data.pop("categories", None)
        if expense_ids is None and primary_only is not None:
            expense_ids = [primary_only]
        elif expense_ids is None and validated_data.get("category") is not None:
            expense_ids = [validated_data["category"].id]
        txn = super().create(validated_data)
        if expense_ids is not None:
            apply_expense_categories(
                txn, expense_ids, owner=self.context["request"].user
            )
        return txn

    def update(self, instance, validated_data):
        expense_ids = validated_data.pop("_expense_category_ids", None)
        primary_only = validated_data.pop("_expense_primary_id", None)
        clear_cats = validated_data.pop("_clear_categories", False)
        validated_data.pop("categories", None)
        txn = super().update(instance, validated_data)
        if clear_cats:
            clear_expense_categories(txn)
        elif expense_ids is not None:
            apply_expense_categories(
                txn, expense_ids, owner=self.context["request"].user
            )
        elif primary_only is not None and txn.type == "expense":
            existing = list(txn.categories.values_list("id", flat=True))
            if not existing and txn.category_id:
                existing = [txn.category_id]
            merged = resolve_expense_category_ids(
                primary_only, existing or [primary_only]
            )
            apply_expense_categories(txn, merged, owner=self.context["request"].user)
        elif txn.type == "expense" and txn.category_id and not txn.categories.exists():
            apply_expense_categories(
                txn, [txn.category_id], owner=self.context["request"].user
            )
        return txn


class CommitBankEntrySerializer(serializers.Serializer):
    txn_type = serializers.ChoiceField(
        choices=["income", "transfer_in", "transfer_out"]
    )
    amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    title = serializers.CharField(max_length=255, allow_blank=True, required=False)
    merchant = serializers.CharField(max_length=255, allow_blank=True, required=False)
    note = serializers.CharField(allow_blank=True, required=False, default="")
    date_effective = serializers.DateField(required=False, allow_null=True)
    category_id = serializers.IntegerField(required=False, allow_null=True)

    def validate(self, attrs):
        txn_type = attrs.get("txn_type")
        if txn_type == "income" and not attrs.get("category_id"):
            raise ValidationError(
                {"category_id": "Income entries require an income category."}
            )
        if txn_type in ("transfer_in", "transfer_out"):
            attrs.pop("category_id", None)
        if not attrs.get("date_effective"):
            attrs.pop("date_effective", None)
        return attrs


class CommitReceiptSerializer(serializers.Serializer):
    document_kind = serializers.ChoiceField(
        choices=["retail_receipt", "bank_slip"],
        default="retail_receipt",
        required=False,
    )
    title = serializers.CharField(max_length=255, allow_blank=True, required=False)
    merchant = serializers.CharField(max_length=255, allow_blank=True, required=False)
    note = serializers.CharField(allow_blank=True, required=False, default="")
    category_id = serializers.IntegerField(required=False, allow_null=True)
    category_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=False,
        max_length=MAX_EXPENSE_CATEGORIES,
    )
    date_effective = serializers.DateField(required=False)
    items = DraftTransactionItemSerializer(many=True, required=False, allow_empty=True)
    entries = CommitBankEntrySerializer(many=True, required=False, allow_empty=True)

    def validate(self, attrs):
        kind = attrs.get("document_kind") or "retail_receipt"
        attrs["document_kind"] = kind
        if kind == "retail_receipt":
            primary = attrs.get("category_id")
            extras = attrs.get("category_ids")
            # Roll up optional per-line category_ids from items when header list omitted.
            if not extras and attrs.get("items"):
                rolled = []
                if primary:
                    rolled.append(primary)
                for row in attrs["items"]:
                    cid = row.get("category_id")
                    if cid and cid not in rolled:
                        rolled.append(cid)
                extras = rolled[:MAX_EXPENSE_CATEGORIES] or None
            try:
                resolved = resolve_expense_category_ids(primary, extras)
            except ValidationError:
                raise
            if not resolved:
                raise ValidationError(
                    {"category_id": "Category is required for retail receipts."}
                )
            attrs["category_ids"] = resolved
            attrs["category_id"] = resolved[0]
            if not attrs.get("items"):
                raise ValidationError(
                    {"items": "At least one line item is required for retail receipts."}
                )
        else:
            if not attrs.get("entries"):
                raise ValidationError(
                    {"entries": "At least one bank entry is required."}
                )
        return attrs


MAX_BULK_RECEIPT_COMMITS = 10


class BulkCommitReceiptSerializer(serializers.Serializer):
    """Validate a batch of reviewed receipt drafts (1–10)."""

    receipts = CommitReceiptSerializer(many=True, allow_empty=False)

    def validate_receipts(self, value):
        if len(value) > MAX_BULK_RECEIPT_COMMITS:
            raise ValidationError(
                f"At most {MAX_BULK_RECEIPT_COMMITS} receipts per batch."
            )
        return value


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
