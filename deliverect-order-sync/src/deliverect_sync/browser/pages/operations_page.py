"""Operations page object — export operation tracking and download.

Handles navigation to Operations, finding new export operations,
bounded polling, and triggering the download link.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from playwright.sync_api import Download, Page

from deliverect_sync.browser.locator_registry import LocatorRegistry
from deliverect_sync.config import PollingConfig
from deliverect_sync.exceptions import (
    AmbiguousExportError,
    DownloadLinkError,
    ExportFailedError,
    ExportOperationNotFoundError,
    ExportTimeoutError,
    UIChangedError,
)
from deliverect_sync.logging_config import get_logger
from deliverect_sync.models import ExportOperationStatus

logger = get_logger("pages.operations")


@dataclass
class OperationInfo:
    """Information about a visible export operation."""

    index: int = 0
    label: str = ""
    operation_type: str = ""
    status_text: str = ""
    created_text: str = ""
    element: Any = None  # Playwright Locator
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, str]:
        return {
            "index": str(self.index),
            "label": self.label,
            "type": self.operation_type,
            "status": self.status_text,
            "created": self.created_text,
        }


class OperationsPage:
    """Page Object for the Deliverect Operations page."""

    def __init__(self, page: Page, registry: LocatorRegistry) -> None:
        self._page = page
        self._registry = registry

    @property
    def page(self) -> Page:
        return self._page

    def navigate(self) -> None:
        """Navigate to the Operations page."""
        logger.info("Navigating to Operations page")
        locator = self._registry.resolve(self._page, "operations_navigation")
        locator.first.click()
        self._page.wait_for_timeout(3000)
        self.verify_page()

    def verify_page(self) -> bool:
        """Verify we're on the Operations page."""
        url = self._page.url.lower()
        if "operations" in url or "العمليات" in url:
            logger.debug("Operations page verified via URL")
            return True

        try:
            ops_nav = self._registry.try_resolve(self._page, "operations_navigation")
            if ops_nav and ops_nav.count() > 0:
                return True
        except Exception:
            pass

        logger.warning("Could not verify Operations page")
        return False

    def get_visible_operations(self) -> list[OperationInfo]:
        """Get all visible export operations on the page.

        Returns:
            List of OperationInfo for each visible operation.
        """
        operations: list[OperationInfo] = []

        try:
            rows = self._registry.try_resolve(self._page, "operation_row")
            if not rows:
                # Fallback: try generic table rows
                rows = self._page.locator("tbody tr, [class*='operation']")

            count = rows.count() if rows else 0
            logger.debug("Found %d operation rows", count)

            for i in range(count):
                try:
                    row = rows.nth(i)
                    text = row.inner_text().strip()

                    info = OperationInfo(
                        index=i,
                        label=text[:200],  # Truncate for safety
                        element=row,
                    )

                    # Try to extract structured data
                    cells = row.locator("td").all()
                    if len(cells) >= 2:
                        info.operation_type = cells[0].inner_text().strip()[:50]
                        info.status_text = cells[-1].inner_text().strip()[:50]
                    if len(cells) >= 3:
                        info.created_text = cells[1].inner_text().strip()[:50]

                    operations.append(info)
                except Exception:
                    continue

        except Exception as e:
            logger.warning("Could not enumerate operations: %s", type(e).__name__)

        return operations

    def find_new_export_operation(
        self,
        before_operations: list[OperationInfo],
        run_start_time: datetime,
    ) -> OperationInfo:
        """Find the newly created export operation.

        Identifies an operation that:
        1. Did not exist before the export request
        2. Was created after the run started
        3. Appears to be an export operation

        Args:
            before_operations: Operations visible before the export request.
            run_start_time: When this sync run started.

        Returns:
            The matched OperationInfo.

        Raises:
            ExportOperationNotFoundError: If no new operation is found.
            AmbiguousExportError: If multiple candidates exist.
        """
        logger.info("Searching for new export operation")

        # Refresh the page to see new operations
        self._page.reload()
        self._page.wait_for_timeout(3000)

        current_operations = self.get_visible_operations()

        # Find operations not in the before list
        before_labels = {op.label for op in before_operations}
        new_operations = [
            op for op in current_operations
            if op.label not in before_labels
        ]

        if not new_operations:
            # Maybe the page didn't refresh — try checking by index
            if len(current_operations) > len(before_operations):
                new_operations = current_operations[:len(current_operations) - len(before_operations)]
            else:
                raise ExportOperationNotFoundError(
                    "No new export operation found after requesting export. "
                    "The export may still be initializing. Try again in a moment."
                )

        # Filter for export-type operations
        export_candidates = [
            op for op in new_operations
            if self._looks_like_export(op)
        ]

        if not export_candidates:
            # If none look specifically like exports, use all new ones
            export_candidates = new_operations

        if len(export_candidates) == 0:
            raise ExportOperationNotFoundError(
                "No new export operation found after filtering."
            )

        if len(export_candidates) > 1:
            raise AmbiguousExportError(len(export_candidates))

        matched = export_candidates[0]
        logger.info("Matched export operation: %s", matched.label[:80])
        return matched

    def wait_for_operation_completion(
        self,
        operation: OperationInfo,
        polling_config: PollingConfig,
    ) -> ExportOperationStatus:
        """Wait for an export operation to complete using bounded polling.

        Args:
            operation: The operation to monitor.
            polling_config: Polling interval and timeout configuration.

        Returns:
            Final status of the operation.

        Raises:
            ExportTimeoutError: If the operation doesn't complete in time.
            ExportFailedError: If the operation fails.
        """
        interval = polling_config.initial_interval_seconds
        max_interval = polling_config.max_interval_seconds
        max_wait = polling_config.max_wait_seconds
        start = time.time()
        poll_count = 0

        logger.info(
            "Polling operation (interval: %ds → %ds, max wait: %ds)",
            interval, max_interval, max_wait,
        )

        while time.time() - start < max_wait:
            poll_count += 1
            status = self._check_operation_status(operation)

            logger.info(
                "Poll #%d: status=%s (elapsed: %ds)",
                poll_count, status.value, int(time.time() - start),
            )

            if status == ExportOperationStatus.SUCCEEDED:
                return status

            if status == ExportOperationStatus.FAILED:
                raise ExportFailedError(
                    "Export operation failed. Check the Operations page for details."
                )

            if status == ExportOperationStatus.EXPIRED:
                raise ExportFailedError(
                    "Export operation expired before completion."
                )

            # Exponential backoff
            time.sleep(interval)
            interval = min(interval * 1.5, max_interval)

        raise ExportTimeoutError(
            f"Export operation did not complete within {max_wait} seconds "
            f"({poll_count} polls)."
        )

    def _check_operation_status(self, operation: OperationInfo) -> ExportOperationStatus:
        """Check the current status of an operation by refreshing the page."""
        try:
            self._page.reload()
            self._page.wait_for_timeout(2000)

            # Re-find the operation
            current_ops = self.get_visible_operations()

            # Match by index or label similarity
            for op in current_ops:
                if self._is_same_operation(op, operation):
                    return self._parse_status(op.status_text)

            # If not found, it might have moved or changed
            logger.warning("Could not re-find operation after refresh")
            return ExportOperationStatus.UNKNOWN

        except Exception as e:
            logger.warning("Status check failed: %s", type(e).__name__)
            return ExportOperationStatus.UNKNOWN

    def expand_operation(self, operation: OperationInfo) -> None:
        """Expand an operation to reveal details and download link."""
        logger.info("Expanding operation")

        try:
            # Try the expand button
            expand_btn = self._registry.try_resolve(self._page, "operation_expand_button")
            if expand_btn and expand_btn.count() > 0:
                expand_btn.first.click()
                self._page.wait_for_timeout(1000)
                return

            # Fallback: click the operation row itself
            if operation.element:
                operation.element.click()
                self._page.wait_for_timeout(1000)
        except Exception as e:
            logger.warning("Could not expand operation: %s", type(e).__name__)

    def get_download_link(self) -> None:
        """Click 'Get download link' to prepare the download.

        Raises:
            DownloadLinkError: If the download link cannot be activated.
        """
        logger.info("Activating download link")

        try:
            locator = self._registry.resolve(self._page, "download_link_button")
            locator.first.click()
            self._page.wait_for_timeout(2000)
        except UIChangedError:
            raise DownloadLinkError(
                "Could not find the 'Get download link' button. "
                "The UI may have changed — run calibration."
            )
        except Exception as e:
            raise DownloadLinkError(
                f"Failed to activate download link: {type(e).__name__}"
            )

    def download_file(self, timeout_ms: int = 30000) -> Download:
        """Wait for and capture a file download.

        Uses Playwright's download event handling.

        Args:
            timeout_ms: Download timeout in milliseconds.

        Returns:
            Playwright Download object.

        Raises:
            DownloadLinkError: If no download starts.
        """
        logger.info("Waiting for download event")

        try:
            # Click the download link and wait for download
            with self._page.expect_download(timeout=timeout_ms) as download_info:
                # The download link should have been clicked already
                # If there's a second click needed:
                dl_button = self._registry.try_resolve(self._page, "download_link_button")
                if dl_button and dl_button.count() > 0:
                    dl_button.first.click()

            download = download_info.value
            logger.info("Download started: %s", download.suggested_filename)
            return download

        except Exception as e:
            raise DownloadLinkError(
                f"Download did not start within {timeout_ms/1000}s: {type(e).__name__}"
            )

    def _looks_like_export(self, op: OperationInfo) -> bool:
        """Check if an operation looks like an export operation."""
        text = (op.label + op.operation_type).lower()
        return any(kw in text for kw in ["export", "تصدير", "csv", "download"])

    def _is_same_operation(self, a: OperationInfo, b: OperationInfo) -> bool:
        """Check if two operations are the same (after page refresh)."""
        if a.index == b.index:
            return True
        # Compare by label similarity
        if a.label and b.label:
            a_clean = a.label[:100].strip()
            b_clean = b.label[:100].strip()
            if a_clean == b_clean:
                return True
        return False

    @staticmethod
    def _parse_status(status_text: str) -> ExportOperationStatus:
        """Parse status text into an ExportOperationStatus."""
        lower = status_text.lower().strip()

        if any(kw in lower for kw in ["success", "completed", "done", "ناجح", "مكتمل"]):
            return ExportOperationStatus.SUCCEEDED
        elif any(kw in lower for kw in ["failed", "error", "فشل", "خطأ"]):
            return ExportOperationStatus.FAILED
        elif any(kw in lower for kw in ["processing", "running", "in progress", "قيد"]):
            return ExportOperationStatus.PROCESSING
        elif any(kw in lower for kw in ["queued", "pending", "waiting", "انتظار"]):
            return ExportOperationStatus.QUEUED
        elif any(kw in lower for kw in ["expired", "منتهي"]):
            return ExportOperationStatus.EXPIRED
        else:
            return ExportOperationStatus.UNKNOWN
