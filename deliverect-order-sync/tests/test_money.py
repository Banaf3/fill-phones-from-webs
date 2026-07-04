"""Tests for monetary parsing."""

from decimal import Decimal
from deliverect_sync.normalization.money import parse_money

def test_parse_standard():
    assert parse_money("150.50") == Decimal("150.50")
    assert parse_money("1000") == Decimal("1000")
    assert parse_money("-50.25") == Decimal("-50.25")

def test_parse_with_currency():
    assert parse_money("SAR 150.50") == Decimal("150.50")
    assert parse_money("$150.50") == Decimal("150.50")
    assert parse_money("150.50 ر.س") == Decimal("150.50")

def test_parse_european_format():
    assert parse_money("150,50") == Decimal("150.50")
    assert parse_money("1.250,50") == Decimal("1250.50")

def test_parse_arabic_digits():
    # ٠-٩ and ۰-۹
    assert parse_money("١٥٠.٥٠") == Decimal("150.50")
    assert parse_money("۱۵۰.۵۰") == Decimal("150.50")

def test_parse_invalid():
    assert parse_money("") is None
    assert parse_money(None) is None
    assert parse_money("abc") is None
