"""Tests for text normalization."""

from deliverect_sync.normalization.text import sanitize_formula

def test_sanitize_safe():
    assert sanitize_formula("Normal Text") == "Normal Text"
    assert sanitize_formula("12345") == "12345"

def test_sanitize_formula():
    assert sanitize_formula("=SUM(A1:A10)") == "'=SUM(A1:A10)"
    assert sanitize_formula("+12345") == "'+12345"
    assert sanitize_formula("-12345") == "'-12345"
    assert sanitize_formula("@cmd|' /C calc'!A0") == "'@cmd|' /C calc'!A0"

def test_sanitize_empty():
    assert sanitize_formula("") == ""
    assert sanitize_formula(None) is None
