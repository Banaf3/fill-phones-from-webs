"""Header alias registry and flexible column mapping.

Maps source CSV headers to canonical field names using exact matches,
aliases, and fuzzy comparison. Restricts fuzzy mapping for critical fields.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from deliverect_sync.logging_config import get_logger
from deliverect_sync.models import MappingStatus

logger = get_logger("header_mapper")

# Critical fields that should never be fuzzy-mapped without high confidence/review
CRITICAL_FIELDS = {
    "deliverect_order_id",
    "channel_order_id",
    "receipt_id",
    "external_order_id",
    "order_id",
    "business_key",
    "order_total",
    "subtotal",
    "delivery_fee",
    "service_charge",
    "tip",
    "tax_total",
    "discount_total",
    "unit_price",
    "line_total",
}

# Canonical field name → set of known aliases (lowercase, stripped)
HEADER_ALIASES: dict[str, set[str]] = {
    "deliverect_order_id": {
        "deliverect order id",
        "deliverect id",
    },
    "channel_order_id": {
        "channel order id",
        "channel order number",
        "رقم طلب المنصة",
    },
    "receipt_id": {
        "receipt id",
        "pos receipt id",
        "receipt_id",
        "receiptid",
        "رقم الإيصال",
    },
    "external_order_id": {
        "external order id",
        "external id",
    },
    "order_id": {
        "order id",
        "orderid",
        "order_id",
        "رقم الطلب",
    },
    "pickup_time": {
        "pickup time",
        "pick-up time",
        "collection time",
        "pickup_time",
        "pickuptime",
        "وقت الاستلام",
    },
    "location": {
        "location",
        "branch",
        "store",
        "store name",
        "location name",
        "الموقع",
        "الفرع",
    },
    "channel": {
        "channel",
        "platform",
        "source",
        "order channel",
        "القناة",
        "المنصة",
    },
    "delivery_system": {
        "delivery system",
        "delivery",
        "delivery provider",
        "نظام التوصيل",
    },
    "order_status": {
        "status",
        "order status",
        "الحالة",
    },
    "payment_method": {
        "payment method",
        "payment type",
        "payment",
        "طريقة الدفع",
    },
    "payment_status": {
        "payment status",
        "payment state",
        "حالة الدفع",
        "paid",
        "is paid",
        "مدفوع",
    },
    "item_name": {
        "items",
        "item",
        "item name",
        "product",
        "product name",
        "العناصر",
        "المنتج",
    },
    "plu": {
        "plu",
        "sku",
        "product code",
        "item code",
    },
    "quantity": {
        "quantity",
        "qty",
        "count",
        "الكمية",
    },
    "order_total": {
        "total",
        "order total",
        "amount",
        "grand total",
        "الإجمالي",
        "المبلغ",
    },
    "subtotal": {
        "subtotal",
        "sub total",
        "المجموع الفرعي",
    },
    "delivery_fee": {
        "delivery fee",
        "delivery fees",
        "رسوم التوصيل",
    },
    "service_charge": {
        "service charge",
        "service fee",
        "رسوم الخدمة",
    },
    "tip": {
        "tip",
        "gratuity",
        "إكرامية",
    },
    "tax_total": {
        "tax",
        "tax total",
        "vat",
        "الضريبة",
    },
    "discount_total": {
        "discount",
        "discount total",
        "الخصم",
    },
    "unit_price": {
        "unit price",
        "price",
        "item price",
        "السعر",
    },
    "line_total": {
        "line total",
        "item total",
        "إجمالي الصنف",
    },
    "currency": {
        "currency",
        "العملة",
    },
    "customer_name": {
        "customer name",
        "customer",
        "name",
        "اسم العميل",
    },
    "customer_phone": {
        "customer phone",
        "phone",
        "phone number",
        "mobile",
        "telephone",
        "رقم الهاتف",
    },
    "customer_email": {
        "customer email",
        "email",
        "البريد الإلكتروني",
    },
    "delivery_address": {
        "delivery address",
        "address",
        "العنوان",
    },
    "item_notes": {
        "notes",
        "item notes",
        "order notes",
        "ملاحظات",
    },
}

# Build reverse lookup: alias → canonical name
_REVERSE_LOOKUP: dict[str, str] = {}
for canonical, aliases in HEADER_ALIASES.items():
    for alias in aliases:
        _REVERSE_LOOKUP[alias.lower()] = canonical


def normalize_header(header: str) -> str:
    """Normalize a header string for comparison.

    Strips whitespace, lowercases, normalizes Unicode,
    and removes common punctuation.
    """
    header = unicodedata.normalize("NFKC", header)
    header = header.strip().lower()
    header = re.sub(r"[_\-\s]+", " ", header)
    return header.strip()


class FieldMapping:
    def __init__(self, source_header: str, canonical_field: str | None, mapping_method: str, confidence: float, status: MappingStatus, approved_by_user: int = 1):
        self.source_header = source_header
        self.canonical_field = canonical_field
        self.mapping_method = mapping_method
        self.confidence = confidence
        self.status = status
        self.approved_by_user = approved_by_user


class HeaderMapper:
    """Maps source CSV headers to canonical field names."""

    def __init__(self) -> None:
        self._mappings: list[FieldMapping] = []
        self._original_headers: list[str] = []
        self._unmapped_headers: list[str] = []

    @property
    def mappings(self) -> list[FieldMapping]:
        return self._mappings

    @property
    def mapped_count(self) -> int:
        return sum(1 for m in self._mappings if m.status == MappingStatus.EXACT or m.status == MappingStatus.ALIAS or m.status == MappingStatus.FUZZY)

    def map_headers(self, source_headers: list[str]) -> list[FieldMapping]:
        self._original_headers = source_headers
        self._mappings = []
        self._unmapped_headers = []

        used_canonicals: set[str] = set()

        for header in source_headers:
            mapping = self._map_single(header, used_canonicals)
            self._mappings.append(mapping)

            if mapping.status in (MappingStatus.EXACT, MappingStatus.ALIAS, MappingStatus.FUZZY) and mapping.canonical_field:
                used_canonicals.add(mapping.canonical_field)
            elif mapping.status == MappingStatus.UNMAPPED:
                self._unmapped_headers.append(header)

        return self._mappings

    def _map_single(self, header: str, used: set[str]) -> FieldMapping:
        normalized = normalize_header(header)

        # 1. Exact reverse lookup (ALIAS)
        if normalized in _REVERSE_LOOKUP:
            canonical = _REVERSE_LOOKUP[normalized]
            if canonical not in used:
                method = "EXACT" if normalized == canonical.replace("_", " ") else "ALIAS"
                status = MappingStatus.EXACT if method == "EXACT" else MappingStatus.ALIAS
                return FieldMapping(header, canonical, method, 1.0, status)

        # 2. Exact Canonical check
        for canonical in HEADER_ALIASES:
            if normalized == canonical.replace("_", " ") and canonical not in used:
                return FieldMapping(header, canonical, "EXACT", 1.0, MappingStatus.EXACT)

        # 3. Fuzzy match (only if not critical)
        for canonical, aliases in HEADER_ALIASES.items():
            if canonical in used:
                continue
            for alias in aliases:
                if alias in normalized or normalized in alias:
                    if canonical in CRITICAL_FIELDS:
                        # Ambiguous critical field mapping requires review
                        return FieldMapping(header, canonical, "FUZZY_CRITICAL", 0.5, MappingStatus.REQUIRED_REVIEW, 0)
                    return FieldMapping(header, canonical, "FUZZY", 0.8, MappingStatus.FUZZY)

        return FieldMapping(header, None, "NONE", 0.0, MappingStatus.UNMAPPED)

    def get_canonical_index(self, canonical_field: str) -> int | None:
        for i, mapping in enumerate(self._mappings):
            if mapping.canonical_field == canonical_field and mapping.status in (MappingStatus.EXACT, MappingStatus.ALIAS, MappingStatus.FUZZY):
                return i
        return None

    def get_column_name(self, canonical_field: str) -> str | None:
        for mapping in self._mappings:
            if mapping.canonical_field == canonical_field and mapping.status in (MappingStatus.EXACT, MappingStatus.ALIAS, MappingStatus.FUZZY):
                return mapping.source_header
        return None
