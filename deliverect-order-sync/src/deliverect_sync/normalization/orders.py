"""Order-level normalization.

Provides normalization for statuses and locations.
"""

from __future__ import annotations


def normalize_status(status: str | None) -> str | None:
    """Normalize order status strings."""
    if not status:
        return None
    
    s = status.strip().title()
    # Handle known Arabic statuses if they appear
    mapping = {
        "مقبول": "Accepted",
        "مكتمل": "Finalized",
        "ملغى": "Canceled",
        "فشل": "Failed",
        "جديد": "New",
    }
    return mapping.get(s, s)


def normalize_location(location: str | None) -> str | None:
    """Normalize location names."""
    if not location:
        return None
    return location.strip()
