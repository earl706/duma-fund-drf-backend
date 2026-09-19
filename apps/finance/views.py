from django.db import transaction as db_transaction
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from .models import (
    PROFILE_ROLE_OWNER,
    Category,
    Transaction,
    TransactionItem,
)
from .scope import (
    LEDGER_WRITE_ROLES,
    OWNER_ONLY_ROLES,
    ProfileScopedViewSet,
    require_role,
    resolve_profile,
)
from .serializers import (
    CategoryReassignDeleteSerializer,
    CategorySerializer,
    TransactionItemSerializer,
    TransactionSerializer,
    annotate_transaction_amount,
    apply_expense_categories,
    sync_expense_amount,
)


def _unlink_or_reassign_category(category, target, profile):
    """
    Remove category from expense M2M tags when other labels remain.
    Reassign to target when it is the sole / primary-only category.
    Income keeps a single FK → always reassign.
    """
    Transaction.objects.filter(category=category, type="income").update(category=target)

    expense_qs = (
        Transaction.objects.filter(profile=profile, type="expense")
        .filter(Q(category=category) | Q(categories=category))
        .distinct()
    )

    for txn in expense_qs:
        tag_ids = list(txn.categories.values_list("id", flat=True))
        if category.id not in tag_ids and txn.category_id:
            tag_ids = [txn.category_id] + [i for i in tag_ids if i != txn.category_id]
        if category.id not in tag_ids:
            continue
        remaining = [i for i in tag_ids if i != category.id]
        if remaining:
            apply_expense_categories(txn, remaining, profile=profile)
        else:
            apply_expense_categories(txn, [target.id], profile=profile)


# -----------------------------------------------------------------------------
# Categories (owner-only writes)
# -----------------------------------------------------------------------------
class CategoryViewSet(ProfileScopedViewSet):
    serializer_class = CategorySerializer
    queryset = Category.objects.all()
    filterset_fields = ["kind", "parent", "is_system"]
    search_fields = ["name"]
    ordering_fields = ["name", "kind", "created_at"]
    write_roles = OWNER_ONLY_ROLES

    def get_queryset(self):
        return super().get_queryset().select_related("parent")

    def perform_create(self, serializer):
        serializer.save(
            owner=self.request.user,
            profile=self.get_profile(),
            is_system=False,
        )

    @action(detail=True, methods=["post"], url_path="reassign-and-delete")
    def reassign_and_delete(self, request, pk=None):
        require_role(self.get_membership(), PROFILE_ROLE_OWNER)
        category = self.get_object()
        ser = CategoryReassignDeleteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        target_id = ser.validated_data["target_category_id"]
        profile = self.get_profile()
        try:
            target = Category.objects.get(
                pk=target_id, profile=profile, kind=category.kind
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
            _unlink_or_reassign_category(category, target, profile)
            Category.objects.filter(parent=category).update(parent=target)
            category.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    def destroy(self, request, *args, **kwargs):
        require_role(self.get_membership(), PROFILE_ROLE_OWNER)
        category = self.get_object()
        profile = self.get_profile()
        sole_expense = False
        for txn in (
            Transaction.objects.filter(profile=profile, type="expense")
            .filter(Q(category=category) | Q(categories=category))
            .distinct()
            .prefetch_related("categories")
        ):
            tag_ids = set(txn.categories.values_list("id", flat=True))
            if not tag_ids and txn.category_id:
                tag_ids = {txn.category_id}
            if tag_ids == {category.id}:
                sole_expense = True
                break

        income_in_use = Transaction.objects.filter(
            category=category, type="income"
        ).exists()
        has_children = category.children.exists()

        if sole_expense or income_in_use or has_children:
            raise ValidationError(
                {
                    "detail": (
                        "Category is in use as the only label (or has children). "
                        "Use reassign-and-delete with a target_category_id."
                    )
                }
            )

        with db_transaction.atomic():
            for txn in (
                Transaction.objects.filter(profile=profile, type="expense")
                .filter(Q(category=category) | Q(categories=category))
                .distinct()
            ):
                tag_ids = list(txn.categories.values_list("id", flat=True))
                if category.id not in tag_ids and txn.category_id == category.id:
                    tag_ids = [txn.category_id] + [
                        i for i in tag_ids if i != txn.category_id
                    ]
                remaining = [i for i in tag_ids if i != category.id]
                if remaining:
                    apply_expense_categories(txn, remaining, profile=profile)
            category.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


# -----------------------------------------------------------------------------
# Transactions
# -----------------------------------------------------------------------------
class TransactionViewSet(ProfileScopedViewSet):
    serializer_class = TransactionSerializer
    queryset = Transaction.objects.all()
    filterset_fields = ["type", "status", "date_effective", "date_created"]
    search_fields = ["title", "note", "merchant"]
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
    write_roles = LEDGER_WRITE_ROLES

    def get_queryset(self):
        qs = (
            annotate_transaction_amount(super().get_queryset())
            .annotate(item_count=Count("items", distinct=True))
            .select_related("category")
            .prefetch_related("categories")
        )
        category = self.request.query_params.get("category")
        if category not in (None, ""):
            qs = qs.filter(
                Q(category_id=category) | Q(categories__id=category)
            ).distinct()
        return qs

    def perform_create(self, serializer):
        txn_type = serializer.validated_data.get("type")
        extras = {"owner": self.request.user, "profile": self.get_profile()}
        if txn_type in ("transfer_in", "transfer_out"):
            serializer.save(category=None, **extras)
        else:
            serializer.save(**extras)

    def perform_update(self, serializer):
        instance = serializer.save()
        sync_expense_amount(instance)


# -----------------------------------------------------------------------------
# Nested transaction items
# -----------------------------------------------------------------------------
class TransactionItemViewSet(ProfileScopedViewSet):
    serializer_class = TransactionItemSerializer
    queryset = TransactionItem.objects.all()
    filterset_fields = ["status", "date_created"]
    search_fields = ["title"]
    ordering_fields = [
        "title",
        "status",
        "cost",
        "quantity",
        "unit",
        "date_created",
        "date_last_modified",
        "created_at",
    ]
    write_roles = LEDGER_WRITE_ROLES

    def get_queryset(self):
        txn = self._scoped_transaction()
        return TransactionItem.objects.filter(transaction=txn)

    def _scoped_transaction(self):
        try:
            return Transaction.objects.get(
                pk=self.kwargs["transaction_pk"], profile=resolve_profile(self.request)
            )
        except Transaction.DoesNotExist as exc:
            raise NotFound() from exc

    def perform_create(self, serializer):
        txn = self._scoped_transaction()
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
