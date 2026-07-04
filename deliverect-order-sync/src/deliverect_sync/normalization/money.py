"""Money parsing and formatting.

Handles currency extraction and standardizing decimal separators.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def parse_money(value: str | None) -> Decimal | None:
    """Parse a money string into a Decimal."""
    if not value:
        return None
        
    value = str(value).strip()
    if not value:
        return None

    # Remove currency symbols and common non-numeric chars except . and , and -
    cleaned = re.sub(r"[^\d.,\-٠-٩۰-۹]", "", value)
    if not cleaned:
        return None

    # Convert Arabic/Persian digits
    cleaned = _normalize_digits(cleaned)

    try:
        # Handle European format (comma as decimal separator)
        if "," in cleaned and "." not in cleaned:
            cleaned = cleaned.replace(",", ".")
        elif "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(",", "")

        return Decimal(cleaned)
    except InvalidOperation:
        return None


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
