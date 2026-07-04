"""Tests for date normalization."""

from deliverect_sync.normalization.dates import parse_datetime
from datetime import datetime, timezone

def test_parse_iso8601():
    dt = parse_datetime("2023-10-27T10:30:00Z")
    assert dt is not None
    assert dt.year == 2023
    assert dt.month == 10
    assert dt.tzinfo is not None

def test_parse_without_timezone():
    # Should default to UTC if no timezone in string and tz_name="UTC"
    dt = parse_datetime("2023-10-27 10:30:00")
    assert dt is not None
    assert dt.tzinfo is not None

def test_parse_invalid():
    assert parse_datetime("") is None
    assert parse_datetime(None) is None
    assert parse_datetime("invalid date") is None
