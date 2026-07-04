"""Encrypted Playwright authentication state management.

Saves and loads browser storage state (cookies, localStorage, etc.)
encrypted with Fernet. Unencrypted temp files are deleted immediately.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from cryptography.fernet import InvalidToken

from deliverect_sync.exceptions import AuthExpiredError, AuthenticationError
from deliverect_sync.logging_config import get_logger
from deliverect_sync.security.encryption import get_auth_encryption

logger = get_logger("auth_state")

_STATE_FILENAME = "state.enc"


class AuthStateManager:
    """Manages encrypted Playwright browser authentication state."""

    def __init__(self, auth_dir: Path) -> None:
        """Initialize with the directory to store encrypted auth state.

        Args:
            auth_dir: Directory for encrypted state files (e.g., ~/.deliverect-sync/auth/).
        """
        self._auth_dir = auth_dir
        self._auth_dir.mkdir(parents=True, exist_ok=True)
        self._encryption = get_auth_encryption()
        self._restrict_directory_permissions(self._auth_dir)

    @property
    def state_file(self) -> Path:
        """Path to the encrypted state file."""
        return self._auth_dir / _STATE_FILENAME

    def has_state(self) -> bool:
        """Check if saved authentication state exists."""
        return self.state_file.exists() and self.state_file.stat().st_size > 0

    def save_state(self, storage_state: dict[str, Any]) -> None:
        """Encrypt and save Playwright storage state.

        The storage state is serialized to JSON, encrypted with Fernet,
        and written to disk. No unencrypted file is left on disk.

        Args:
            storage_state: Playwright's storage_state() output dict.
        """
        state_json = json.dumps(storage_state, ensure_ascii=False)
        encrypted = self._encryption.encrypt(state_json.encode("utf-8"))

        # Write to a temp file first, then rename atomically
        tmp_fd = None
        tmp_path = None
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(self._auth_dir),
                prefix=".state_tmp_",
                suffix=".enc",
            )
            os.write(tmp_fd, encrypted)
            os.close(tmp_fd)
            tmp_fd = None

            # Atomic rename (on same filesystem)
            dest = self.state_file
            if dest.exists():
                dest.unlink()
            Path(tmp_path).rename(dest)

            logger.info("Authentication state saved (encrypted)")
        except Exception:
            # Clean up temp file on failure
            if tmp_fd is not None:
                os.close(tmp_fd)
            if tmp_path and Path(tmp_path).exists():
                Path(tmp_path).unlink()
            raise
        finally:
            # Paranoia: ensure no temp files remain
            if tmp_path and Path(tmp_path).exists():
                Path(tmp_path).unlink()

    def load_state(self) -> dict[str, Any]:
        """Load and decrypt the saved authentication state.

        Returns:
            Playwright storage state dict.

        Raises:
            AuthExpiredError: If the state file doesn't exist.
            AuthenticationError: If decryption fails.
        """
        if not self.has_state():
            raise AuthExpiredError()

        try:
            encrypted = self.state_file.read_bytes()
            decrypted = self._encryption.decrypt(encrypted)
            state: dict[str, Any] = json.loads(decrypted.decode("utf-8"))
            logger.debug("Authentication state loaded and decrypted")
            return state
        except InvalidToken:
            logger.error("Failed to decrypt authentication state — key may have changed")
            raise AuthenticationError(
                "Cannot decrypt authentication state. "
                "The encryption key may have been rotated. "
                "Run 'python -m deliverect_sync reauthenticate' to log in again."
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error("Corrupted authentication state file")
            raise AuthenticationError(
                "Authentication state file is corrupted. "
                "Run 'python -m deliverect_sync reauthenticate' to log in again.",
                details=str(e),
            )

    def save_from_context(self, context: Any) -> None:
        """Save storage state directly from a Playwright BrowserContext.

        Args:
            context: Playwright BrowserContext with active session.
        """
        # Save to a temp file, read it, encrypt, delete temp
        tmp_path = None
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(self._auth_dir),
                prefix=".state_plain_",
                suffix=".json",
            )
            os.close(tmp_fd)

            # Playwright saves storage state to the file
            context.storage_state(path=tmp_path)

            # Read the plaintext state
            with open(tmp_path, encoding="utf-8") as f:
                state = json.load(f)

            # Encrypt and save
            self.save_state(state)
        finally:
            # CRITICAL: Delete unencrypted temp file immediately
            if tmp_path and Path(tmp_path).exists():
                Path(tmp_path).unlink()
                logger.debug("Unencrypted temp state file deleted")

    def delete_state(self) -> None:
        """Delete the saved authentication state."""
        if self.state_file.exists():
            self.state_file.unlink()
            logger.info("Authentication state deleted")

    def write_state_for_playwright(self) -> Path:
        """Write decrypted state to a temp file for Playwright context creation.

        IMPORTANT: The caller MUST delete this file after use.

        Returns:
            Path to the temporary plaintext state file.
        """
        state = self.load_state()
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self._auth_dir),
            prefix=".state_use_",
            suffix=".json",
        )
        try:
            state_json = json.dumps(state, ensure_ascii=False)
            os.write(tmp_fd, state_json.encode("utf-8"))
            os.close(tmp_fd)
            return Path(tmp_path)
        except Exception:
            os.close(tmp_fd)
            if Path(tmp_path).exists():
                Path(tmp_path).unlink()
            raise

    @staticmethod
    def _restrict_directory_permissions(directory: Path) -> None:
        """Restrict directory permissions to the current user only.

        On Windows, uses icacls to remove inherited permissions and
        grant full control only to the current user.
        On other platforms, uses chmod 700.
        """
        try:
            if sys.platform == "win32":
                username = os.environ.get("USERNAME", "")
                if username:
                    # Remove inheritance and grant only current user
                    subprocess.run(
                        [
                            "icacls",
                            str(directory),
                            "/inheritance:r",
                            "/grant:r",
                            f"{username}:(OI)(CI)F",
                        ],
                        capture_output=True,
                        check=False,
                        timeout=10,
                    )
                    logger.debug("Directory permissions restricted (Windows)")
            else:
                directory.chmod(stat.S_IRWXU)  # 700
                logger.debug("Directory permissions restricted (Unix)")
        except Exception as e:
            # Non-fatal: log and continue
            logger.warning("Could not restrict directory permissions: %s", type(e).__name__)
