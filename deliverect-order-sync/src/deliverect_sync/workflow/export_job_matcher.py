"""Export job matcher — identifies the correct new export operation.

Prevents downloading an old export by matching new operations against
pre-export state and run timing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from deliverect_sync.browser.pages.operations_page import OperationInfo
from deliverect_sync.exceptions import AmbiguousExportError, ExportOperationNotFoundError
from deliverect_sync.logging_config import get_logger

logger = get_logger("export_job_matcher")


class ExportJobMatcher:
    """Matches a new export operation against pre-export state."""

    def __init__(self) -> None:
        self._before_operations: list[OperationInfo] = []
        self._run_start: datetime | None = None

    def capture_before_state(self, operations: list[OperationInfo]) -> None:
        """Capture the operations visible before requesting an export.

        Args:
            operations: List of operations currently visible.
        """
        self._before_operations = operations
        logger.info("Captured %d pre-export operations", len(operations))

    def set_run_start(self, run_start: datetime) -> None:
        """Record when the export request was made."""
        self._run_start = run_start

    def find_new_operation(
        self, current_operations: list[OperationInfo]
    ) -> OperationInfo:
        """Find the operation that was created by this run.

        Logic:
        1. Filter out operations that existed before the request.
        2. Keep only export-type operations.
        3. If exactly one candidate, return it.
        4. If zero candidates, raise ExportOperationNotFoundError.
        5. If multiple candidates, raise AmbiguousExportError.

        Args:
            current_operations: Operations now visible on the page.

        Returns:
            The matched OperationInfo.

        Raises:
            ExportOperationNotFoundError: No new operation found.
            AmbiguousExportError: Multiple candidates found.
        """
        # Build set of known operation labels
        before_labels = {op.label.strip() for op in self._before_operations}

        # Find new operations
        new_ops = [
            op for op in current_operations
            if op.label.strip() not in before_labels
        ]

        logger.info(
            "Found %d new operations (total: %d, before: %d)",
            len(new_ops), len(current_operations), len(self._before_operations),
        )

        if not new_ops:
            # Check if count increased
            if len(current_operations) > len(self._before_operations):
                # Take the first new entry (most recent)
                new_ops = current_operations[:len(current_operations) - len(self._before_operations)]
            else:
                raise ExportOperationNotFoundError(
                    "No new export operation found. The export may not have "
                    "started successfully, or the page needs to refresh."
                )

        # Filter for export-type operations
        export_ops = [op for op in new_ops if self._is_export_operation(op)]

        if not export_ops:
            # If none clearly marked as export, use all new ones
            export_ops = new_ops

        if len(export_ops) == 0:
            raise ExportOperationNotFoundError(
                "No export operations found among new operations."
            )

        if len(export_ops) == 1:
            matched = export_ops[0]
            logger.info("Matched single new export operation: %s", matched.label[:80])
            return matched

        # Multiple candidates — ambiguous
        logger.warning(
            "Found %d candidate export operations — ambiguous",
            len(export_ops),
        )
        for i, op in enumerate(export_ops):
            logger.info("  Candidate %d: %s", i + 1, op.label[:80])

        raise AmbiguousExportError(len(export_ops))

    @staticmethod
    def _is_export_operation(op: OperationInfo) -> bool:
        """Check if an operation appears to be an export."""
        text = (op.label + " " + op.operation_type).lower()
        export_keywords = ["export", "تصدير", "csv", "download", "report"]
        return any(kw in text for kw in export_keywords)
