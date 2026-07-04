"""CSV file detection — encoding, delimiter, and schema identification."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from deliverect_sync.exceptions import CSVParseError
from deliverect_sync.logging_config import get_logger

logger = get_logger("csv_detector")


class CSVDetectionResult:
    """Results from CSV file analysis."""

    def __init__(self) -> None:
        self.encoding: str = "utf-8"
        self.has_bom: bool = False
        self.delimiter: str = ","
        self.quotechar: str = '"'
        self.header_row: list[str] = []
        self.row_count: int = 0
        self.schema_version: str | None = None


def detect_csv(filepath: Path) -> CSVDetectionResult:
    """Analyze a CSV file to detect encoding, delimiter, and schema.

    Args:
        filepath: Path to the CSV file.

    Returns:
        CSVDetectionResult with detected parameters.

    Raises:
        CSVParseError: If the file cannot be analyzed.
    """
    result = CSVDetectionResult()

    try:
        # Read raw bytes for encoding detection
        raw = filepath.read_bytes()
    except OSError as e:
        raise CSVParseError(f"Cannot read file: {e}")

    # Detect encoding
    result.encoding, result.has_bom = _detect_encoding(raw)
    logger.debug("Detected encoding: %s (BOM: %s)", result.encoding, result.has_bom)

    # Decode content
    try:
        text = _decode_content(raw, result.encoding, result.has_bom)
    except (UnicodeDecodeError, LookupError) as e:
        raise CSVParseError(f"Cannot decode file with encoding {result.encoding}: {e}")

    # Detect delimiter
    result.delimiter = _detect_delimiter(text)
    logger.debug("Detected delimiter: %r", result.delimiter)

    # Parse headers
    reader = csv.reader(io.StringIO(text), delimiter=result.delimiter, quotechar=result.quotechar)
    try:
        result.header_row = next(reader)
        # Clean up headers
        result.header_row = [h.strip() for h in result.header_row]
    except StopIteration:
        raise CSVParseError("CSV file has no rows")

    # Handle duplicate headers
    result.header_row = _handle_duplicate_headers(result.header_row)

    # Count rows
    result.row_count = sum(1 for _ in reader)

    # Detect schema version based on column set
    result.schema_version = _detect_schema_version(result.header_row)

    logger.info(
        "CSV detected: %d columns, %d rows, delimiter=%r, encoding=%s, schema=%s",
        len(result.header_row), result.row_count, result.delimiter,
        result.encoding, result.schema_version,
    )

    return result


def _detect_encoding(raw: bytes) -> tuple[str, bool]:
    """Detect file encoding.

    Checks for BOM markers first, then tries UTF-8.

    Returns:
        Tuple of (encoding_name, has_bom).
    """
    # Check for BOM markers
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8", True
    elif raw.startswith(b"\xff\xfe"):
        return "utf-16-le", True
    elif raw.startswith(b"\xfe\xff"):
        return "utf-16-be", True

    # Try UTF-8
    try:
        raw.decode("utf-8")
        return "utf-8", False
    except UnicodeDecodeError:
        pass

    # Fallback: try common encodings
    for encoding in ["windows-1256", "iso-8859-6", "latin-1"]:
        try:
            raw.decode(encoding)
            return encoding, False
        except (UnicodeDecodeError, LookupError):
            continue

    # Last resort
    return "utf-8", False


def _decode_content(raw: bytes, encoding: str, has_bom: bool) -> str:
    """Decode raw bytes using detected encoding."""
    if has_bom and encoding == "utf-8":
        raw = raw[3:]  # Strip UTF-8 BOM
    elif has_bom and encoding.startswith("utf-16"):
        raw = raw[2:]  # Strip UTF-16 BOM

    return raw.decode(encoding, errors="replace")


def _detect_delimiter(text: str) -> str:
    """Detect CSV delimiter from the first few lines.

    Tries comma, semicolon, tab, and pipe.
    """
    first_lines = text.split("\n")[:5]
    if not first_lines:
        return ","

    first_line = first_lines[0]

    # Count occurrences of common delimiters in the first line
    candidates = {
        ",": first_line.count(","),
        ";": first_line.count(";"),
        "\t": first_line.count("\t"),
        "|": first_line.count("|"),
    }

    # Filter out zero-count delimiters
    candidates = {k: v for k, v in candidates.items() if v > 0}

    if not candidates:
        return ","

    # If comma is present with reasonable count, prefer it
    if "," in candidates and candidates[","] >= 2:
        # But check if semicolons are more frequent (European format)
        if ";" in candidates and candidates[";"] > candidates[","]:
            return ";"
        return ","

    # Return the most frequent delimiter
    return max(candidates, key=candidates.get)


def _handle_duplicate_headers(headers: list[str]) -> list[str]:
    """Handle duplicate column headers by appending suffixes.

    Example: ["Name", "Name", "Name"] → ["Name", "Name_2", "Name_3"]
    """
    seen: dict[str, int] = {}
    result: list[str] = []

    for header in headers:
        if header in seen:
            seen[header] += 1
            result.append(f"{header}_{seen[header]}")
        else:
            seen[header] = 1
            result.append(header)

    return result


def _detect_schema_version(headers: list[str]) -> str | None:
    """Detect the schema version based on which columns are present."""
    header_set = {h.lower().strip() for h in headers}

    # Known column groups that identify versions
    if "channel order id" in header_set and "plu" in header_set:
        return "v2_item_split"
    elif "channel order id" in header_set:
        return "v2_standard"
    elif "order id" in header_set:
        return "v1_standard"

    return None
