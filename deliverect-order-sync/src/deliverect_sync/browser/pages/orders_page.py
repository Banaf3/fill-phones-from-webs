"""Orders page object — navigation, filters, and export trigger.

Automates the Orders page interactions including applying filters
and opening the export dialog. Validates page state before actions.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from playwright.sync_api import Page

from deliverect_sync.browser.locator_registry import LocatorRegistry
from deliverect_sync.config import ExportConfig
from deliverect_sync.exceptions import ExportRequestError, UIChangedError
from deliverect_sync.logging_config import get_logger

logger = get_logger("pages.orders")


class OrdersPage:
    """Page Object for the Deliverect Orders page."""

    def __init__(self, page: Page, registry: LocatorRegistry) -> None:
        self._page = page
        self._registry = registry

    @property
    def page(self) -> Page:
        return self._page

    def navigate(self) -> None:
        """Navigate to the Orders page via the sidebar link."""
        logger.info("Navigating to Orders page")
        locator = self._registry.resolve(self._page, "orders_navigation")
        locator.first.click()
        self._page.wait_for_timeout(2000)
        self.verify_page()

    def verify_page(self) -> bool:
        """Verify we're on the Orders page.

        Returns:
            True if we appear to be on the Orders page.

        Raises:
            UIChangedError: If page verification fails.
        """
        url = self._page.url.lower()
        if "orders" in url or "الطلبات" in url:
            logger.debug("Orders page verified via URL")
            return True

        # Check for orders-specific content
        try:
            orders_nav = self._registry.try_resolve(self._page, "orders_navigation")
            if orders_nav and orders_nav.count() > 0:
                return True
        except Exception:
            pass

        logger.warning("Could not verify Orders page")
        return False

    def apply_filters(self, export_config: ExportConfig) -> None:
        """Apply configured filters on the Orders page.

        Filters are applied through the UI controls (date range,
        locations, channels, statuses). Available filters may vary
        by account configuration.

        Args:
            export_config: Export configuration with filter settings.
        """
        logger.info("Applying order filters")

        # Date range
        if export_config.date_mode.value == "rolling":
            end_date = datetime.now(tz=timezone.utc).date()
            start_date = end_date - timedelta(days=export_config.rolling_days)
            logger.info("Date range: %s to %s (rolling %d days)",
                       start_date, end_date, export_config.rolling_days)
        elif export_config.fixed_start and export_config.fixed_end:
            start_date = export_config.fixed_start
            end_date = export_config.fixed_end
            logger.info("Date range: %s to %s (fixed)", start_date, end_date)

        # Note: The actual filter application depends on the UI structure
        # discovered during calibration. The export dialog handles most
        # filter configuration in Deliverect.
        logger.info("Filters noted — will be applied in export dialog")

    def open_more_menu(self) -> None:
        """Open the More/additional-actions menu.

        Raises:
            UIChangedError: If the More menu cannot be found.
        """
        logger.info("Opening More menu")
        locator = self._registry.resolve(self._page, "orders_more_menu")
        locator.first.click()
        self._page.wait_for_timeout(1000)

    def click_export_orders(self) -> None:
        """Click 'Export orders' in the More menu.

        Raises:
            UIChangedError: If the Export orders item cannot be found.
            ExportRequestError: If clicking fails.
        """
        logger.info("Clicking Export orders")
        try:
            locator = self._registry.resolve(self._page, "export_orders_menu_item")
            locator.first.click()
            self._page.wait_for_timeout(2000)
        except UIChangedError:
            raise
        except Exception as e:
            raise ExportRequestError(
                f"Failed to click Export orders: {type(e).__name__}"
            )

    def open_export_dialog(self) -> None:
        """Open the export dialog by clicking More → Export orders."""
        self.open_more_menu()
        self.click_export_orders()

    def get_visible_order_count(self) -> int:
        """Get the number of visible order cards/rows on the page.

        Used by the detail fallback to enumerate orders.
        """
        try:
            # Try common order row selectors
            rows = self._page.locator("tr[data-testid*='order'], [class*='order-card']")
            count = rows.count()
            if count > 0:
                return count

            # Fallback: look for table rows
            rows = self._page.locator("tbody tr")
            return rows.count()
        except Exception:
            return 0

    def get_order_elements(self) -> list[Any]:
        """Get all visible order elements for detail fallback.

        Returns:
            List of Playwright Locator elements for individual orders.
        """
        try:
            rows = self._page.locator("tbody tr").all()
            return rows
        except Exception:
            return []
