"""Text normalization.

Mitigates CSV injection vulnerabilities.
"""

from __future__ import annotations


def sanitize_formula(value: str | None) -> str | None:
    """Sanitize strings to prevent CSV formula injection.
    
    Prepends a single quote to strings starting with formula characters.
    """
    if not value:
        return value
        
    value = str(value)
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
        
    return value
