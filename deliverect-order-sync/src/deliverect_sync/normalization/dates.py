"""Date parsing and timezone awareness."""

from __future__ import annotations

from datetime import datetime
import dateutil.parser
import pytz


def parse_datetime(dt_str: str | None, tz_name: str = "UTC") -> datetime | None:
    """Parse a datetime string into a timezone-aware datetime object."""
    if not dt_str:
        return None
        
    try:
        # dateutil parser handles many formats including ISO8601
        dt = dateutil.parser.parse(dt_str)
        
        # If naive, make aware using configured timezone
        if dt.tzinfo is None:
            tz = pytz.timezone(tz_name)
            dt = tz.localize(dt)
            
        return dt
    except (ValueError, TypeError, OverflowError):
        return None
