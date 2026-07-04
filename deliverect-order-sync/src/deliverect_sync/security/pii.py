"""PII field-level encryption and data retention management.

Customer PII (name, phone, email, address) is encrypted before
storage in SQLite and decrypted only when needed for output.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from deliverect_sync.logging_config import get_logger
from deliverect_sync.security.encryption import get_pii_encryption

logger = get_logger("pii")


class PIIFieldEncryption:
    """Encrypt and decrypt individual PII fields for SQLite storage."""

    def __init__(self) -> None:
        self._encryption = get_pii_encryption()

    def encrypt_field(self, value: str | None) -> bytes | None:
        """Encrypt a PII field value.

        Args:
            value: Plaintext PII value, or None.

        Returns:
            Encrypted bytes, or None if input is None/empty.
        """
        if not value:
            return None
        return self._encryption.encrypt(value.encode("utf-8"))

    def decrypt_field(self, value: bytes | None) -> str | None:
        """Decrypt a PII field value.

        Args:
            value: Encrypted PII bytes, or None.

        Returns:
            Decrypted plaintext string, or None if input is None/empty.
        """
        if not value:
            return None
        try:
            return self._encryption.decrypt(value).decode("utf-8")
        except Exception:
            logger.warning("Failed to decrypt PII field — returning None")
            return None


class DataRetentionManager:
    """Manages data retention and cleanup of expired files."""

    def __init__(self, downloads_dir: Path, retention_days: int = 7) -> None:
        """Initialize retention manager.

        Args:
            downloads_dir: Directory containing downloaded CSV files.
            retention_days: Number of days to retain raw downloads.
        """
        self._downloads_dir = downloads_dir
        self._retention_days = retention_days

    def purge_expired_downloads(self) -> list[str]:
        """Delete downloaded files older than the retention period.

        Returns:
            List of deleted file names.
        """
        if not self._downloads_dir.exists():
            return []

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=self._retention_days)
        cutoff_ts = cutoff.timestamp()
        deleted: list[str] = []

        for filepath in self._downloads_dir.iterdir():
            if filepath.is_file():
                try:
                    file_mtime = filepath.stat().st_mtime
                    if file_mtime < cutoff_ts:
                        self._secure_delete(filepath)
                        deleted.append(filepath.name)
                        logger.info(
                            "Purged expired download: %s (age: %d days)",
                            filepath.name,
                            (time.time() - file_mtime) / 86400,
                        )
                except OSError as e:
                    logger.warning(
                        "Could not check/delete file %s: %s",
                        filepath.name,
                        type(e).__name__,
                    )

        return deleted

    def purge_expired_screenshots(
        self, screenshots_dir: Path, retention_days: int = 1
    ) -> list[str]:
        """Delete screenshots older than the retention period.

        Args:
            screenshots_dir: Directory containing diagnostic screenshots.
            retention_days: Number of days to retain screenshots.

        Returns:
            List of deleted file names.
        """
        if not screenshots_dir.exists():
            return []

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=retention_days)
        cutoff_ts = cutoff.timestamp()
        deleted: list[str] = []

        for filepath in screenshots_dir.iterdir():
            if filepath.is_file() and filepath.suffix.lower() in (".png", ".jpg", ".jpeg"):
                try:
                    if filepath.stat().st_mtime < cutoff_ts:
                        filepath.unlink()
                        deleted.append(filepath.name)
                except OSError:
                    pass

        return deleted

    @staticmethod
    def _secure_delete(filepath: Path) -> None:
        """Delete a file with best-effort secure overwrite.

        On SSDs this provides limited additional security, but it
        prevents trivial recovery of plaintext CSV data.
        """
        try:
            size = filepath.stat().st_size
            if size > 0 and size < 100 * 1024 * 1024:  # Only overwrite files < 100 MB
                with open(filepath, "r+b") as f:
                    f.write(os.urandom(size))
                    f.flush()
                    os.fsync(f.fileno())
        except OSError:
            pass
        finally:
            filepath.unlink(missing_ok=True)

    def purge_job_data(self, job_dir: Path) -> None:
        """Purge all data associated with a specific job.

        Args:
            job_dir: Directory containing job-specific files.
        """
        if not job_dir.exists():
            return

        for filepath in job_dir.rglob("*"):
            if filepath.is_file():
                try:
                    self._secure_delete(filepath)
                except OSError:
                    pass

        # Remove empty directories
        for dirpath in sorted(job_dir.rglob("*"), reverse=True):
            if dirpath.is_dir():
                try:
                    dirpath.rmdir()
                except OSError:
                    pass

        try:
            job_dir.rmdir()
        except OSError:
            pass

        logger.info("Purged job data directory: %s", job_dir.name)
