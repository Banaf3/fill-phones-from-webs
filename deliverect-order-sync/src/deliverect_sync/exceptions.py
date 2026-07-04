"""Exception hierarchy for Deliverect Order Sync.

All exceptions carry structured error codes. No raw tracebacks
are exposed in user-facing messages.
"""

from __future__ import annotations

from deliverect_sync.models import RunStatus


class DeliverectSyncError(Exception):
    """Base exception for all application errors."""

    status: RunStatus = RunStatus.EXPORT_FAILED

    def __init__(self, message: str, *, details: str | None = None) -> None:
        self.details = details
        super().__init__(message)

    @property
    def error_code(self) -> str:
        return self.status.value


# --- Authentication ---


class AuthenticationError(DeliverectSyncError):
    """Authentication failed or is required."""

    status = RunStatus.AUTH_REQUIRED


class AuthExpiredError(AuthenticationError):
    """Saved authentication state has expired."""

    def __init__(self) -> None:
        super().__init__(
            "Authentication session has expired. "
            "Run 'python -m deliverect_sync reauthenticate' to log in again."
        )


class LoginFailedError(DeliverectSyncError):
    """Login process did not complete successfully."""

    status = RunStatus.LOGIN_FAILED


class AccountMismatchError(DeliverectSyncError):
    """The authenticated account does not match the expected account."""

    status = RunStatus.ACCOUNT_MISMATCH


# --- Permissions ---


class PermissionError_(DeliverectSyncError):
    """A required Deliverect permission is missing.

    Named with trailing underscore to avoid shadowing built-in PermissionError.
    """

    def __init__(self, permission: RunStatus, *, guidance: str | None = None) -> None:
        self.status = permission
        msg = f"{permission.value}: "
        if permission == RunStatus.MISSING_VIEW_ORDERS_PERMISSION:
            msg += "Cannot view the Orders page."
        elif permission == RunStatus.MISSING_EXPORT_ORDERS_PERMISSION:
            msg += "Cannot export orders."
        elif permission == RunStatus.MISSING_OPERATIONS_PERMISSION:
            msg += "Cannot access the Operations page."
        else:
            msg += "Missing required permission."

        if guidance:
            msg += f" {guidance}"
        else:
            msg += (
                " Ask your Deliverect account administrator "
                "to grant the relevant role permissions."
            )

        super().__init__(msg)


# --- Calibration and UI ---


class CalibrationRequiredError(DeliverectSyncError):
    """Selector calibration has not been performed."""

    status = RunStatus.CALIBRATION_REQUIRED

    def __init__(self) -> None:
        super().__init__(
            "Calibration is required. "
            "Run 'python -m deliverect_sync calibrate' to discover UI selectors."
        )


class UIChangedError(DeliverectSyncError):
    """The Deliverect UI has changed and selectors are no longer valid."""

    status = RunStatus.UI_CHANGED

    def __init__(self, element: str, *, details: str | None = None) -> None:
        super().__init__(
            f"UI change detected: could not locate '{element}'. "
            "Run 'python -m deliverect_sync calibrate' to update selectors.",
            details=details,
        )


# --- Export ---


class ExportRequestError(DeliverectSyncError):
    """Failed to request an export from the dialog."""

    status = RunStatus.EXPORT_REQUEST_FAILED


class ExportOperationNotFoundError(DeliverectSyncError):
    """Could not find the newly created export operation."""

    status = RunStatus.EXPORT_OPERATION_NOT_FOUND


class AmbiguousExportError(DeliverectSyncError):
    """Multiple candidate export operations found."""

    status = RunStatus.AMBIGUOUS_EXPORT_OPERATION

    def __init__(self, count: int) -> None:
        super().__init__(
            f"Found {count} candidate export operations. "
            "Cannot determine which one belongs to this run. "
            "Use the dashboard to select the correct operation."
        )


class ExportTimeoutError(DeliverectSyncError):
    """Export operation did not complete within the configured timeout."""

    status = RunStatus.EXPORT_TIMEOUT


class ExportFailedError(DeliverectSyncError):
    """Export operation completed with a failure status."""

    status = RunStatus.EXPORT_FAILED


# --- Download ---


class DownloadLinkError(DeliverectSyncError):
    """Failed to obtain or activate the download link."""

    status = RunStatus.DOWNLOAD_LINK_FAILED


class DownloadError(DeliverectSyncError):
    """File download failed."""

    status = RunStatus.DOWNLOAD_FAILED


class InvalidDownloadError(DeliverectSyncError):
    """Downloaded file is invalid (wrong type, empty, duplicate, etc.)."""

    status = RunStatus.INVALID_DOWNLOAD

    def __init__(self, reason: str) -> None:
        super().__init__(f"Invalid download: {reason}")


# --- CSV ---


class CSVSchemaUnknownError(DeliverectSyncError):
    """CSV file has an unrecognized schema."""

    status = RunStatus.CSV_SCHEMA_UNKNOWN


class CSVParseError(DeliverectSyncError):
    """Failed to parse CSV content."""

    status = RunStatus.CSV_PARSE_FAILED


# --- Database ---


class DatabaseError_(DeliverectSyncError):
    """Database operation failed.

    Named with trailing underscore to avoid shadowing builtins.
    """

    status = RunStatus.DATABASE_FAILED


class ConcurrentRunError(DeliverectSyncError):
    """Another sync run is already active."""

    status = RunStatus.DATABASE_FAILED

    def __init__(self) -> None:
        super().__init__(
            "Another sync run is already active. "
            "Wait for it to complete or use 'status' to check."
        )


# --- Excel ---


class ExcelExportError(DeliverectSyncError):
    """Failed to generate Excel output."""

    status = RunStatus.EXCEL_EXPORT_FAILED


# --- User ---


class CanceledByUserError(DeliverectSyncError):
    """Operation was canceled by the user."""

    status = RunStatus.CANCELED_BY_USER

    def __init__(self) -> None:
        super().__init__("Operation canceled by user.")
