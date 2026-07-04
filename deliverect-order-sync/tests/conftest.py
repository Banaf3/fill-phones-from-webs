"""Pytest configuration and shared fixtures."""

import pytest
from pathlib import Path

@pytest.fixture
def sample_csv_dir(tmp_path: Path) -> Path:
    """Create a directory with sample CSV files for testing."""
    d = tmp_path / "csv"
    d.mkdir()
    
    # Create a basic sample CSV
    basic = d / "basic_export.csv"
    basic.write_text(
        "Order ID,Location,Channel,Order Total,Status\n"
        "12345,Riyadh,Deliveroo,150.50,Accepted\n"
        "12346,Jeddah,HungerStation,75.00,Completed\n",
        encoding="utf-8"
    )
    
    # Create an Arabic sample CSV
    arabic = d / "arabic_export.csv"
    arabic.write_text(
        "رقم الطلب,الفرع,المنصة,الإجمالي,الحالة\n"
        "67890,الرياض,جاهز,120.00,مكتمل\n",
        encoding="utf-8-sig"  # Include BOM
    )
    
    return d
