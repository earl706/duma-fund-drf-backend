from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from apps.common.viewsets import OwnedModelViewSet

from .models import Category, Transaction, TransactionItem
from .seeds import ensure_finance_ready
from .serializers import (
    CategoryReassignDeleteSerializer,
    CategorySerializer,
    TransactionItemSerializer,
    TransactionSerializer,
    annotate_transaction_amount,
    sync_expense_amount,
)


LINE_TOTAL_ANN = ExpressionWrapper(
    F("items__cost") * F("items__quantity"),
    output_field=DecimalField(max_digits=14, decimal_places=2),
)


# -----------------------------------------------------------------------------
# Categories
# -----------------------------------------------------------------------------
class CategoryViewSet(OwnedModelViewSet):
    serializer_class = CategorySerializer
    queryset = Category.objects.all()
    filterset_fields = ["kind", "parent", "is_system"]
    search_fields = ["name"]
    ordering_fields = ["name", "kind", "created_at"]

    def get_queryset(self):
        ensure_finance_ready(self.request.user)
        return super().get_queryset().select_related("parent")

    def perform_create(self, serializer):
        ensure_finance_ready(self.request.user)
        serializer.save(owner=self.request.user, is_system=False)

    @action(detail=True, methods=["post"], url_path="reassign-and-delete")
    def reassign_and_delete(self, request, pk=None):
        category = self.get_object()
        ser = CategoryReassignDeleteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        target_id = ser.validated_data["target_category_id"]
        try:
            target = Category.objects.get(
                pk=target_id, owner=request.user, kind=category.kind
            )
        except Category.DoesNotExist as exc:
            raise ValidationError(
                {"target_category_id": "Target category not found."}
            ) from exc
        if target.pk == category.pk:
            raise ValidationError(
                {"target_category_id": "Choose a different category."}
            )

        with db_transaction.atomic():
            Transaction.objects.filter(category=category).update(category=target)
            TransactionItem.objects.filter(category=category).update(category=target)
            # Re-parent children onto target (or keep as roots under same kind)
            Category.objects.filter(parent=category).update(parent=target)
            category.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    def destroy(self, request, *args, **kwargs):
        category = self.get_object()
        in_use = (
            Transaction.objects.filter(category=category).exists()
            or TransactionItem.objects.filter(category=category).exists()
            or category.children.exists()
        )
        if in_use:
            raise ValidationError(
                {
                    "detail": (
                        "Category is in use. Use reassign-and-delete with a "
                        "target_category_id."
                    )
                }
            )
        return super().destroy(request, *args, **kwargs)


# -----------------------------------------------------------------------------
# Transactions
# -----------------------------------------------------------------------------
class TransactionViewSet(OwnedModelViewSet):
    serializer_class = TransactionSerializer
    queryset = Transaction.objects.all()
    filterset_fields = ["type", "status", "category", "date_effective", "date_created"]
    search_fields = ["title", "note"]
    ordering_fields = [
        "title",
        "type",
        "amount",
        "status",
        "date_created",
        "date_effective",
        "date_last_modified",
        "created_at",
        "updated_at",
    ]

    def get_queryset(self):
        ensure_finance_ready(self.request.user)
        return (
            annotate_transaction_amount(super().get_queryset())
            .annotate(item_count=Count("items"))
            .select_related("category")
        )

    def perform_create(self, serializer):
        ensure_finance_ready(self.request.user)
        txn_type = serializer.validated_data.get("type")
        if txn_type in ("transfer_in", "transfer_out"):
            serializer.save(owner=self.request.user, category=None)
        else:
            serializer.save(owner=self.request.user)

    def perform_update(self, serializer):
        instance = serializer.save()
        sync_expense_amount(instance)


# -----------------------------------------------------------------------------
# Nested transaction items
# -----------------------------------------------------------------------------
class TransactionItemViewSet(OwnedModelViewSet):
    serializer_class = TransactionItemSerializer
    queryset = TransactionItem.objects.all()
    filterset_fields = ["status", "category", "date_created", "date_effective"]
    search_fields = ["title"]
    ordering_fields = [
        "title",
        "status",
        "cost",
        "quantity",
        "unit",
        "date_created",
        "date_effective",
        "date_last_modified",
        "created_at",
    ]

    def _owned_transaction(self):
        try:
            return Transaction.objects.get(
                pk=self.kwargs["transaction_pk"], owner=self.request.user
            )
        except Transaction.DoesNotExist as exc:
            raise NotFound() from exc

    def get_queryset(self):
        txn = self._owned_transaction()
        return super().get_queryset().filter(transaction=txn).select_related("category")

    def perform_create(self, serializer):
        txn = self._owned_transaction()
        if txn.type != "expense":
            raise ValidationError(
                {"detail": "Line items are only allowed on expense transactions."}
            )
        item = serializer.save(owner=self.request.user, transaction=txn)
        sync_expense_amount(txn)
        return item

    def perform_update(self, serializer):
        item = serializer.save()
        sync_expense_amount(item.transaction)

    def perform_destroy(self, instance):
        txn = instance.transaction
        super().perform_destroy(instance)
        sync_expense_amount(txn)
