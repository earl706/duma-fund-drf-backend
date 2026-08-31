from rest_framework import serializers

from .models import CostItem, CostList


# -----------------------------------------------------------------------------
# Cost list serializer
# -----------------------------------------------------------------------------
class CostListSerializer(serializers.ModelSerializer):
    total_cost = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True
    )

    class Meta:
        model = CostList
        fields = [
            "id",
            "uuid",
            "title",
            "description",
            "status",
            "date_created",
            "date_effective",
            "date_last_modified",
            "total_cost",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "uuid",
            "date_last_modified",
            "total_cost",
            "created_at",
            "updated_at",
        ]


# -----------------------------------------------------------------------------
# Cost item serializer
# -----------------------------------------------------------------------------
class CostItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = CostItem
        fields = [
            "id",
            "uuid",
            "cost_list",
            "title",
            "description",
            "status",
            "cost",
            "quantity",
            "date_created",
            "date_effective",
            "date_last_modified",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "uuid",
            "cost_list",
            "date_last_modified",
            "created_at",
            "updated_at",
        ]
