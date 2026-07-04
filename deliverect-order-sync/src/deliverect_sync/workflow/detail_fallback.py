"""Detail fallback workflow — extracts fields from individual order pages.

Only activated when:
1. fallback.order_detail_extraction is True
2. A mandatory field is missing from the CSV export

Rate-limited with strict maximum count.
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from deliverect_sync.browser.locator_registry import LocatorRegistry
from deliverect_sync.browser.pages.order_details_page import OrderDetailsPage
from deliverect_sync.browser.pages.orders_page import OrdersPage
from deliverect_sync.config import AppSettings
from deliverect_sync.logging_config import get_logger

logger = get_logger("detail_fallback")


class DetailFallback:
    """Extracts specific fields from individual order detail pages.

    This is a fallback mechanism with strict safety controls:
    - Only runs when explicitly enabled in config
    - Only extracts fields that are configured as needed
    - Rate-limited between extractions
    - Hard cap on number of orders processed
    - Stops immediately on any safety concern
    """

    def __init__(
        self,
        settings: AppSettings,
        page: Page,
        registry: LocatorRegistry,
    ) -> None:
        self._settings = settings
        self._page = page
        self._registry = registry
        self._max_orders = settings.fallback.maximum_orders_per_run
        self._orders_page = OrdersPage(page, registry)
        self._details_page = OrderDetailsPage(page, registry)

    def is_enabled(self) -> bool:
        """Check if detail fallback is enabled in configuration."""
        return self._settings.fallback.order_detail_extraction

    def extract_missing_fields(
        self,
        missing_fields: list[str],
    ) -> list[dict[str, str | None]]:
        """Extract missing fields from visible orders on the Orders page.

        Args:
            missing_fields: Field names not available in the CSV export.

        Returns:
            List of dicts mapping field names to extracted values.
        """
        if not self.is_enabled():
            logger.info("Detail fallback is disabled — skipping")
            return []

        if not missing_fields:
            logger.info("No missing fields — detail fallback not needed")
            return []

        logger.info(
            "Detail fallback: extracting %d fields from up to %d orders",
            len(missing_fields), self._max_orders,
        )

        results: list[dict[str, str | None]] = []
        order_elements = self._orders_page.get_order_elements()

        processed = 0
        for element in order_elements:
            if processed >= self._max_orders:
                logger.info("Reached maximum order count (%d)", self._max_orders)
                break

            # Safety check before each order
            if not self._details_page.is_safe_to_continue():
                logger.warning("Safety check failed — stopping detail extraction")
                break

            try:
                # Open the order
                if not self._details_page.open_order(element):
                    logger.warning("Could not open order %d — skipping", processed + 1)
                    continue

                # Extract configured fields only
                extracted = self._details_page.extract_configured_fields(missing_fields)
                results.append(extracted)

                # Close the order
                self._details_page.close_order()

                processed += 1
                logger.debug("Extracted order %d/%d", processed, self._max_orders)

            except Exception as e:
                logger.warning(
                    "Error extracting order %d: %s — stopping",
                    processed + 1, type(e).__name__,
                )
                try:
                    self._details_page.close_order()
                except Exception:
                    pass
                break

        logger.info("Detail fallback complete: %d orders processed", processed)
        return results
