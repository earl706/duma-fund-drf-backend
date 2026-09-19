"""Standalone receipt / bank-slip scan and commit to Transaction(s)."""

import json
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import transaction as db_transaction
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from .models import Category, Transaction, TransactionItem, today
from .receipt_scan import scan_receipt_image
from .scope import LEDGER_WRITE_ROLES, ProfileScopedAPIView
from .serializers import (
    BulkCommitReceiptSerializer,
    CommitReceiptSerializer,
    MAX_BULK_RECEIPT_COMMITS,
    TransactionSerializer,
    apply_expense_categories,
    sync_expense_amount,
)
from .views import annotate_transaction_amount


def _loads_json_field(raw, field_name):
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw else []
        except json.JSONDecodeError as exc:
            raise ValidationError({field_name: "Invalid JSON."}) from exc
    return raw


def parse_commit_payload(data):
    """
    Build a plain dict for CommitReceiptSerializer from request.data-like mapping.
    Avoids QueryDict list-wrapping issues after nested JSON loads.
    """
    items_raw = _loads_json_field(data.get("items"), "items")
    entries_raw = _loads_json_field(data.get("entries"), "entries")

    items = items_raw if isinstance(items_raw, list) else []
    entries = entries_raw if isinstance(entries_raw, list) else []

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

    payload = {
        "document_kind": data.get("document_kind") or "retail_receipt",
        "title": data.get("title", ""),
        "merchant": data.get("merchant", ""),
        "note": data.get("note", ""),
        "items": items,
        "entries": entries,
    }
    category_id = data.get("category_id")
    if category_id not in ("", None):
        payload["category_id"] = category_id
    category_ids_raw = data.get("category_ids")
    if isinstance(category_ids_raw, str):
        try:
            category_ids = json.loads(category_ids_raw) if category_ids_raw else None
        except json.JSONDecodeError as exc:
            raise ValidationError({"category_ids": "Invalid JSON."}) from exc
    else:
        category_ids = category_ids_raw
    if isinstance(category_ids, list) and category_ids:
        payload["category_ids"] = category_ids
    date_effective = data.get("date_effective")
    if date_effective:
        payload["date_effective"] = date_effective
    return payload


def llm_override_from_request(request):
    provider = (request.data.get("llm_provider") or "").strip().lower()
    api_key = (request.data.get("llm_api_key") or "").strip()
    model = (request.data.get("llm_model") or "").strip()
    if not (provider or api_key or model):
        return None
    return {
        "provider": provider or None,
        "api_key": api_key or None,
        "model": model or None,
    }


def collect_bulk_images(request, count):
    """Return list of (bytes, mime, name) for image_0…image_{count-1}."""
    images = []
    for index in range(count):
        upload = request.FILES.get(f"image_{index}") or request.FILES.get(
            f"image{index}"
        )
        if not upload:
            raise ValidationError({f"image_{index}": "Image is required."})
        images.append(
            (
                upload.read(),
                upload.content_type or "image/jpeg",
                upload.name or f"receipt-{index}.jpg",
            )
        )
    return images


class ReceiptScanView(ProfileScopedAPIView):
    """POST multipart image → vision draft (retail or bank). Does not persist."""

    parser_classes = [MultiPartParser, FormParser]
    write_roles = LEDGER_WRITE_ROLES

    def post(self, request):
        self.assert_write()
        upload = request.FILES.get("image")
        if not upload:
            raise ValidationError({"image": "Receipt image is required."})

        file_bytes = upload.read()
        mime_type = upload.content_type or "image/jpeg"
        llm_override = llm_override_from_request(request)

        try:
            draft = scan_receipt_image(
                file_bytes,
                mime_type,
                request.user,
                llm_override=llm_override,
                profile=self.get_profile(),
            )
        except RuntimeError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(draft)


class BulkScanReceiptView(ProfileScopedAPIView):
    """
    POST multipart (max 10): image_0…image_{n-1} + optional llm_* → OCR drafts.
    Partial success: each index returns ok + draft, or ok=false + detail.
    Does not persist.
    """

    parser_classes = [MultiPartParser, FormParser]
    write_roles = LEDGER_WRITE_ROLES

    def post(self, request):
        self.assert_write()
        count_raw = request.data.get("count")
        try:
            count = int(count_raw) if count_raw not in (None, "") else 0
        except (TypeError, ValueError) as exc:
            raise ValidationError({"count": "Must be an integer."}) from exc

        if count < 1:
            # Infer from uploaded files when count omitted.
            count = 0
            while (
                request.FILES.get(f"image_{count}")
                or request.FILES.get(f"image{count}")
            ) and count < MAX_BULK_RECEIPT_COMMITS:
                count += 1

        if count < 1:
            raise ValidationError({"images": "At least one image is required."})
        if count > MAX_BULK_RECEIPT_COMMITS:
            raise ValidationError(
                {"images": (f"At most {MAX_BULK_RECEIPT_COMMITS} images per batch.")}
            )

        images = collect_bulk_images(request, count)
        llm_override = llm_override_from_request(request)
        results = []
        ok_count = 0
        for index, (file_bytes, mime_type, _name) in enumerate(images):
            try:
                draft = scan_receipt_image(
                    file_bytes,
                    mime_type,
                    request.user,
                    llm_override=llm_override,
                    profile=self.get_profile(),
                )
                kind = draft.get("document_kind") or (
                    "bank_slip" if draft.get("entries") else "retail_receipt"
                )
                if kind == "bank_slip":
                    if not draft.get("entries"):
                        results.append(
                            {
                                "index": index,
                                "ok": False,
                                "detail": "No transfers or income found on the image.",
                            }
                        )
                        continue
                elif not draft.get("items"):
                    results.append(
                        {
                            "index": index,
                            "ok": False,
                            "detail": "No items found on the receipt.",
                        }
                    )
                    continue
                results.append({"index": index, "ok": True, "draft": draft})
                ok_count += 1
            except RuntimeError as exc:
                results.append({"index": index, "ok": False, "detail": str(exc)})
            except ValueError as exc:
                results.append({"index": index, "ok": False, "detail": str(exc)})
            except Exception as exc:  # noqa: BLE001 — isolate per-image failures
                results.append(
                    {
                        "index": index,
                        "ok": False,
                        "detail": str(exc) or "Could not scan image.",
                    }
                )

        return Response(
            {
                "results": results,
                "summary": {
                    "image_count": count,
                    "ok_count": ok_count,
                    "error_count": count - ok_count,
                },
            }
        )


class CommitReceiptMixin:
    """Shared retail / bank persistence for single and bulk commit views."""

    def _attach_image(self, txn, image_bytes, image_name):
        if image_bytes and image_name:
            txn.receipt_image.save(image_name, ContentFile(image_bytes), save=True)

    def _serialize_txn(self, txn_id, request):
        qs = (
            annotate_transaction_amount(Transaction.objects.filter(pk=txn_id))
            .select_related("category")
            .prefetch_related("categories")
        )
        return TransactionSerializer(qs.get(), context={"request": request}).data

    def _commit_retail(self, owner, profile, payload, image_bytes, image_name, request):
        category_ids = payload["category_ids"]
        cats = {
            c.id: c
            for c in Category.objects.filter(
                profile=profile, kind="expense", pk__in=category_ids
            )
        }
        missing = [cid for cid in category_ids if cid not in cats]
        if missing:
            raise ValidationError(
                {"category_ids": f"Invalid expense category id(s): {missing}."}
            )
        header_cat = cats[category_ids[0]]

        effective = payload.get("date_effective") or today()
        merchant = (payload.get("merchant") or payload.get("title") or "").strip()
        title = (payload.get("title") or merchant or "Receipt").strip()
        note = payload.get("note") or ""

        with db_transaction.atomic():
            txn = Transaction.objects.create(
                owner=owner,
                profile=profile,
                type="expense",
                amount=Decimal("0.00"),
                title=title,
                merchant=merchant,
                note=note,
                category=header_cat,
                date_created=today(),
                date_effective=effective,
            )
            apply_expense_categories(txn, category_ids, profile=profile)
            self._attach_image(txn, image_bytes, image_name)

            for row in payload["items"]:
                TransactionItem.objects.create(
                    owner=owner,
                    transaction=txn,
                    title=row["title"],
                    cost=row["cost"],
                    quantity=row["quantity"],
                    unit=row.get("unit") or "pcs",
                    date_created=today(),
                )

            sync_expense_amount(txn)

        return self._serialize_txn(txn.pk, request)

    def _commit_bank(self, owner, profile, payload, image_bytes, image_name, request):
        income_cats = {}
        for entry in payload["entries"]:
            if entry["txn_type"] != "income":
                continue
            cid = entry.get("category_id")
            if cid not in income_cats:
                try:
                    income_cats[cid] = Category.objects.get(
                        pk=cid, profile=profile, kind="income"
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
                    profile=profile,
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

    def _commit_one(self, owner, profile, payload, image_bytes, image_name, request):
        if payload["document_kind"] == "bank_slip":
            created = self._commit_bank(
                owner, profile, payload, image_bytes, image_name, request
            )
            return {
                "document_kind": "bank_slip",
                "results": created,
                "id": created[0]["id"] if created else None,
            }
        txn_data = self._commit_retail(
            owner, profile, payload, image_bytes, image_name, request
        )
        return {**txn_data, "document_kind": "retail_receipt"}


class CommitReceiptView(CommitReceiptMixin, ProfileScopedAPIView):
    """
    POST multipart:
      retail_receipt — image + title, note, category_id / category_ids, date_effective, items
        → expense Transaction + TransactionItems (categories on header only)
      bank_slip — image + entries (JSON) → one or more income/transfer Transactions
    """

    parser_classes = [MultiPartParser, FormParser, JSONParser]
    write_roles = LEDGER_WRITE_ROLES

    def post(self, request):
        self.assert_write()
        data = parse_commit_payload(request.data)
        ser = CommitReceiptSerializer(data=data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data
        upload = request.FILES.get("image")
        image_bytes = upload.read() if upload else None
        image_name = upload.name if upload else None
        result = self._commit_one(
            request.user,
            self.get_profile(),
            payload,
            image_bytes,
            image_name,
            request,
        )
        return Response(result, status=status.HTTP_201_CREATED)


class BulkCommitReceiptView(CommitReceiptMixin, ProfileScopedAPIView):
    """
    POST multipart (max 10):
      receipts — JSON array of CommitReceiptSerializer fields (mixed retail + bank OK)
      image_0 … image_{n-1} — optional cropped image per receipt index
    Atomically creates all transactions; one failure rolls back the batch.
    """

    parser_classes = [MultiPartParser, FormParser, JSONParser]
    write_roles = LEDGER_WRITE_ROLES

    def post(self, request):
        self.assert_write()
        receipts_raw = _loads_json_field(request.data.get("receipts"), "receipts")
        if not isinstance(receipts_raw, list):
            raise ValidationError({"receipts": "Expected a JSON array."})
        if not receipts_raw:
            raise ValidationError({"receipts": "At least one receipt is required."})
        if len(receipts_raw) > MAX_BULK_RECEIPT_COMMITS:
            raise ValidationError(
                {
                    "receipts": (
                        f"At most {MAX_BULK_RECEIPT_COMMITS} receipts per batch."
                    )
                }
            )

        parsed = [parse_commit_payload(row) for row in receipts_raw]
        ser = BulkCommitReceiptSerializer(data={"receipts": parsed})
        ser.is_valid(raise_exception=True)
        payloads = ser.validated_data["receipts"]

        images = []
        for index in range(len(payloads)):
            upload = request.FILES.get(f"image_{index}") or request.FILES.get(
                f"image{index}"
            )
            if upload:
                images.append((upload.read(), upload.name))
            else:
                images.append((None, None))

        owner = request.user
        profile = self.get_profile()
        results = []
        with db_transaction.atomic():
            for index, payload in enumerate(payloads):
                image_bytes, image_name = images[index]
                results.append(
                    self._commit_one(
                        owner, profile, payload, image_bytes, image_name, request
                    )
                )

        expense_count = sum(
            1 for r in results if r.get("document_kind") == "retail_receipt"
        )
        bank_txn_count = sum(
            len(r.get("results") or [])
            for r in results
            if r.get("document_kind") == "bank_slip"
        )
        return Response(
            {
                "results": results,
                "summary": {
                    "receipt_count": len(results),
                    "expense_count": expense_count,
                    "bank_txn_count": bank_txn_count,
                    "total_transactions": expense_count + bank_txn_count,
                },
            },
            status=status.HTTP_201_CREATED,
        )
