"""Export workflow orchestrator.

Executes the complete export-import-Excel workflow or
the export-only subset. Manages stage transitions and error handling.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deliverect_sync.browser.browser_factory import BrowserFactory
from deliverect_sync.browser.locator_registry import LocatorRegistry
from deliverect_sync.browser.pages.export_dialog import ExportDialog
from deliverect_sync.browser.pages.login_page import LoginPage
from deliverect_sync.browser.pages.operations_page import OperationsPage
from deliverect_sync.browser.pages.orders_page import OrdersPage
from deliverect_sync.browser.session_manager import SessionManager
from deliverect_sync.config import AppSettings
from deliverect_sync.exceptions import (
    AuthExpiredError,
    DeliverectSyncError,
)
from deliverect_sync.logging_config import get_logger
from deliverect_sync.models import RunStatus, SyncRun
from deliverect_sync.storage.database import DatabaseManager
from deliverect_sync.workflow.download_manager import DownloadManager

logger = get_logger("workflow")


class ExportWorkflow:
    """Orchestrates the complete Deliverect export workflow."""

    def __init__(
        self,
        settings: AppSettings,
        db: DatabaseManager,
        run_id: str,
        *,
        dry_run: bool = False,
        export_only: bool = False,
    ) -> None:
        self._settings = settings
        self._db = db
        self._run_id = run_id
        self._dry_run = dry_run
        self._export_only = export_only
        self._run: SyncRun | None = None

    def execute(self) -> SyncRun:
        """Execute the workflow.

        Returns:
            The completed SyncRun record.
        """
        # Create run record
        self._run = SyncRun(
            id=self._run_id,
            started_at=datetime.now(tz=timezone.utc),
            portal=self._settings.portal.base_url,
            locations=self._settings.export.locations,
            channels=self._settings.export.channels,
            statuses=self._settings.export.statuses,
        )

        self._db.create_run(self._run)
        logger.info("Run %s started", self._run_id[:8])
        
        # Acquire the global run lock
        from deliverect_sync.storage.database import DatabaseLockError
        try:
            workflow_lock = self._db.acquire_lock("export_workflow", self._run_id)
        except DatabaseLockError as e:
            self._fail(RunStatus.FAILED, f"Lock error: {e}")
            raise DeliverectSyncError(str(e), status=RunStatus.FAILED)

        try:
            # Stage 1: Authenticate
            self._stage("authenticate")
            factory = BrowserFactory(self._settings)
            session_mgr = SessionManager(self._settings)

            if not session_mgr.auth_manager.has_state():
                raise AuthExpiredError()

            with factory:
                browser = factory.create_browser(
                    headless=self._settings.browser.headless_for_scheduled
                )
                context, temp_path = factory.create_authenticated_context(browser)

                try:
                    page = context.new_page()
                    page.goto(self._settings.portal.base_url, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)

                    # Verify session
                    login_page = LoginPage(page)
                    if login_page.is_login_page():
                        self._fail(RunStatus.AUTH_REQUIRED, "Session expired")
                        raise AuthExpiredError()

                    # Load locators
                    self._stage("load_selectors")
                    registry = LocatorRegistry()

                    # Check permissions
                    self._stage("check_permissions")
                    session_mgr.check_permissions(page, registry)

                    # Stage 2: Open Orders
                    self._stage("open_orders")
                    orders_page = OrdersPage(page, registry)
                    orders_page.navigate()

                    # Stage 3: Apply filters
                    self._stage("apply_filters")
                    orders_page.apply_filters(self._settings.export)

                    # Stage 4: Capture pre-export operations
                    self._stage("capture_pre_export_state")
                    ops_page = OperationsPage(page, registry)

                    # Navigate to operations first to capture existing state
                    ops_page.navigate()
                    before_operations = ops_page.get_visible_operations()
                    logger.info("Captured %d existing operations", len(before_operations))

                    # Navigate back to Orders
                    orders_page.navigate()

                    # Stage 5: Request export
                    self._stage("request_export")
                    run_start = datetime.now(tz=timezone.utc)
                    orders_page.open_export_dialog()

                    export_dialog = ExportDialog(page, registry)
                    field_mappings = export_dialog.configure_and_request(
                        self._settings.export
                    )

                    # Store field mappings
                    self._run.export_operation_id = "pending"
                    self._db.update_run(self._run)

                    # Stage 6: Find the new export operation
                    self._stage("find_export_operation")
                    ops_page.navigate()
                    matched_op = ops_page.find_new_export_operation(
                        before_operations, run_start
                    )

                    self._run.export_operation_id = matched_op.label[:200]
                    self._db.update_run(self._run)

                    # Stage 7: Wait for completion
                    self._stage("wait_for_export")
                    ops_page.wait_for_operation_completion(
                        matched_op, self._settings.polling
                    )

                    # Stage 8: Download CSV
                    self._stage("download_csv")
                    ops_page.expand_operation(matched_op)
                    ops_page.get_download_link()

                    download_mgr = DownloadManager(self._settings)
                    csv_path = download_mgr.handle_download(page)

                    self._run.downloaded_filename = csv_path.name
                    self._run.file_hash = download_mgr.last_hash
                    self._db.update_run(self._run)

                    if self._export_only:
                        self._run.result = RunStatus.SUCCESS
                        self._run.finished_at = datetime.now(tz=timezone.utc)
                        self._db.update_run(self._run)
                        logger.info("Export-only run completed: %s", csv_path.name)
                        return self._run

                    # Stage 9: Import CSV
                    self._stage("import_csv")
                    from deliverect_sync.importers.order_importer import OrderImporter
                    from deliverect_sync.models import SourceFileOrigin

                    importer = OrderImporter(self._settings, self._db, workflow_lock)
                    import_result = importer.import_csv(
                        csv_path, 
                        self._run_id, 
                        origin=SourceFileOrigin.AUTOMATED_DOWNLOAD
                    )

                    self._run.imported_rows = import_result.imported_rows
                    self._run.new_orders = import_result.new_orders
                    self._run.updated_orders = import_result.updated_orders
                    self._run.rejected_rows = import_result.rejected_rows

                    # Stage 10: Generate Excel
                    self._stage("generate_excel")
                    from deliverect_sync.exporters.excel_exporter import ExcelExporter

                    exporter = ExcelExporter(self._settings, self._db)
                    output_path = exporter.export(self._run_id)
                    logger.info("Excel output: %s", output_path)

                    # Stage 11: Audit log
                    self._stage("audit")
                    logger.info(
                        "Run complete audit details: %s",
                        json.dumps({
                            "imported": import_result.imported_rows,
                            "new": import_result.new_orders,
                            "updated": import_result.updated_orders,
                            "rejected": import_result.rejected_rows,
                            "output": str(output_path),
                        })
                    )

                    # Finalize run status
                    self._run.result = (
                        RunStatus.SUCCESS_WITH_WARNINGS
                        if import_result.rejected_rows > 0
                        else RunStatus.SUCCESS
                    )
                    if import_result.imported_rows == 0:
                        self._run.result = RunStatus.NO_ORDERS

                    self._run.finished_at = datetime.now(tz=timezone.utc)
                    self._db.update_run(self._run)
                    
                    # Stage 12: Data Lifecycle Cleanup
                    self._stage("cleanup")
                    if not self._settings.privacy.retain_raw_csv:
                        try:
                            if csv_path.exists():
                                csv_path.unlink()
                                logger.info("Deleted automated download: %s", csv_path.name)
                        except OSError as e:
                            logger.error("Failed to delete CSV: %s", e)
                            
                    logger.info("Run %s completed: %s", self._run_id[:8], self._run.result.value)
                    return self._run

                finally:
                    context.close()
                    if temp_path and temp_path.exists():
                        temp_path.unlink()

        except DeliverectSyncError as e:
            self._fail(e.status, str(e))
            raise
        except Exception as e:
            self._fail(RunStatus.EXPORT_FAILED, f"Unexpected error: {type(e).__name__}")
            raise
        finally:
            # Release lock
            if workflow_lock:
                try:
                    self._db.release_lock(workflow_lock)
                except Exception:
                    pass

    def _stage(self, name: str) -> None:
        """Log a stage transition."""
        logger.info("━━ Stage: %s", name)

    def _fail(self, status: RunStatus, message: str) -> None:
        """Record a run failure."""
        if self._run:
            self._run.result = status
            self._run.error_message = message
            self._run.finished_at = datetime.now(tz=timezone.utc)
            try:
                self._db.update_run(self._run)
            except Exception:
                pass
        logger.error("Run failed: [%s] %s", status.value, message)
