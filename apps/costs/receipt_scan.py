"""Vision receipt extraction for CostItem drafts (OpenAI or Gemini)."""

import base64
import json
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings

from .models import UNIT_CHOICES

VALID_UNITS = {choice[0] for choice in UNIT_CHOICES}
MAX_RECEIPT_BYTES = 10 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}

RECEIPT_PROMPT = """Extract purchasable line items from this retail receipt image (Philippines).

Return JSON only:
{
  "date_effective": "YYYY-MM-DD or null",
  "items": [
    {"title": "product name", "cost": "7.00", "quantity": "12", "unit": "pcs"}
  ]
}

Rules:
- cost = UNIT PRICE (not line total)
- quantity = number of units purchased
- unit must be one of: pcs, kg, g, L, mL
- For items sold by piece/pack, use unit "pcs" even if the name includes weight/volume (e.g. "65G-..." or "200ML-...")
- Only use kg/g/L/mL when sold by weight or volume on the receipt
- Skip subtotals, tax, payment, change, headers, and non-product lines
- Use decimal strings; cost with 2 decimal places
"""


def _quantize_decimal(value, places=2):
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if d < 0:
        return None
    quant = Decimal("1") if places == 0 else Decimal("0." + "0" * (places - 1) + "1")
    return str(d.quantize(quant))


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


def normalize_receipt_payload(raw):
    """Validate and normalize model JSON into API-safe draft items."""
    if not isinstance(raw, dict):
        raise ValueError("Receipt response must be a JSON object.")

    date_effective = _normalize_date(raw.get("date_effective"))
    items_in = raw.get("items") or []
    if not isinstance(items_in, list):
        raise ValueError("Receipt items must be a list.")

    items = []
    for entry in items_in:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        cost = _quantize_decimal(entry.get("cost"))
        quantity = _quantize_decimal(entry.get("quantity"))
        if cost is None or quantity is None:
            continue
        items.append(
            {
                "title": title[:255],
                "cost": cost,
                "quantity": quantity,
                "unit": _normalize_unit(entry.get("unit")),
            }
        )

    if not items:
        raise ValueError("No line items could be extracted from the receipt.")

    return {"date_effective": date_effective, "items": items}


def _validate_image(file_bytes, mime_type):
    if mime_type not in ALLOWED_MIME:
        raise ValueError("Upload a JPEG, PNG, or WebP receipt image.")
    if len(file_bytes) > MAX_RECEIPT_BYTES:
        raise ValueError("Receipt image must be 10 MB or smaller.")


def _parse_model_json(content):
    try:
        return json.loads(content or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Could not parse receipt response.") from exc


def _resolve_provider():
    provider = getattr(settings, "RECEIPT_PROVIDER", "openai").lower()
    if provider not in {"openai", "gemini"}:
        raise RuntimeError(
            f"Invalid RECEIPT_PROVIDER '{provider}' (use openai or gemini)."
        )
    if provider == "openai" and not getattr(settings, "OPENAI_API_KEY", None):
        raise RuntimeError("OpenAI is not configured (OPENAI_API_KEY missing).")
    if provider == "gemini" and not getattr(settings, "GEMINI_API_KEY", None):
        raise RuntimeError("Gemini is not configured (GEMINI_API_KEY missing).")
    return provider


def _scan_openai(file_bytes, mime_type):
    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    model = getattr(settings, "OPENAI_RECEIPT_MODEL", "gpt-4o-mini")
    b64 = base64.b64encode(file_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{b64}"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": RECEIPT_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    return _parse_model_json(response.choices[0].message.content)


def _scan_gemini(file_bytes, mime_type):
    from google import genai
    from google.genai import types
    from google.genai.errors import ClientError, ServerError

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    model = getattr(settings, "GEMINI_RECEIPT_MODEL", "gemini-3.6-flash")

    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_text(text=RECEIPT_PROMPT),
                types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
    except ClientError as exc:
        if exc.code == 404:
            raise ValueError(
                f"Gemini model '{model}' is not available. "
                "Set GEMINI_RECEIPT_MODEL=gemini-3.6-flash in your environment."
            ) from exc
        raise ValueError(f"Gemini request failed: {exc}") from exc
    except ServerError as exc:
        raise RuntimeError("Gemini is temporarily unavailable.") from exc

    return _parse_model_json(response.text)


def scan_receipt_image(file_bytes, mime_type):
    """Call the configured vision provider and return normalized draft items."""
    _validate_image(file_bytes, mime_type)
    provider = _resolve_provider()

    if provider == "gemini":
        parsed = _scan_gemini(file_bytes, mime_type)
    else:
        parsed = _scan_openai(file_bytes, mime_type)

    return normalize_receipt_payload(parsed)
