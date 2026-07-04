"""Playwright browser and context factory.

Creates Chromium browser instances with optional saved auth state.
No stealth fingerprinting — this is an authorized business workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright

from deliverect_sync.config import AppSettings
from deliverect_sync.logging_config import get_logger
from deliverect_sync.security.auth_state import AuthStateManager

logger = get_logger("browser_factory")


class BrowserFactory:
    """Creates and manages Playwright browser instances."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def start(self) -> Playwright:
        """Start the Playwright engine."""
        if self._playwright is None:
            self._playwright = sync_playwright().start()
            logger.debug("Playwright engine started")
        return self._playwright

    def create_browser(self, *, headless: bool = False) -> Browser:
        """Launch a Chromium browser.

        Args:
            headless: If True, run without a visible window.

        Returns:
            Playwright Browser instance.
        """
        pw = self.start()
        self._browser = pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )
        logger.info("Browser launched (headless=%s)", headless)
        return self._browser

    def create_context(
        self,
        browser: Browser,
        *,
        storage_state_path: Path | str | None = None,
        storage_state_dict: dict[str, Any] | None = None,
    ) -> BrowserContext:
        """Create a browser context with optional auth state.

        Args:
            browser: The browser to create a context in.
            storage_state_path: Path to a JSON storage state file.
            storage_state_dict: Storage state as a dictionary.

        Returns:
            BrowserContext ready for navigation.
        """
        context_args: dict[str, Any] = {
            "viewport": {"width": 1280, "height": 800},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        if storage_state_path:
            context_args["storage_state"] = str(storage_state_path)
        elif storage_state_dict:
            context_args["storage_state"] = storage_state_dict

        context = browser.new_context(**context_args)
        context.set_default_timeout(self._settings.browser.navigation_timeout)

        logger.debug("Browser context created (with_auth=%s)", bool(storage_state_path or storage_state_dict))
        return context

    def create_authenticated_context(self, browser: Browser) -> tuple[BrowserContext, Path | None]:
        """Create a context with saved encrypted auth state.

        Returns:
            Tuple of (BrowserContext, temp_state_path).
            The caller MUST delete temp_state_path after use.
        """
        auth_mgr = AuthStateManager(self._settings.auth_dir)

        if not auth_mgr.has_state():
            logger.info("No saved auth state — creating fresh context")
            return self.create_context(browser), None

        # Decrypt auth state to a temp file
        temp_path = auth_mgr.write_state_for_playwright()
        try:
            context = self.create_context(browser, storage_state_path=temp_path)
            return context, temp_path
        except Exception:
            # Clean up on failure
            if temp_path.exists():
                temp_path.unlink()
            raise

    def close(self) -> None:
        """Close browser and Playwright engine."""
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        logger.debug("Browser factory closed")

    def __enter__(self) -> BrowserFactory:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
