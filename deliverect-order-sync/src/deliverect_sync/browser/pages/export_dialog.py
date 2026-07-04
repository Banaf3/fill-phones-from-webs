"""Export dialog page object — field mapping and export request.

Handles the Deliverect export dialog: date range, locations,
channels, statuses, field selection, format, and export request.
Flexibly maps requested logical fields to available UI fields.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from playwright.sync_api import Locator, Page

from deliverect_sync.browser.locator_registry import LocatorRegistry
from deliverect_sync.config import ExportConfig
from deliverect_sync.exceptions import ExportRequestError, UIChangedError
from deliverect_sync.logging_config import get_logger
from deliverect_sync.models import FieldMapping, MappingConfidence, MappingStatus

logger = get_logger("pages.export_dialog")

# Flexible field name matching — maps canonical names to possible UI labels
_FIELD_LABEL_ALIASES: dict[str, list[str]] = {
    "Pickup time": ["pickup time", "pick-up time", "collection time", "وقت الاستلام"],
    "Location": ["location", "branch", "store", "الموقع", "الفرع"],
    "Order ID": ["order id", "channel order id", "external order id", "رقم الطلب"],
    "Channel": ["channel", "platform", "القناة", "المنصة"],
    "Delivery System": ["delivery system", "delivery", "نظام التوصيل"],
    "Receipt ID": ["receipt id", "pos receipt id", "رقم الإيصال"],
    "Status": ["status", "order status", "الحالة"],
    "Items": ["items", "products", "العناصر", "المنتجات"],
    "PLU": ["plu", "sku", "product code"],
    "Quantity": ["quantity", "qty", "الكمية"],
    "Total": ["total", "order total", "amount", "الإجمالي", "المبلغ"],
    "Fees": ["fees", "delivery fee", "service fee", "الرسوم"],
    "Payment method": ["payment method", "payment type", "طريقة الدفع"],
    "Paid": ["paid", "payment state", "is paid", "مدفوع"],
}


class ExportDialog:
    """Page Object for the Deliverect export orders dialog."""

    def __init__(self, page: Page, registry: LocatorRegistry) -> None:
        self._page = page
        self._registry = registry
        self._available_fields: list[str] = []
        self._field_mappings: list[FieldMapping] = []

    @property
    def field_mappings(self) -> list[FieldMapping]:
        """Get the field mapping results."""
        return self._field_mappings

    @property
    def available_fields(self) -> list[str]:
        """Get the list of available field labels in the dialog."""
        return self._available_fields

    def wait_for_dialog(self) -> None:
        """Wait for the export dialog to appear.

        Raises:
            UIChangedError: If the dialog is not found.
        """
        logger.info("Waiting for export dialog")
        try:
            locator = self._registry.resolve(self._page, "export_dialog")
            locator.first.wait_for(state="visible", timeout=5000)
            logger.debug("Export dialog visible")
        except Exception as e:
            raise UIChangedError("export_dialog", details=str(e))

    def verify_dialog_title(self) -> bool:
        """Verify the dialog title matches expected text."""
        try:
            dialog = self._registry.resolve(self._page, "export_dialog")
            text = dialog.first.inner_text()
            # Check for export-related title
            lower = text.lower()
            if any(kw in lower for kw in ["export", "تصدير"]):
                return True
        except Exception:
            pass
        return False

    def configure_date_range(self, config: ExportConfig) -> None:
        """Set the pickup date/time range in the dialog.

        Args:
            config: Export configuration with date range settings.
        """
        from datetime import datetime, timezone

        if config.date_mode.value == "rolling":
            end_date = datetime.now(tz=timezone.utc).date()
            start_date = end_date - timedelta(days=config.rolling_days)
        elif config.fixed_start and config.fixed_end:
            start_date = config.fixed_start
            end_date = config.fixed_end
        else:
            logger.warning("No date range configured — using dialog defaults")
            return

        logger.info("Setting date range: %s to %s", start_date, end_date)

        # Try to find and fill date inputs
        try:
            start_input = self._registry.try_resolve(self._page, "export_date_start")
            if start_input and start_input.count() > 0:
                start_input.first.fill(start_date.isoformat())

            end_input = self._registry.try_resolve(self._page, "export_date_end")
            if end_input and end_input.count() > 0:
                end_input.first.fill(end_date.isoformat())
        except Exception as e:
            logger.warning("Could not set date range inputs: %s", type(e).__name__)

    def select_locations(self, locations: list[str]) -> None:
        """Select location checkboxes/dropdown options."""
        if not locations:
            return

        logger.info("Selecting locations: %s", locations)
        try:
            select = self._registry.try_resolve(self._page, "export_location_select")
            if select and select.count() > 0:
                for location in locations:
                    self._select_dropdown_option(select.first, location)
        except Exception as e:
            logger.warning("Could not select locations: %s", type(e).__name__)

    def select_channels(self, channels: list[str]) -> None:
        """Select channel checkboxes/dropdown options."""
        if not channels:
            return

        logger.info("Selecting channels: %s", channels)
        try:
            select = self._registry.try_resolve(self._page, "export_channel_select")
            if select and select.count() > 0:
                for channel in channels:
                    self._select_dropdown_option(select.first, channel)
        except Exception as e:
            logger.warning("Could not select channels: %s", type(e).__name__)

    def select_statuses(self, statuses: list[str]) -> None:
        """Select order status checkboxes/dropdown options."""
        if not statuses:
            return

        logger.info("Selecting statuses: %s", statuses)
        try:
            select = self._registry.try_resolve(self._page, "export_status_select")
            if select and select.count() > 0:
                for status_val in statuses:
                    self._select_dropdown_option(select.first, status_val)
        except Exception as e:
            logger.warning("Could not select statuses: %s", type(e).__name__)

    def set_custom_tags(self, tags: list[str]) -> None:
        """Set custom tags if available."""
        if not tags:
            return
        logger.info("Custom tags requested but may not be available: %s", tags)

    def discover_available_fields(self) -> list[str]:
        """Inspect the dialog to find available export field options.

        Returns:
            List of available field label strings.
        """
        logger.info("Discovering available export fields")
        fields: list[str] = []

        try:
            # Look for checkboxes in the dialog
            dialog = self._registry.try_resolve(self._page, "export_dialog")
            if dialog:
                checkboxes = dialog.first.get_by_role("checkbox").all()
                for cb in checkboxes:
                    try:
                        # Get the label text
                        label = cb.get_attribute("aria-label") or ""
                        if not label:
                            # Try getting adjacent text
                            parent = cb.locator("..")
                            label = parent.inner_text().strip()

                        if label and len(label) < 100:
                            fields.append(label)
                    except Exception:
                        continue

            # Also check for labeled inputs
            if not fields:
                labels = self._page.locator("label").all()
                for label_el in labels:
                    try:
                        text = label_el.inner_text().strip()
                        if text and len(text) < 100:
                            fields.append(text)
                    except Exception:
                        continue

        except Exception as e:
            logger.warning("Could not discover fields: %s", type(e).__name__)

        self._available_fields = fields
        logger.info("Discovered %d available fields", len(fields))
        return fields

    def map_requested_fields(
        self, requested: list[str], mandatory: list[str] | None = None
    ) -> list[FieldMapping]:
        """Map requested logical field names to available UI field labels.

        Uses flexible matching with aliases and fuzzy comparison.

        Args:
            requested: List of requested field names from config.
            mandatory: Fields that must be available (export fails if missing).

        Returns:
            List of FieldMapping results.

        Raises:
            ExportRequestError: If a mandatory field is unavailable.
        """
        mandatory = mandatory or []
        available_lower = {f.lower().strip(): f for f in self._available_fields}
        mappings: list[FieldMapping] = []

        for field_name in requested:
            mapping = self._match_field(field_name, available_lower)
            mappings.append(mapping)

            if mapping.status == MappingStatus.UNAVAILABLE and field_name in mandatory:
                raise ExportRequestError(
                    f"Mandatory field '{field_name}' is not available in the export dialog. "
                    f"Available fields: {', '.join(self._available_fields)}"
                )

        self._field_mappings = mappings
        mapped_count = sum(1 for m in mappings if m.status == MappingStatus.MAPPED)
        logger.info("Field mapping: %d/%d mapped", mapped_count, len(requested))

        return mappings

    def _match_field(
        self, field_name: str, available: dict[str, str]
    ) -> FieldMapping:
        """Match a single field name to an available UI label."""
        field_lower = field_name.lower().strip()

        # Exact match
        if field_lower in available:
            return FieldMapping(
                source_header=available[field_lower],
                canonical_field=field_name,
                confidence=MappingConfidence.EXACT,
                status=MappingStatus.MAPPED,
            )

        # Alias match
        aliases = _FIELD_LABEL_ALIASES.get(field_name, [])
        for alias in aliases:
            if alias.lower() in available:
                return FieldMapping(
                    source_header=available[alias.lower()],
                    canonical_field=field_name,
                    confidence=MappingConfidence.ALIAS,
                    status=MappingStatus.MAPPED,
                )

        # Fuzzy: check if any available field contains the key term
        for avail_lower, avail_original in available.items():
            if field_lower in avail_lower or avail_lower in field_lower:
                return FieldMapping(
                    source_header=avail_original,
                    canonical_field=field_name,
                    confidence=MappingConfidence.FUZZY,
                    status=MappingStatus.MAPPED,
                )

        # Not found
        return FieldMapping(
            source_header="",
            canonical_field=field_name,
            confidence=MappingConfidence.UNMAPPED,
            status=MappingStatus.UNAVAILABLE,
        )

    def select_fields(self, mappings: list[FieldMapping]) -> None:
        """Select or deselect field checkboxes based on mappings.

        Args:
            mappings: Field mapping results from map_requested_fields.
        """
        for mapping in mappings:
            if mapping.status == MappingStatus.MAPPED and mapping.source_header:
                try:
                    checkbox = self._registry.resolve(
                        self._page,
                        "export_field_checkbox",
                        template_vars={"field_name": re.escape(mapping.source_header)},
                    )
                    if checkbox.count() > 0 and not checkbox.first.is_checked():
                        checkbox.first.check()
                        logger.debug("Selected field: %s", mapping.source_header)
                except Exception as e:
                    logger.warning(
                        "Could not select field '%s': %s",
                        mapping.source_header,
                        type(e).__name__,
                    )

    def set_format(self, format_type: str = "csv") -> None:
        """Set the export format (CSV)."""
        logger.info("Setting export format: %s", format_type)
        try:
            format_select = self._registry.try_resolve(self._page, "export_format_select")
            if format_select and format_select.count() > 0:
                self._select_dropdown_option(format_select.first, format_type)
        except Exception as e:
            logger.warning("Could not set format: %s", type(e).__name__)

    def set_split_items(self, split: bool) -> None:
        """Toggle 'split items into separate rows'."""
        logger.info("Setting split items: %s", split)
        try:
            toggle = self._registry.try_resolve(self._page, "export_split_items_toggle")
            if toggle and toggle.count() > 0:
                is_checked = toggle.first.is_checked()
                if split and not is_checked:
                    toggle.first.check()
                elif not split and is_checked:
                    toggle.first.uncheck()
        except Exception as e:
            logger.warning("Could not set split items: %s", type(e).__name__)

    def request_export(self) -> None:
        """Click the 'Request export' button.

        Raises:
            ExportRequestError: If the button cannot be found or clicked.
        """
        logger.info("Requesting export")

        # Validate dialog state before clicking
        if not self.verify_dialog_title():
            logger.warning("Dialog title verification failed — proceeding with caution")

        try:
            button = self._registry.resolve(self._page, "export_request_button")
            button.first.click()
            self._page.wait_for_timeout(2000)
            logger.info("Export request submitted")
        except UIChangedError:
            raise
        except Exception as e:
            raise ExportRequestError(
                f"Failed to click Request export: {type(e).__name__}"
            )

    def configure_and_request(self, config: ExportConfig) -> list[FieldMapping]:
        """Configure all dialog options and request the export.

        This is the high-level method that handles the entire dialog flow.

        Args:
            config: Export configuration.

        Returns:
            Field mapping results.
        """
        self.wait_for_dialog()
        self.configure_date_range(config)
        self.select_locations(config.locations)
        self.select_channels(config.channels)
        self.select_statuses(config.statuses)
        self.set_custom_tags(config.custom_tags)

        # Discover and map fields
        self.discover_available_fields()
        mappings = self.map_requested_fields(
            config.requested_fields,
            config.mandatory_fields,
        )
        self.select_fields(mappings)

        # Format and split settings
        self.set_format(config.format)
        self.set_split_items(config.split_items_into_separate_rows)

        # Request the export
        self.request_export()

        return mappings

    def _select_dropdown_option(self, select_locator: Locator, option_text: str) -> None:
        """Select an option from a dropdown or multi-select.

        Handles both native <select> and custom dropdowns.
        """
        try:
            # Try native select first
            select_locator.select_option(label=option_text)
            return
        except Exception:
            pass

        try:
            # Custom dropdown: click to open, then click the option
            select_locator.click()
            self._page.wait_for_timeout(500)

            option = self._page.get_by_text(option_text, exact=True)
            if option.count() > 0:
                option.first.click()
                self._page.wait_for_timeout(300)
                return

            # Try partial match
            option = self._page.get_by_text(option_text, exact=False)
            if option.count() > 0:
                option.first.click()
                self._page.wait_for_timeout(300)
        except Exception:
            logger.warning("Could not select option: %s", option_text)
