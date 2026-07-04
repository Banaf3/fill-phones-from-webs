"""Tests for phone normalization."""

from deliverect_sync.normalization.phones import normalize_phone

def test_normalize_international():
    assert normalize_phone("+966501234567") == "+966501234567"
    assert normalize_phone("00966501234567") == "+966501234567"

def test_normalize_local():
    assert normalize_phone("0501234567") == "+966501234567"
    assert normalize_phone("501234567") == "+966501234567"

def test_normalize_with_noise():
    assert normalize_phone("+966 50 123 4567") == "+966501234567"
    assert normalize_phone("(050) 123-4567") == "+966501234567"

def test_normalize_invalid():
    assert normalize_phone("") is None
    assert normalize_phone(None) is None
    assert normalize_phone("abc") is None
