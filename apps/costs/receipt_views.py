"""Receipt scan and bulk item import."""

from django.core.files.base import ContentFile
from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsEmailVerified

from .models import CostItem, CostList, today
from .receipt_scan import scan_receipt_image
from .serializers import BulkCostItemImportSerializer, CostItemSerializer


def _owned_list(request, list_pk):
    try:
        return CostList.objects.get(pk=list_pk, owner=request.user)
    except CostList.DoesNotExist as exc:
        raise NotFound() from exc


class ReceiptScanView(APIView):
    """POST multipart image → vision provider draft items; stores image on the list."""

    permission_classes = [IsAuthenticated, IsEmailVerified]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, list_pk):
        cost_list = _owned_list(request, list_pk)
        upload = request.FILES.get("image")
        if not upload:
            raise ValidationError({"image": "Receipt image is required."})

        file_bytes = upload.read()
        mime_type = upload.content_type or "image/jpeg"

        try:
            draft = scan_receipt_image(file_bytes, mime_type)
        except RuntimeError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        cost_list.receipt_image.save(upload.name, ContentFile(file_bytes), save=True)
        cost_list.refresh_from_db()

        receipt_url = None
        if cost_list.receipt_image:
            receipt_url = request.build_absolute_uri(cost_list.receipt_image.url)

        return Response(
            {
                "date_effective": draft["date_effective"],
                "items": draft["items"],
                "receipt_image": receipt_url,
            }
        )


class BulkCostItemImportView(APIView):
    """POST JSON items[] → create CostItems on an owned list."""

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def post(self, request, list_pk):
        cost_list = _owned_list(request, list_pk)
        serializer = BulkCostItemImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        effective = payload.get("date_effective") or today()
        created = []

        with transaction.atomic():
            for row in payload["items"]:
                item = CostItem.objects.create(
                    owner=request.user,
                    cost_list=cost_list,
                    title=row["title"],
                    cost=row["cost"],
                    quantity=row["quantity"],
                    unit=row["unit"],
                    date_effective=row.get("date_effective") or effective,
                    date_created=today(),
                )
                created.append(item)

        return Response(
            CostItemSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )
