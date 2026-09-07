"""Standalone receipt / bank-slip scan and commit to Transaction(s)."""

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
    """POST multipart image → vision draft (retail or bank). Does not persist."""

    permission_classes = [IsAuthenticated, IsEmailVerified]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        ensure_finance_ready(request.user)
        upload = request.FILES.get("image")
        if not upload:
            raise ValidationError({"image": "Receipt image is required."})

        file_bytes = upload.read()
        mime_type = upload.content_type or "image/jpeg"

        llm_override = None
        provider = (request.data.get("llm_provider") or "").strip().lower()
        api_key = (request.data.get("llm_api_key") or "").strip()
        model = (request.data.get("llm_model") or "").strip()
        if provider or api_key or model:
            llm_override = {
                "provider": provider or None,
                "api_key": api_key or None,
                "model": model or None,
            }

        try:
            draft = scan_receipt_image(
                file_bytes, mime_type, request.user, llm_override=llm_override
            )
        except RuntimeError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(draft)


class CommitReceiptView(APIView):
    """
    POST multipart:
      retail_receipt — image + title, note, category_id, date_effective, items
        → expense Transaction + TransactionItems
      bank_slip — image + entries (JSON) → one or more income/transfer Transactions
    """

    permission_classes = [IsAuthenticated, IsEmailVerified]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        ensure_finance_ready(request.user)
        # Build a plain dict — QueryDict.__setitem__ wraps values in a list and
        # breaks nested JSON after json.loads.
        items_raw = request.data.get("items")
        if isinstance(items_raw, str):
            try:
                items = json.loads(items_raw) if items_raw else []
            except json.JSONDecodeError as exc:
                raise ValidationError({"items": "Invalid JSON."}) from exc
        else:
            items = items_raw if items_raw is not None else []

        entries_raw = request.data.get("entries")
        if isinstance(entries_raw, str):
            try:
                entries = json.loads(entries_raw) if entries_raw else []
            except json.JSONDecodeError as exc:
                raise ValidationError({"entries": "Invalid JSON."}) from exc
        else:
            entries = entries_raw if entries_raw is not None else []

        if isinstance(entries, list):
            cleaned_entries = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                row = dict(entry)
                if not row.get("date_effective"):
                    row.pop("date_effective", None)
                if row.get("category_id") in ("", None):
                    row.pop("category_id", None)
                cleaned_entries.append(row)
            entries = cleaned_entries

        data = {
            "document_kind": request.data.get("document_kind") or "retail_receipt",
            "title": request.data.get("title", ""),
            "merchant": request.data.get("merchant", ""),
            "note": request.data.get("note", ""),
            "items": items if isinstance(items, list) else [],
            "entries": entries if isinstance(entries, list) else [],
        }
        category_id = request.data.get("category_id")
        if category_id not in ("", None):
            data["category_id"] = category_id
        date_effective = request.data.get("date_effective")
        if date_effective:
            data["date_effective"] = date_effective

        ser = CommitReceiptSerializer(data=data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data
        owner = request.user
        upload = request.FILES.get("image")
        image_bytes = upload.read() if upload else None
        image_name = upload.name if upload else None

        if payload["document_kind"] == "bank_slip":
            created = self._commit_bank(
                owner, payload, image_bytes, image_name, request
            )
            return Response(
                {
                    "document_kind": "bank_slip",
                    "results": created,
                    # Convenience: first created txn (matches prior single-txn clients)
                    "id": created[0]["id"] if created else None,
                },
                status=status.HTTP_201_CREATED,
            )

        txn_data = self._commit_retail(owner, payload, image_bytes, image_name, request)
        return Response(
            {**txn_data, "document_kind": "retail_receipt"},
            status=status.HTTP_201_CREATED,
        )

    def _attach_image(self, txn, image_bytes, image_name):
        if image_bytes and image_name:
            txn.receipt_image.save(image_name, ContentFile(image_bytes), save=True)

    def _serialize_txn(self, txn_id, request):
        qs = annotate_transaction_amount(
            Transaction.objects.filter(pk=txn_id)
        ).select_related("category")
        return TransactionSerializer(qs.get(), context={"request": request}).data

    def _commit_retail(self, owner, payload, image_bytes, image_name, request):
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
        merchant = (payload.get("merchant") or payload.get("title") or "").strip()
        title = (payload.get("title") or merchant or "Receipt").strip()
        note = payload.get("note") or ""

        with db_transaction.atomic():
            txn = Transaction.objects.create(
                owner=owner,
                type="expense",
                amount=Decimal("0.00"),
                title=title,
                merchant=merchant,
                note=note,
                category=header_cat,
                date_created=today(),
                date_effective=effective,
            )
            self._attach_image(txn, image_bytes, image_name)

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
                )

            sync_expense_amount(txn)

        return self._serialize_txn(txn.pk, request)

    def _commit_bank(self, owner, payload, image_bytes, image_name, request):
        income_cats = {}
        for entry in payload["entries"]:
            if entry["txn_type"] != "income":
                continue
            cid = entry.get("category_id")
            if cid not in income_cats:
                try:
                    income_cats[cid] = Category.objects.get(
                        pk=cid, owner=owner, kind="income"
                    )
                except Category.DoesNotExist as exc:
                    raise ValidationError(
                        {"entries": f"Invalid income category_id {cid}."}
                    ) from exc

        created_ids = []
        with db_transaction.atomic():
            for entry in payload["entries"]:
                txn_type = entry["txn_type"]
                merchant = (entry.get("merchant") or "").strip()
                title = (
                    entry.get("title") or merchant or txn_type.replace("_", " ").title()
                ).strip()
                note = entry.get("note") or ""
                effective = entry.get("date_effective") or today()
                category = (
                    income_cats.get(entry.get("category_id"))
                    if txn_type == "income"
                    else None
                )
                txn = Transaction.objects.create(
                    owner=owner,
                    type=txn_type,
                    amount=entry["amount"],
                    title=title,
                    merchant=merchant,
                    note=note,
                    category=category,
                    date_created=today(),
                    date_effective=effective,
                )
                # Same cropped image on each imported row for audit trail.
                self._attach_image(txn, image_bytes, image_name)
                created_ids.append(txn.pk)

        return [self._serialize_txn(pk, request) for pk in created_ids]
