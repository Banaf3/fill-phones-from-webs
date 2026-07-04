"""Secure download manager — validates and stores downloaded files.

Handles Playwright downloads with SHA-256 hashing, type validation,
atomic file moves, and duplicate detection.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import Download, Page

from deliverect_sync.config import AppSettings
from deliverect_sync.exceptions import DownloadError, InvalidDownloadError
from deliverect_sync.logging_config import get_logger

logger = get_logger("download_manager")

# Maximum allowed file size (100 MB)
_MAX_FILE_SIZE = 100 * 1024 * 1024

# Minimum valid CSV file size (at least a header row)
_MIN_FILE_SIZE = 10


class DownloadManager:
    """Manages secure file downloads with validation."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._downloads_dir = settings.downloads_dir
        self._last_hash: str | None = None

    @property
    def last_hash(self) -> str | None:
        """SHA-256 hash of the last downloaded file."""
        return self._last_hash

    def handle_download(self, page: Page) -> Path:
        """Handle a Playwright download event.

        Waits for the download, validates the file, and moves it
        to the configured downloads directory.

        Args:
            page: Current Playwright page.

        Returns:
            Path to the validated and saved file.

        Raises:
            DownloadError: If download fails.
            InvalidDownloadError: If the downloaded file is invalid.
        """
        timeout_ms = self._settings.browser.download_timeout * 1000

        try:
            with page.expect_download(timeout=timeout_ms) as download_info:
                # The download should already be triggered
                pass

            download: Download = download_info.value
            return self._process_download(download)

        except Exception as e:
            if isinstance(e, (DownloadError, InvalidDownloadError)):
                raise
            raise DownloadError(f"Download failed: {type(e).__name__}: {e}")

    def handle_download_object(self, download: Download) -> Path:
        """Process a Download object directly."""
        return self._process_download(download)

    def _process_download(self, download: Download) -> Path:
        """Process and validate a Playwright Download.

        Steps:
        1. Save to temp directory
        2. Calculate SHA-256
        3. Validate file type and size
        4. Check for duplicates
        5. Move atomically to downloads directory

        Args:
            download: Playwright Download object.

        Returns:
            Final path of the saved file.
        """
        suggested_name = download.suggested_filename or "export.csv"
        logger.info("Processing download: %s", suggested_name)

        # Save to temp directory
        temp_dir = tempfile.mkdtemp(dir=str(self._downloads_dir))
        temp_path = Path(temp_dir) / suggested_name

        try:
            download.save_as(str(temp_path))
            logger.debug("Download saved to temp: %s", temp_path)

            # Validate
            self._validate_file(temp_path)

            # Calculate hash
            file_hash = self._calculate_hash(temp_path)
            self._last_hash = file_hash
            logger.info("File hash: %s", file_hash[:16])

            # Check for duplicates
            if self._is_duplicate(file_hash):
                logger.warning("Duplicate download detected (hash: %s)", file_hash[:16])
                raise InvalidDownloadError(
                    f"Duplicate file — a file with hash {file_hash[:16]}... "
                    "has already been downloaded"
                )

            # Move to final location
            final_path = self._downloads_dir / suggested_name

            # Ensure unique filename
            if final_path.exists():
                stem = final_path.stem
                suffix = final_path.suffix
                counter = 1
                while final_path.exists():
                    final_path = self._downloads_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            shutil.move(str(temp_path), str(final_path))
            logger.info("Download saved: %s (%d bytes)", final_path.name, final_path.stat().st_size)

            return final_path

        finally:
            # Clean up temp directory
            try:
                if temp_path.exists():
                    temp_path.unlink()
                Path(temp_dir).rmdir()
            except OSError:
                pass

    def validate_file(self, filepath: Path) -> str:
        """Validate a file and return its SHA-256 hash.

        Public method for validating manually downloaded files.

        Args:
            filepath: Path to the file.

        Returns:
            SHA-256 hash string.

        Raises:
            InvalidDownloadError: If validation fails.
        """
        self._validate_file(filepath)
        file_hash = self._calculate_hash(filepath)
        self._last_hash = file_hash
        return file_hash

    def _validate_file(self, filepath: Path) -> None:
        """Validate a downloaded file.

        Rejects:
        - Zero-byte files
        - Files exceeding maximum size
        - HTML error pages renamed as CSV
        - Files without a recognizable header row
        """
        if not filepath.exists():
            raise InvalidDownloadError("File does not exist")

        file_size = filepath.stat().st_size

        # Check size
        if file_size == 0:
            raise InvalidDownloadError("File is empty (zero bytes)")

        if file_size < _MIN_FILE_SIZE:
            raise InvalidDownloadError(f"File too small ({file_size} bytes)")

        if file_size > _MAX_FILE_SIZE:
            raise InvalidDownloadError(
                f"File exceeds maximum size ({file_size / 1024 / 1024:.1f} MB > "
                f"{_MAX_FILE_SIZE / 1024 / 1024:.0f} MB)"
            )

        # Check content type
        try:
            with open(filepath, "rb") as f:
                # Read first 1024 bytes
                head = f.read(1024)

            # Reject HTML error pages
            head_lower = head.lower()
            if head_lower.startswith(b"<!doctype") or head_lower.startswith(b"<html"):
                raise InvalidDownloadError(
                    "File appears to be an HTML page, not a CSV export"
                )

            # Check for UTF-8 BOM
            if head.startswith(b"\xef\xbb\xbf"):
                head = head[3:]

            # Try to decode and check for header-like content
            try:
                text = head.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text = head.decode("latin-1")
                except UnicodeDecodeError:
                    raise InvalidDownloadError("File encoding is not recognized")

            # Check if first line looks like a CSV header
            first_line = text.split("\n")[0].strip()
            if not first_line:
                raise InvalidDownloadError("File has no header row")

            # A valid CSV header should have at least one delimiter or look like column names
            has_delimiter = any(d in first_line for d in [",", ";", "\t", "|"])
            has_alpha = any(c.isalpha() for c in first_line)

            if not has_alpha:
                raise InvalidDownloadError(
                    "First line does not look like a CSV header"
                )

            logger.debug("File validation passed: %d bytes, header looks valid", file_size)

        except InvalidDownloadError:
            raise
        except Exception as e:
            raise InvalidDownloadError(f"Could not read file: {type(e).__name__}")

    @staticmethod
    def _calculate_hash(filepath: Path) -> str:
        """Calculate SHA-256 hash of a file."""
        sha256 = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _is_duplicate(self, file_hash: str) -> bool:
        """Check if a file with this hash already exists in downloads."""
        for filepath in self._downloads_dir.iterdir():
            if filepath.is_file() and filepath.suffix.lower() == ".csv":
                try:
                    existing_hash = self._calculate_hash(filepath)
                    if existing_hash == file_hash:
                        return True
                except OSError:
                    continue
        return False
