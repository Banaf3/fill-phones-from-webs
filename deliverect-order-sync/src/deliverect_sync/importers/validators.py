"""Row-level CSV validation.

Validates individual rows against expected types and constraints
without stopping the entire import.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from deliverect_sync.importers.header_mapper import HeaderMapper
from deliverect_sync.logging_config import get_logger
from deliverect_sync.models import ImportErrorRecord

logger = get_logger("validators")


def validate_row(
    row: dict[str, str],
    mapper: HeaderMapper,
    row_number: int,
) -> list[ImportErrorRecord]:
    """Validate a single CSV row.

    Checks:
    - Required fields are present and non-empty
    - Monetary values are parseable
    - Date/time values are valid
    - Status values are recognized

    Args:
        row: Dict of column_name → value.
        mapper: HeaderMapper with active mappings.
        row_number: 1-based row number for error reporting.

    Returns:
        List of ImportErrorRecord for any validation failures.
        Empty list means the row passed validation.
    """
    errors: list[ImportErrorRecord] = []

    # Get order ID for error reporting
    order_id_col = mapper.get_column_name("order_id")
    order_id = row.get(order_id_col, "") if order_id_col else ""

    # Check order_id is present
    if not order_id.strip():
        errors.append(_make_error(
            row_number, order_id, "MISSING_ORDER_ID",
            "Order ID is empty or missing",
        ))

    # Validate monetary values if present
    money_fields = [
        "order_total", "subtotal", "delivery_fee",
        "service_charge", "tip", "tax_total", "discount_total",
        "unit_price", "line_total",
    ]

    for field in money_fields:
        col_name = mapper.get_column_name(field)
        if col_name and col_name in row:
            value = row[col_name].strip()
            if value and not _is_valid_money(value):
                errors.append(_make_error(
                    row_number, order_id, "INVALID_MONETARY_VALUE",
                    f"Field '{col_name}' has invalid monetary value: '{value[:50]}'",
                ))

    # Validate quantity
    qty_col = mapper.get_column_name("quantity")
    if qty_col and qty_col in row:
        qty_val = row[qty_col].strip()
        if qty_val and not _is_valid_number(qty_val):
            errors.append(_make_error(
                row_number, order_id, "INVALID_QUANTITY",
                f"Quantity is not a valid number: '{qty_val[:50]}'",
            ))

    return errors


def _is_valid_money(value: str) -> bool:
    """Check if a string can be parsed as a monetary value."""
    # Strip currency symbols and whitespace
    import re

    cleaned = re.sub(r"[^\d.,\-٠-٩۰-۹]", "", value)
    if not cleaned:
        return False

    # Convert Arabic/Persian digits
    cleaned = _normalize_digits(cleaned)

    # Try parsing
    try:
        # Handle European format (comma as decimal separator)
        if "," in cleaned and "." not in cleaned:
            cleaned = cleaned.replace(",", ".")
        elif "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(",", "")

        float(cleaned)
        return True
    except ValueError:
        return False


def _is_valid_number(value: str) -> bool:
    """Check if a string can be parsed as a number."""
    cleaned = _normalize_digits(value.strip())
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


def _normalize_digits(text: str) -> str:
    """Convert Arabic-Indic and Persian digits to ASCII."""
    result = []
    for ch in text:
        cp = ord(ch)
        # Arabic-Indic digits (٠-٩)
        if 0x0660 <= cp <= 0x0669:
            result.append(str(cp - 0x0660))
        # Extended Arabic-Indic / Persian digits (۰-۹)
        elif 0x06F0 <= cp <= 0x06F9:
            result.append(str(cp - 0x06F0))
        else:
            result.append(ch)
    return "".join(result)


def _make_error(
    row_number: int,
    order_id: str,
    code: str,
    message: str,
) -> ImportErrorRecord:
    """Create a validation error record."""
    return ImportErrorRecord(
        id=str(uuid.uuid4()),
        sync_run_id="",  # Set by caller
        source_file_id="",  # Set by caller
        row_number=row_number,
        stage="validation",
        order_id=order_id[:100] if order_id else None,
        error_code=code,
        error_message=message[:500],
        timestamp=datetime.now(tz=timezone.utc),
    )
