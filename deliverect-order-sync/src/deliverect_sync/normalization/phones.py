"""Phone number normalization.

Normalizes phone numbers to international format (E.164).
"""

from __future__ import annotations

import re


def normalize_phone(phone: str | None, default_country_code: str = "966") -> str | None:
    """Normalize a phone number to standard format.
    
    Default behavior expects Saudi numbers (+966) if no country code provided.
    """
    if not phone:
        return None
        
    # Remove all non-digit characters except plus
    cleaned = re.sub(r"[^\d+]", "", str(phone))
    if not cleaned:
        return None
        
    # If it starts with a plus, assume it's already international
    if cleaned.startswith("+"):
        return cleaned
        
    # If it starts with 00, replace with +
    if cleaned.startswith("00"):
        return "+" + cleaned[2:]
        
    # If it starts with a local prefix like 05 (Saudi mobile), add country code
    if cleaned.startswith("0"):
        return f"+{default_country_code}{cleaned[1:]}"
        
    # If it's a raw number starting with country code, add +
    if cleaned.startswith(default_country_code):
        return "+" + cleaned
        
    # Otherwise, assume it needs the country code
    return f"+{default_country_code}{cleaned}"
