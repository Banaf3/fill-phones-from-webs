"""Calibration wizard for discovering and validating Playwright locators.

Opens the live Deliverect account and guides the user through
identifying UI elements. Saves a locator profile without storing
customer data.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from playwright.sync_api import Page

from deliverect_sync.browser.browser_factory import BrowserFactory
from deliverect_sync.browser.session_manager import SessionManager
from deliverect_sync.config import AppSettings
from deliverect_sync.exceptions import AuthExpiredError, CalibrationRequiredError
from deliverect_sync.logging_config import get_logger

logger = get_logger("calibration")


# Elements to calibrate, in order
_CALIBRATION_STEPS = [
    {
        "name": "orders_navigation",
        "instruction": "Navigate to the ORDERS page using the sidebar/navigation menu.",
        "page": "any",
        "description": "Orders navigation link",
    },
    {
        "name": "orders_more_menu",
        "instruction": "On the Orders page, look for the 'More' or '⋯' button (additional actions).",
        "page": "orders",
        "description": "More actions menu button",
    },
    {
        "name": "export_orders_menu_item",
        "instruction": "Click the More menu, then identify the 'Export orders' menu item.",
        "page": "orders",
        "description": "Export orders menu item",
    },
    {
        "name": "export_dialog",
        "instruction": "The export dialog should now be open. Verify you see it.",
        "page": "orders",
        "description": "Export orders dialog",
    },
    {
        "name": "export_request_button",
        "instruction": "Find the 'Request export' button in the dialog.",
        "page": "orders",
        "description": "Request export button",
    },
    {
        "name": "operations_navigation",
        "instruction": "Close the dialog and navigate to the OPERATIONS page.",
        "page": "any",
        "description": "Operations navigation link",
    },
    {
        "name": "download_link_button",
        "instruction": "Find and expand a completed export operation, then identify 'Get download link'.",
        "page": "operations",
        "description": "Download link button",
    },
]


class CalibrationWizard:
    """Interactive wizard for discovering UI locators."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._factory = BrowserFactory(settings)
        self._discovered: dict[str, dict[str, Any]] = {}

    def run(self) -> None:
        """Execute the calibration wizard."""
        with self._factory:
            browser = self._factory.create_browser(headless=False)
            context, temp_path = self._factory.create_authenticated_context(browser)

            try:
                page = context.new_page()

                # Navigate to portal
                page.goto(self._settings.portal.base_url, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                # Verify authentication
                session_mgr = SessionManager(self._settings)
                if not session_mgr._is_authenticated(page):
                    raise AuthExpiredError()

                # Run calibration steps
                for step in _CALIBRATION_STEPS:
                    self._calibrate_step(page, step)

                # Generate page fingerprint
                fingerprint = self._generate_fingerprint(page)

                # Build and save selectors
                self._save_selectors(fingerprint)

                # Validation test
                self._validate(page)

                logger.info("Calibration complete — selectors saved")

            finally:
                context.close()
                if temp_path and temp_path.exists():
                    temp_path.unlink()

    def _calibrate_step(self, page: Page, step: dict[str, Any]) -> None:
        """Run a single calibration step.

        Inspects the page for accessible roles and labels,
        proposes candidate locators, and asks the user to verify.
        """
        element_name = step["name"]
        instruction = step["instruction"]
        description = step["description"]

        print(f"\n{'='*60}")
        print(f"  Calibrating: {description}")
        print(f"  {instruction}")
        print(f"{'='*60}")

        input("\nPress Enter when ready to inspect the page...")

        # Discover candidates
        candidates = self._discover_candidates(page, element_name, description)

        if candidates:
            print(f"\n  Found {len(candidates)} candidate locator(s):")
            for i, candidate in enumerate(candidates):
                print(f"    [{i+1}] {candidate['strategy']}: {candidate.get('description', '')}")

            choice = input(f"\n  Accept candidates? (y/n/skip) [y]: ").strip().lower()

            if choice == "skip":
                print(f"  Skipped {element_name}")
                return
            elif choice in ("", "y", "yes"):
                self._discovered[element_name] = {
                    "alternatives": candidates,
                }
                print(f"  ✓ Saved {len(candidates)} alternative(s) for {element_name}")
            else:
                print(f"  Skipped {element_name} — re-run calibration to retry")
        else:
            print(f"\n  [!] Could not auto-discover candidates for {element_name}.")
            print("      This element will need manual configuration in selectors.yaml.")

    def _discover_candidates(
        self, page: Page, element_name: str, description: str
    ) -> list[dict[str, Any]]:
        """Discover candidate locators by inspecting accessible roles and labels.

        Returns a list of locator alternative dicts.
        """
        candidates: list[dict[str, Any]] = []

        # Strategy 1: Look for links/buttons with matching text
        role_types = ["link", "button", "menuitem", "checkbox", "combobox"]
        for role in role_types:
            try:
                elements = page.get_by_role(role).all()
                for elem in elements:
                    try:
                        name = elem.get_attribute("aria-label") or elem.inner_text()
                        name = name.strip()[:100]  # Truncate for safety

                        # Skip elements with PII-like content
                        if self._looks_like_pii(name):
                            continue

                        if name and len(name) > 1:
                            # Check if this could be our target
                            if self._is_likely_match(name, element_name, description):
                                candidates.append({
                                    "strategy": "role",
                                    "role": role,
                                    "name_regex": f"^{re.escape(name)}$",
                                    "description": f"role={role}, name='{name}'",
                                })
                    except Exception:
                        continue
            except Exception:
                continue

        # Strategy 2: CSS selectors based on test IDs or href
        try:
            # Look for test IDs
            test_id_elements = page.locator("[data-testid]").all()
            for elem in test_id_elements:
                try:
                    test_id = elem.get_attribute("data-testid") or ""
                    if self._is_likely_match(test_id, element_name, description):
                        candidates.append({
                            "strategy": "css",
                            "selector": f"[data-testid='{test_id}']",
                            "description": f"data-testid='{test_id}'",
                        })
                except Exception:
                    continue
        except Exception:
            pass

        # Deduplicate
        seen: set[str] = set()
        unique_candidates: list[dict[str, Any]] = []
        for c in candidates:
            key = json.dumps(c, sort_keys=True)
            if key not in seen:
                seen.add(key)
                unique_candidates.append(c)

        return unique_candidates[:5]  # Limit to top 5

    def _is_likely_match(self, text: str, element_name: str, description: str) -> bool:
        """Check if discovered text is likely our target element."""
        text_lower = text.lower()
        name_parts = element_name.replace("_", " ").lower().split()
        desc_parts = description.lower().split()

        # Check if any keyword matches
        keywords = set(name_parts + desc_parts)
        for keyword in keywords:
            if keyword in text_lower:
                return True

        return False

    @staticmethod
    def _looks_like_pii(text: str) -> bool:
        """Check if text looks like PII (customer names, phones, etc.)."""
        # Phone patterns
        if re.search(r"\+?\d{8,}", text):
            return True
        # Email patterns
        if re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.", text):
            return True
        # If it contains only digits (order numbers are OK, but long ones are suspicious)
        if re.match(r"^\d{10,}$", text):
            return True
        return False

    def _generate_fingerprint(self, page: Page) -> dict[str, Any]:
        """Generate a page fingerprint without PII.

        Captures:
        - pathname
        - page title
        - non-sensitive navigation items
        - structural role hash
        """
        fingerprint: dict[str, Any] = {
            "pathname": page.url.split("//", 1)[-1].split("/", 1)[-1] if "//" in page.url else "",
            "title": page.title(),
            "captured_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        # Capture nav item names (non-PII)
        nav_items: list[str] = []
        try:
            links = page.get_by_role("link").all()
            for link in links[:20]:  # Limit
                try:
                    text = link.inner_text().strip()
                    if text and len(text) < 30 and not self._looks_like_pii(text):
                        nav_items.append(text)
                except Exception:
                    continue
        except Exception:
            pass

        fingerprint["nav_items"] = nav_items

        # Structural hash (roles present)
        structure_parts: list[str] = []
        for role in ["navigation", "main", "complementary", "banner", "contentinfo"]:
            try:
                count = page.get_by_role(role).count()
                structure_parts.append(f"{role}:{count}")
            except Exception:
                pass

        structure_str = "|".join(structure_parts)
        fingerprint["structure_hash"] = hashlib.sha256(structure_str.encode()).hexdigest()[:16]

        return fingerprint

    def _save_selectors(self, fingerprint: dict[str, Any]) -> None:
        """Save discovered selectors to selectors.yaml."""
        output: dict[str, Any] = {
            "ui_profile": {
                "portal": self._settings.portal.type.value,
                "language": self._detect_language(fingerprint),
                "fingerprint": fingerprint.get("structure_hash"),
                "calibrated_at": datetime.now(tz=timezone.utc).isoformat(),
            },
        }

        # Merge discovered selectors with defaults from example
        example_path = Path.cwd() / "selectors.example.yaml"
        if example_path.exists():
            with open(example_path, encoding="utf-8") as f:
                defaults = yaml.safe_load(f) or {}

            # Use discovered selectors where available, fall back to defaults
            for key, value in defaults.items():
                if key == "ui_profile":
                    continue
                if key in self._discovered:
                    output[key] = self._discovered[key]
                else:
                    output[key] = value
        else:
            output.update(self._discovered)

        # Save
        selectors_path = Path.cwd() / "selectors.yaml"
        with open(selectors_path, "w", encoding="utf-8") as f:
            yaml.dump(output, f, default_flow_style=False, allow_unicode=True)

        logger.info("Selectors saved to %s", selectors_path)

    def _detect_language(self, fingerprint: dict[str, Any]) -> str:
        """Detect interface language from navigation items."""
        nav_items = fingerprint.get("nav_items", [])
        arabic_count = sum(1 for item in nav_items if re.search(r"[\u0600-\u06FF]", item))
        if arabic_count > len(nav_items) / 2:
            return "ar"
        return "en"

    def _validate(self, page: Page) -> None:
        """Run a non-destructive validation of discovered selectors."""
        print(f"\n{'='*60}")
        print("  Running validation...")
        print(f"{'='*60}")

        success = 0
        failed = 0

        for name, definition in self._discovered.items():
            try:
                # Try each alternative
                found = False
                for alt in definition.get("alternatives", []):
                    try:
                        strategy = alt["strategy"]
                        if strategy == "role":
                            role = alt["role"]
                            name_re = alt.get("name_regex")
                            if name_re:
                                locator = page.get_by_role(role, name=re.compile(name_re))
                            else:
                                locator = page.get_by_role(role)
                            if locator.count() > 0:
                                found = True
                                break
                        elif strategy == "css":
                            locator = page.locator(alt["selector"])
                            if locator.count() > 0:
                                found = True
                                break
                    except Exception:
                        continue

                if found:
                    print(f"  ✓ {name}")
                    success += 1
                else:
                    print(f"  ✗ {name} — not found on current page")
                    failed += 1
            except Exception:
                print(f"  ✗ {name} — error during validation")
                failed += 1

        print(f"\n  Results: {success} passed, {failed} failed")
        if failed > 0:
            print("  Some elements may only be visible on specific pages (e.g., Operations).")
            print("  Re-run calibration if needed.")
