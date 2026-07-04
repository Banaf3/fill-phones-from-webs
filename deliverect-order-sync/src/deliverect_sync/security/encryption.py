"""Fernet encryption with keyring-managed keys.

Encryption keys are stored in the OS credential manager
(Windows Credential Manager on Windows) via the keyring library.
No keys are stored in source code, config files, or on disk.
"""

from __future__ import annotations

import logging

import keyring
from cryptography.fernet import Fernet, InvalidToken

from deliverect_sync.logging_config import get_logger

logger = get_logger("encryption")

# Service name for keyring entries
_SERVICE_NAME = "deliverect-order-sync"
_AUTH_KEY_ACCOUNT = "deliverect_sync_auth_state_key"
_PII_KEY_ACCOUNT = "deliverect_sync_pii_key"


class EncryptionManager:
    """Manages Fernet encryption keys via the OS credential manager."""

    def __init__(self, key_account: str = _AUTH_KEY_ACCOUNT) -> None:
        """Initialize with a specific key account name.

        Args:
            key_account: The keyring account name for this key.
                         Use different accounts for auth state vs PII encryption.
        """
        self._key_account = key_account
        self._fernet: Fernet | None = None

    @property
    def service_name(self) -> str:
        return _SERVICE_NAME

    def _get_or_create_key(self) -> bytes:
        """Retrieve the encryption key from keyring, or generate a new one."""
        stored_key = keyring.get_password(_SERVICE_NAME, self._key_account)

        if stored_key:
            logger.debug("Encryption key loaded from credential manager")
            return stored_key.encode("utf-8")

        # Generate a new Fernet key
        new_key = Fernet.generate_key()
        keyring.set_password(_SERVICE_NAME, self._key_account, new_key.decode("utf-8"))
        logger.info("New encryption key generated and stored in credential manager")
        return new_key

    def _get_fernet(self) -> Fernet:
        """Get or create the Fernet cipher instance."""
        if self._fernet is None:
            key = self._get_or_create_key()
            self._fernet = Fernet(key)
        return self._fernet

    def encrypt(self, data: bytes) -> bytes:
        """Encrypt data using Fernet symmetric encryption.

        Args:
            data: Plaintext bytes to encrypt.

        Returns:
            Encrypted ciphertext bytes (URL-safe base64).
        """
        return self._get_fernet().encrypt(data)

    def decrypt(self, data: bytes) -> bytes:
        """Decrypt data using Fernet symmetric encryption.

        Args:
            data: Ciphertext bytes to decrypt.

        Returns:
            Decrypted plaintext bytes.

        Raises:
            InvalidToken: If the data cannot be decrypted (wrong key, corrupted, etc.).
        """
        try:
            return self._get_fernet().decrypt(data)
        except InvalidToken:
            logger.error("Failed to decrypt data — key mismatch or corrupted ciphertext")
            raise

    def encrypt_string(self, text: str) -> str:
        """Encrypt a string and return base64-encoded ciphertext."""
        encrypted = self.encrypt(text.encode("utf-8"))
        return encrypted.decode("utf-8")

    def decrypt_string(self, ciphertext: str) -> str:
        """Decrypt a base64-encoded ciphertext string."""
        decrypted = self.decrypt(ciphertext.encode("utf-8"))
        return decrypted.decode("utf-8")

    def has_key(self) -> bool:
        """Check if an encryption key exists in the credential manager."""
        return keyring.get_password(_SERVICE_NAME, self._key_account) is not None

    def rotate_key(self) -> None:
        """Generate a new encryption key.

        WARNING: This invalidates all data encrypted with the previous key.
        Re-encrypt existing data before calling this.
        """
        new_key = Fernet.generate_key()
        keyring.set_password(_SERVICE_NAME, self._key_account, new_key.decode("utf-8"))
        self._fernet = Fernet(new_key)
        logger.warning("Encryption key rotated — previous encrypted data is now unreadable")

    def delete_key(self) -> None:
        """Remove the encryption key from the credential manager."""
        try:
            keyring.delete_password(_SERVICE_NAME, self._key_account)
            self._fernet = None
            logger.info("Encryption key deleted from credential manager")
        except keyring.errors.PasswordDeleteError:
            logger.debug("No encryption key to delete")


def get_auth_encryption() -> EncryptionManager:
    """Get the encryption manager for authentication state."""
    return EncryptionManager(_AUTH_KEY_ACCOUNT)


def get_pii_encryption() -> EncryptionManager:
    """Get the encryption manager for PII field encryption."""
    return EncryptionManager(_PII_KEY_ACCOUNT)
