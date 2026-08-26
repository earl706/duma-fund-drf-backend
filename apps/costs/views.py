from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework.exceptions import NotFound

from apps.common.viewsets import OwnedModelViewSet

from .models import CostItem, CostList
from .serializers import CostItemSerializer, CostListSerializer


LINE_TOTAL = ExpressionWrapper(
    F("items__cost") * F("items__quantity"),
    output_field=DecimalField(max_digits=14, decimal_places=2),
)


# -----------------------------------------------------------------------------
# Cost list viewset
# -----------------------------------------------------------------------------
class CostListViewSet(OwnedModelViewSet):
    serializer_class = CostListSerializer
    queryset = CostList.objects.all()
    filterset_fields = ["status", "date_created"]
    search_fields = ["title", "description"]
    ordering_fields = [
        "title",
        "status",
        "date_created",
        "date_last_modified",
        "created_at",
        "updated_at",
        "total_cost",
    ]

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .annotate(
                total_cost=Coalesce(
                    Sum(LINE_TOTAL),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            )
        )


# -----------------------------------------------------------------------------
# Nested cost item viewset
# -----------------------------------------------------------------------------
class CostItemViewSet(OwnedModelViewSet):
    serializer_class = CostItemSerializer
    queryset = CostItem.objects.all()
    filterset_fields = ["status", "date_created"]
    search_fields = ["title", "description"]
    ordering_fields = [
        "title",
        "status",
        "cost",
        "quantity",
        "date_created",
        "date_last_modified",
        "created_at",
    ]

    def _owned_list(self):
        try:
            return CostList.objects.get(
                pk=self.kwargs["list_pk"], owner=self.request.user
            )
        except CostList.DoesNotExist as exc:
            raise NotFound() from exc

    def get_queryset(self):
        cost_list = self._owned_list()
        return super().get_queryset().filter(cost_list=cost_list)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, cost_list=self._owned_list())
