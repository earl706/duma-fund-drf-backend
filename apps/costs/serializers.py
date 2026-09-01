from decimal import Decimal

from rest_framework import serializers

from .models import CostItem, CostList, UNIT_CHOICES


# -----------------------------------------------------------------------------
# Bulk import (receipt scan confirm)
# -----------------------------------------------------------------------------
class DraftCostItemSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    cost = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0")
    )
    quantity = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0")
    )
    unit = serializers.ChoiceField(choices=UNIT_CHOICES, default="pcs")
    date_effective = serializers.DateField(required=False)


class BulkCostItemImportSerializer(serializers.Serializer):
    date_effective = serializers.DateField(required=False)
    items = DraftCostItemSerializer(many=True, allow_empty=False)


# -----------------------------------------------------------------------------
# Cost list serializer
# -----------------------------------------------------------------------------
class CostListSerializer(serializers.ModelSerializer):
    total_cost = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True
    )
    receipt_image = serializers.ImageField(read_only=True)

    class Meta:
        model = CostList
        fields = [
            "id",
            "uuid",
            "title",
            "description",
            "receipt_image",
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
            "status",
            "cost",
            "quantity",
            "unit",
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
