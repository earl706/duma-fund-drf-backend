"""Vision receipt / bank-slip extraction with category suggestions."""

import base64
import json
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings

from .models import MAX_EXPENSE_CATEGORIES, Category, UNIT_CHOICES
from .seeds import (
    ensure_finance_ready,
    get_default_expense_category,
    get_default_income_category,
)

VALID_UNITS = {choice[0] for choice in UNIT_CHOICES}
VALID_BANK_TXN_TYPES = {"income", "transfer_in", "transfer_out"}
VALID_DOCUMENT_KINDS = {"retail_receipt", "bank_slip"}
MAX_RECEIPT_BYTES = 10 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}


def _category_prompt_lines(categories):
    lines = []
    by_id = {c.id: c for c in categories}
    for cat in categories:
        if cat.parent_id:
            parent = by_id.get(cat.parent_id)
            label = f"{parent.name} > {cat.name}" if parent else cat.name
        else:
            label = cat.name
        lines.append(f'  {{"id": {cat.id}, "name": "{label}"}}')
    return "\n".join(lines) if lines else '  {"id": null, "name": "Other"}'


def build_receipt_prompt(expense_categories, income_categories, account_holder_name=""):
    expense_catalog = _category_prompt_lines(expense_categories)
    income_catalog = _category_prompt_lines(income_categories)
    holder = (account_holder_name or "").strip() or "(unknown — use Received/Sent cues)"
    return f"""Classify and extract data from this finance document image (Philippines).

Account holder name (this app user's profile): {holder}

Decide document_kind:
- "retail_receipt" — store / grocery / restaurant purchase receipt with product lines
- "bank_slip" — digital bank or e-wallet transfer screenshot, deposit slip, passbook page,
  InstaPay/PESONet confirmation, payroll credit, or similar bank movement proof

Return JSON only.

If retail_receipt:
{{
  "document_kind": "retail_receipt",
  "merchant": "store name or null",
  "date_effective": "YYYY-MM-DD or null",
  "category_id": <expense category id for the whole receipt (primary)>,
  "items": [
    {{
      "title": "product name",
      "cost": "7.00",
      "quantity": "12",
      "unit": "pcs",
      "category_id": <best matching expense category id (rolled up to receipt)>
    }}
  ]
}}

If bank_slip:
{{
  "document_kind": "bank_slip",
  "entries": [
    {{
      "txn_type": "income" | "transfer_in" | "transfer_out",
      "amount": "1500.00",
      "title": "counterparty · ref · account last4 (compose from available fields)",
      "merchant": "bank or e-wallet app name",
      "note": "purpose / description or empty string",
      "date_effective": "YYYY-MM-DD or null",
      "category_id": <income category id when txn_type is income, else null>,
      "recipient_name": "person/account the money was sent TO, or null",
      "sender_name": "person/account the money was sent FROM, or null"
    }}
  ]
}}

Available expense categories (retail_receipt only; use these ids only):
{expense_catalog}

Available income categories (bank_slip income rows only; use these ids only):
{income_catalog}

Retail rules:
- cost = UNIT PRICE (not line total)
- quantity = number of units purchased
- unit must be one of: pcs, kg, g, L, mL
- For items sold by piece/pack, use unit "pcs" even if the name includes weight/volume
- Only use kg/g/L/mL when sold by weight or volume on the receipt
- Skip subtotals, tax, payment, change, headers, and non-product lines
- Use decimal strings; cost with 2 decimal places
- Pick the closest category_id for the receipt and each line from the expense list
  (line categories are suggestions; they will be merged onto the receipt header)
- merchant = store / vendor name printed on the receipt

Bank / transfer rules:
- Extract EVERY visible money movement row (passbook pages and history screens may have many)
- Always fill recipient_name (TO) and sender_name (FROM) when printed on the document
- PRIMARY transfer direction (when not clear salary/payroll/interest/cashback income):
  Compare recipient_name to the account holder name above (allow minor spelling /
  middle-initial / order differences).
  - If money was sent TO the account holder → txn_type = "transfer_in"
  - If money was sent TO any other person/name → txn_type = "transfer_out"
- Only use Received / Sent / Credit / Debit cues when recipient_name is missing or the
  account holder name is unknown
- Salary / Payroll / Interest / Cashback / clear earnings → income (not transfer)
- Prefer income for clear earnings; prefer transfer_in/out for person-to-person moves
- amount = main transaction amount only (positive; skip standalone fee rows unless that is the only amount)
- title MUST include available counterparty/sender/recipient, reference/trace number, and
  masked account / last-4 when present (join with " · "; omit missing parts)
- merchant = bank, remittance house, or e-wallet app name (generic — any PH bank/app)
- note = purpose / remarks / message if shown; otherwise ""
- category_id required suggestion only when txn_type is income; null for transfers
- Use decimal strings with 2 decimal places for amount
- Skip headers, balances-only lines, and decorative UI chrome
"""


def _quantize_decimal(value, places=2):
    if value is None or value == "":
        return None
    text = str(value).strip()
    text = re.sub(r"(?i)\b(?:php|usd|eur|sgd)\b", "", text)
    text = re.sub(r"[₱$€£¥]", "", text).strip()
    text = text.replace(",", "")
    try:
        d = Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if d < 0:
        return None
    quant = Decimal("1") if places == 0 else Decimal("0." + "0" * (places - 1) + "1")
    return str(d.quantize(quant))


def _item_title(entry):
    for key in ("title", "name", "description", "product", "item"):
        title = str(entry.get(key) or "").strip()
        if title:
            return title[:255]
    return ""


def _item_cost(entry):
    for key in ("cost", "unit_price", "price", "amount"):
        if entry.get(key) is not None and entry.get(key) != "":
            cost = _quantize_decimal(entry.get(key))
            if cost is not None:
                return cost
    return None


def _item_quantity(entry):
    for key in ("quantity", "qty", "count"):
        if entry.get(key) is not None and entry.get(key) != "":
            quantity = _quantize_decimal(entry.get(key))
            if quantity is not None:
                return quantity
    return "1.00"


def _normalize_unit(value):
    if not value:
        return "pcs"
    unit = str(value).strip()
    aliases = {
        "pc": "pcs",
        "piece": "pcs",
        "pieces": "pcs",
        "liter": "L",
        "litre": "L",
        "ml": "mL",
        "milliliter": "mL",
        "millilitre": "mL",
        "kilogram": "kg",
        "gram": "g",
        "grams": "g",
    }
    unit = aliases.get(unit.lower(), unit)
    return unit if unit in VALID_UNITS else "pcs"


def _normalize_date(value):
    if not value:
        return None
    text = str(value).strip()
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return text[:10]
    match = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
    if not match:
        return None
    month, day, year = match.groups()
    if len(year) == 2:
        year = f"20{year}"
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _resolve_category_id(raw_id, valid_ids, fallback_id):
    try:
        cid = int(raw_id)
    except (TypeError, ValueError):
        return fallback_id
    return cid if cid in valid_ids else fallback_id


def _normalize_txn_type(value):
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "in": "transfer_in",
        "out": "transfer_out",
        "transfer": "transfer_in",
        "received": "transfer_in",
        "credit": "transfer_in",
        "deposit": "transfer_in",
        "incoming": "transfer_in",
        "sent": "transfer_out",
        "debit": "transfer_out",
        "withdrawal": "transfer_out",
        "outgoing": "transfer_out",
        "salary": "income",
        "payroll": "income",
    }
    mapped = aliases.get(raw, raw)
    return mapped if mapped in VALID_BANK_TXN_TYPES else "transfer_in"


def _normalize_person_name(value):
    text = re.sub(r"[^a-z0-9\s]", " ", str(value or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _names_match(left, right):
    """True if person names refer to the same account holder (soft PH-name match)."""
    a = _normalize_person_name(left)
    b = _normalize_person_name(right)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    tokens_a = {t for t in a.split() if len(t) > 1}
    tokens_b = {t for t in b.split() if len(t) > 1}
    if not tokens_a or not tokens_b:
        return False
    shorter, longer = (
        (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    )
    return shorter.issubset(longer)


def _infer_bank_txn_type(entry, account_holder_name):
    """
    Prefer recipient vs account-holder for transfer_in/out.
    Keep income when the model (or cues) mark clear earnings.
    """
    cue_type = _normalize_txn_type(entry.get("txn_type"))
    if cue_type == "income":
        return "income"

    holder = (account_holder_name or "").strip()
    recipient = str(entry.get("recipient_name") or "").strip()
    if holder and recipient:
        if _names_match(recipient, holder):
            return "transfer_in"
        return "transfer_out"

    return cue_type


def normalize_retail_payload(raw, categories, fallback_category_id):
    """Validate and normalize retail receipt JSON into API-safe draft."""
    valid_ids = {c.id for c in categories}
    date_effective = _normalize_date(raw.get("date_effective"))
    merchant = str(raw.get("merchant") or "").strip()[:255] or None
    header_category_id = _resolve_category_id(
        raw.get("category_id"), valid_ids, fallback_category_id
    )

    items_in = (
        raw.get("items")
        or raw.get("line_items")
        or raw.get("products")
        or raw.get("lines")
        or []
    )
    if not isinstance(items_in, list):
        raise ValueError("Receipt items must be a list.")

    items = []
    rolled_category_ids = []
    if header_category_id:
        rolled_category_ids.append(header_category_id)

    for entry in items_in:
        if not isinstance(entry, dict):
            continue
        title = _item_title(entry)
        if not title:
            continue
        cost = _item_cost(entry)
        quantity = _item_quantity(entry)
        if cost is None or quantity is None:
            continue
        line_cat = _resolve_category_id(
            entry.get("category_id"), valid_ids, header_category_id
        )
        if line_cat and line_cat not in rolled_category_ids:
            if len(rolled_category_ids) < MAX_EXPENSE_CATEGORIES:
                rolled_category_ids.append(line_cat)
        items.append(
            {
                "title": title,
                "cost": cost,
                "quantity": quantity,
                "unit": _normalize_unit(entry.get("unit")),
            }
        )

    if not items:
        raise ValueError("No line items could be extracted from the receipt.")

    if not rolled_category_ids and header_category_id:
        rolled_category_ids = [header_category_id]

    return {
        "document_kind": "retail_receipt",
        "merchant": merchant,
        "date_effective": date_effective,
        "category_id": rolled_category_ids[0] if rolled_category_ids else header_category_id,
        "category_ids": rolled_category_ids,
        "items": items,
    }


def normalize_bank_payload(
    raw, income_categories, fallback_income_category_id, account_holder_name=""
):
    """Validate and normalize bank / transfer JSON into API-safe draft."""
    valid_ids = {c.id for c in income_categories}
    entries_in = raw.get("entries") or []
    if not isinstance(entries_in, list):
        raise ValueError("Bank entries must be a list.")

    entries = []
    for entry in entries_in:
        if not isinstance(entry, dict):
            continue
        amount = _quantize_decimal(entry.get("amount"))
        if amount is None or Decimal(amount) <= 0:
            continue
        title = str(entry.get("title") or "").strip()
        merchant = str(entry.get("merchant") or "").strip()[:255]
        note = str(entry.get("note") or "").strip()
        if not title:
            title = merchant or "Bank transfer"
        txn_type = _infer_bank_txn_type(entry, account_holder_name)
        category_id = None
        if txn_type == "income":
            category_id = _resolve_category_id(
                entry.get("category_id"), valid_ids, fallback_income_category_id
            )
        entries.append(
            {
                "txn_type": txn_type,
                "amount": amount,
                "title": title[:255],
                "merchant": merchant,
                "note": note,
                "date_effective": _normalize_date(entry.get("date_effective")),
                "category_id": category_id,
            }
        )

    if not entries:
        raise ValueError("No transfers or income could be extracted from the image.")

    return {
        "document_kind": "bank_slip",
        "entries": entries,
    }


def normalize_receipt_payload(
    raw,
    expense_categories,
    expense_fallback_id,
    income_categories,
    income_fallback_id,
    account_holder_name="",
):
    """Validate and normalize model JSON into API-safe draft."""
    if not isinstance(raw, dict):
        raise ValueError("Receipt response must be a JSON object.")

    kind = str(raw.get("document_kind") or "").strip().lower()
    if kind not in VALID_DOCUMENT_KINDS:
        # Heuristic fallback when the model omits document_kind.
        if isinstance(raw.get("entries"), list) and raw.get("entries"):
            kind = "bank_slip"
        elif isinstance(raw.get("items"), list) and raw.get("items"):
            kind = "retail_receipt"
        else:
            raise ValueError(
                "Could not determine document kind (receipt vs bank slip)."
            )

    if kind == "bank_slip":
        return normalize_bank_payload(
            raw, income_categories, income_fallback_id, account_holder_name
        )
    return normalize_retail_payload(raw, expense_categories, expense_fallback_id)


def _validate_image(file_bytes, mime_type):
    if mime_type not in ALLOWED_MIME:
        raise ValueError("Upload a JPEG, PNG, or WebP receipt image.")
    if len(file_bytes) > MAX_RECEIPT_BYTES:
        raise ValueError("Receipt image must be 10 MB or smaller.")


def _parse_model_json(content):
    text = (content or "").strip()
    if not text:
        raise ValueError("Could not parse receipt response.")

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Could not parse receipt response.") from exc


def _resolve_provider(override=None):
    override = override or {}
    provider = (
        str(override.get("provider") or getattr(settings, "RECEIPT_PROVIDER", "openai"))
        .strip()
        .lower()
    )
    if provider not in {"openai", "gemini"}:
        raise RuntimeError(
            f"Invalid RECEIPT_PROVIDER '{provider}' (use openai or gemini)."
        )

    api_key = (override.get("api_key") or "").strip()
    if provider == "openai":
        if not api_key:
            api_key = getattr(settings, "OPENAI_API_KEY", "") or ""
        if not api_key:
            raise RuntimeError(
                "OpenAI is not configured. Add an API key in Settings or set OPENAI_API_KEY."
            )
    else:
        if not api_key:
            api_key = getattr(settings, "GEMINI_API_KEY", "") or ""
        if not api_key:
            raise RuntimeError(
                "Gemini is not configured. Add an API key in Settings or set GEMINI_API_KEY."
            )

    if provider == "openai":
        model = (override.get("model") or "").strip() or getattr(
            settings, "OPENAI_RECEIPT_MODEL", "gpt-4o-mini"
        )
    else:
        model = (override.get("model") or "").strip() or getattr(
            settings, "GEMINI_RECEIPT_MODEL", "gemini-3.5-flash"
        )

    return provider, api_key, model


def _scan_openai(file_bytes, mime_type, prompt, api_key, model):
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    b64 = base64.b64encode(file_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{b64}"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    return _parse_model_json(response.choices[0].message.content)


# Prefer current Flash IDs; keep older aliases as last resorts.
GEMINI_MODEL_FALLBACKS = (
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-2.5-flash",
)


def _gemini_model_candidates(preferred):
    preferred = (preferred or "").strip()
    ordered = []
    if preferred:
        ordered.append(preferred)
    for model in GEMINI_MODEL_FALLBACKS:
        if model not in ordered:
            ordered.append(model)
    return ordered


def _scan_gemini(file_bytes, mime_type, prompt, api_key, model):
    import time

    from google import genai
    from google.genai import types
    from google.genai.errors import ClientError, ServerError

    client = genai.Client(api_key=api_key)
    contents = [
        types.Part.from_text(text=prompt),
        types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
    ]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0,
    )

    last_server_error = None
    last_missing = None
    for candidate in _gemini_model_candidates(model):
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=candidate,
                    contents=contents,
                    config=config,
                )
                return _parse_model_json(response.text)
            except ClientError as exc:
                if exc.code == 404:
                    last_missing = candidate
                    break  # try next model
                raise ValueError(f"Gemini request failed: {exc}") from exc
            except ServerError as exc:
                last_server_error = (candidate, exc)
                if attempt == 0:
                    time.sleep(0.8)
                    continue
                break  # try next model

    if last_missing and not last_server_error:
        raise ValueError(
            f"Gemini model '{model}' is not available. "
            "Try gemini-3.5-flash in Settings or GEMINI_RECEIPT_MODEL."
        )
    failed_model = last_server_error[0] if last_server_error else model
    raise RuntimeError(
        f"Gemini is temporarily unavailable (model '{failed_model}' returned 503). "
        "Retry shortly, or switch model in Settings (e.g. gemini-3.5-flash)."
    )


def scan_receipt_image(file_bytes, mime_type, user, llm_override=None, profile=None):
    """Call vision provider; return normalized draft (retail or bank)."""
    _validate_image(file_bytes, mime_type)
    if profile is None:
        profile = ensure_finance_ready(user)
    expense_categories = list(
        Category.objects.filter(profile=profile, kind="expense").select_related(
            "parent"
        )
    )
    income_categories = list(
        Category.objects.filter(profile=profile, kind="income").select_related(
            "parent"
        )
    )
    expense_fallback = get_default_expense_category(profile)
    income_fallback = get_default_income_category(profile)
    expense_fallback_id = expense_fallback.id if expense_fallback else None
    income_fallback_id = income_fallback.id if income_fallback else None
    if expense_fallback_id is None:
        raise RuntimeError("No expense categories available.")
    if income_fallback_id is None:
        raise RuntimeError("No income categories available.")

    account_holder_name = (getattr(user, "full_name", None) or "").strip()
    prompt = build_receipt_prompt(
        expense_categories, income_categories, account_holder_name
    )
    provider, api_key, model = _resolve_provider(llm_override)

    if provider == "gemini":
        parsed = _scan_gemini(file_bytes, mime_type, prompt, api_key, model)
    else:
        parsed = _scan_openai(file_bytes, mime_type, prompt, api_key, model)

    return normalize_receipt_payload(
        parsed,
        expense_categories,
        expense_fallback_id,
        income_categories,
        income_fallback_id,
        account_holder_name=account_holder_name,
    )
