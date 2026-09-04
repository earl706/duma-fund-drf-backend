"""Standalone receipt scan and commit to expense Transaction + items."""

import json
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import transaction as db_transaction
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsEmailVerified

from .models import Category, Transaction, TransactionItem, today
from .receipt_scan import scan_receipt_image
from .serializers import (
    CommitReceiptSerializer,
    TransactionSerializer,
    sync_expense_amount,
)
from .seeds import ensure_finance_ready
from .views import annotate_transaction_amount


class ReceiptScanView(APIView):
    """POST multipart image → vision draft (merchant, categories, items). Does not persist."""

    permission_classes = [IsAuthenticated, IsEmailVerified]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        ensure_finance_ready(request.user)
        upload = request.FILES.get("image")
        if not upload:
            raise ValidationError({"image": "Receipt image is required."})

        file_bytes = upload.read()
        mime_type = upload.content_type or "image/jpeg"

        try:
            draft = scan_receipt_image(file_bytes, mime_type, request.user)
        except RuntimeError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(draft)


class CommitReceiptView(APIView):
    """
    POST multipart: image + title, note, category_id, date_effective, items (JSON string)
    → creates expense Transaction + TransactionItems atomically.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        ensure_finance_ready(request.user)
        # Build a plain dict — QueryDict.__setitem__ wraps values in a list and
        # breaks nested "items" JSON after json.loads.
        items_raw = request.data.get("items")
        if isinstance(items_raw, str):
            try:
                items = json.loads(items_raw)
            except json.JSONDecodeError as exc:
                raise ValidationError({"items": "Invalid JSON."}) from exc
        else:
            items = items_raw

        data = {
            "title": request.data.get("title", ""),
            "note": request.data.get("note", ""),
            "category_id": request.data.get("category_id"),
            "date_effective": request.data.get("date_effective") or None,
            "items": items,
        }
        # Drop empty optional date so DateField(required=False) is happy.
        if not data["date_effective"]:
            data.pop("date_effective")

        ser = CommitReceiptSerializer(data=data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data
        owner = request.user

        try:
            header_cat = Category.objects.get(
                pk=payload["category_id"], owner=owner, kind="expense"
            )
        except Category.DoesNotExist as exc:
            raise ValidationError({"category_id": "Invalid expense category."}) from exc

        item_cats = {}
        for row in payload["items"]:
            cid = row["category_id"]
            if cid not in item_cats:
                try:
                    item_cats[cid] = Category.objects.get(
                        pk=cid, owner=owner, kind="expense"
                    )
                except Category.DoesNotExist as exc:
                    raise ValidationError(
                        {"items": f"Invalid category_id {cid}."}
                    ) from exc

        effective = payload.get("date_effective") or today()
        merchant = (payload.get("title") or "").strip()
        note = payload.get("note") or ""
        if merchant and merchant not in note:
            note = f"{merchant}\n{note}".strip() if note else merchant

        upload = request.FILES.get("image")

        with db_transaction.atomic():
            txn = Transaction.objects.create(
                owner=owner,
                type="expense",
                amount=Decimal("0.00"),
                title=merchant or "Receipt",
                note=note,
                category=header_cat,
                date_created=today(),
                date_effective=effective,
            )
            if upload:
                txn.receipt_image.save(
                    upload.name, ContentFile(upload.read()), save=True
                )

            for row in payload["items"]:
                TransactionItem.objects.create(
                    owner=owner,
                    transaction=txn,
                    title=row["title"],
                    cost=row["cost"],
                    quantity=row["quantity"],
                    unit=row.get("unit") or "pcs",
                    category=item_cats[row["category_id"]],
                    date_created=today(),
                    date_effective=row.get("date_effective") or effective,
                )

            sync_expense_amount(txn)

        qs = annotate_transaction_amount(
            Transaction.objects.filter(pk=txn.pk)
        ).select_related("category")
        out = TransactionSerializer(qs.get(), context={"request": request})
        return Response(out.data, status=status.HTTP_201_CREATED)
