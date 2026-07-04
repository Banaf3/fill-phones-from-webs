"""Order details page object — optional fallback for missing export fields.

Only used when explicitly enabled and a mandatory field is missing
from the CSV export. Extracts only configured fields from visible
order detail tabs.
"""

from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Locator, Page

from deliverect_sync.browser.locator_registry import LocatorRegistry
from deliverect_sync.logging_config import get_logger

logger = get_logger("pages.order_details")

# Rate limit: minimum seconds between order detail extractions
_MIN_INTERVAL_SECONDS = 1.0


class OrderDetailsPage:
    """Page Object for individual order detail views.

    This is a fallback mechanism, only activated when:
    1. fallback.order_detail_extraction is True in config
    2. A mandatory field is missing from the CSV export

    Rate-limited and capped at maximum_orders_per_run.
    """

    def __init__(self, page: Page, registry: LocatorRegistry) -> None:
        self._page = page
        self._registry = registry
        self._last_extraction_time: float = 0

    @property
    def page(self) -> Page:
        return self._page

    def open_order(self, order_element: Locator) -> bool:
        """Open an order detail view by clicking an order row.

        Args:
            order_element: The order row/card Locator to click.

        Returns:
            True if a detail view appears to have opened.
        """
        try:
            order_element.click()
            self._page.wait_for_timeout(1500)

            # Check if a detail panel/modal opened
            # Look for common detail indicators
            detail_indicators = [
                self._page.locator("[class*='detail'], [class*='drawer'], [class*='modal']"),
                self._page.get_by_role("dialog"),
            ]

            for indicator in detail_indicators:
                if indicator.count() > 0:
                    logger.debug("Order detail view opened")
                    return True

            return False
        except Exception as e:
            logger.warning("Could not open order: %s", type(e).__name__)
            return False

    def extract_configured_fields(
        self, field_names: list[str]
    ) -> dict[str, str | None]:
        """Extract only the configured fields from the visible detail view.

        Args:
            field_names: List of field names to extract.

        Returns:
            Dict mapping field names to extracted values (None if not found).
        """
        self._rate_limit()

        extracted: dict[str, str | None] = {}

        for field_name in field_names:
            try:
                value = self._extract_field(field_name)
                extracted[field_name] = value
            except Exception:
                extracted[field_name] = None
                logger.debug("Could not extract field: %s", field_name)

        self._last_extraction_time = time.time()
        return extracted

    def _extract_field(self, field_name: str) -> str | None:
        """Extract a single field value from the detail view.

        Looks for labeled values (e.g., "Status: Accepted").
        """
        field_lower = field_name.lower()

        # Try to find a label-value pair
        try:
            # Look for elements containing the field name as a label
            labels = self._page.locator(
                f"dt, th, label, [class*='label'], [class*='key']"
            ).all()

            for label in labels:
                try:
                    label_text = label.inner_text().strip().lower()
                    if field_lower in label_text:
                        # Try to get the adjacent value
                        value_locator = label.locator(
                            "~ dd, ~ td, ~ [class*='value'], + *"
                        )
                        if value_locator.count() > 0:
                            return value_locator.first.inner_text().strip()

                        # Try parent's next sibling
                        parent = label.locator("..")
                        siblings = parent.locator("~ *")
                        if siblings.count() > 0:
                            return siblings.first.inner_text().strip()
                except Exception:
                    continue
        except Exception:
            pass

        return None

    def close_order(self) -> None:
        """Close the order detail view."""
        try:
            # Try close button
            close_buttons = [
                self._page.get_by_role("button", name="Close"),
                self._page.get_by_role("button", name="إغلاق"),
                self._page.locator("[class*='close']"),
                self._page.locator("button[aria-label='Close']"),
            ]

            for close_btn in close_buttons:
                if close_btn.count() > 0:
                    close_btn.first.click()
                    self._page.wait_for_timeout(500)
                    return

            # Fallback: press Escape
            self._page.keyboard.press("Escape")
            self._page.wait_for_timeout(500)

        except Exception as e:
            logger.warning("Could not close order detail: %s", type(e).__name__)
            # Try Escape as last resort
            try:
                self._page.keyboard.press("Escape")
            except Exception:
                pass

    def is_safe_to_continue(self) -> bool:
        """Check if it's safe to continue extracting orders.

        Returns False if:
        - An unexpected dialog appeared
        - The page URL changed unexpectedly
        - A permission error occurred
        """
        try:
            # Check for unexpected dialogs
            dialogs = self._page.get_by_role("alertdialog")
            if dialogs.count() > 0:
                logger.warning("Unexpected dialog detected — stopping extraction")
                return False

            # Check URL is still on orders
            url = self._page.url.lower()
            if "login" in url or "auth" in url:
                logger.warning("Redirected to login — session may have expired")
                return False

            return True
        except Exception:
            return False

    def _rate_limit(self) -> None:
        """Enforce rate limiting between extractions."""
        if self._last_extraction_time > 0:
            elapsed = time.time() - self._last_extraction_time
            if elapsed < _MIN_INTERVAL_SECONDS:
                time.sleep(_MIN_INTERVAL_SECONDS - elapsed)
