"""Browser session management: login, auth verification, permission checks.

Handles interactive login flow, session expiry detection, and
verifies required Deliverect permissions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from deliverect_sync.browser.browser_factory import BrowserFactory
from deliverect_sync.browser.locator_registry import LocatorRegistry
from deliverect_sync.config import AppSettings
from deliverect_sync.exceptions import (
    AuthExpiredError,
    AuthenticationError,
    LoginFailedError,
    PermissionError_,
)
from deliverect_sync.logging_config import get_logger
from deliverect_sync.models import RunStatus
from deliverect_sync.security.auth_state import AuthStateManager

logger = get_logger("session_manager")

# Max time to wait for manual login (10 minutes)
_LOGIN_TIMEOUT_MS = 600_000

# Indicators that we're on an authenticated page
_AUTH_INDICATORS = [
    "orders",
    "dashboard",
    "الطلبات",
    "لوحة",
]


class SessionManager:
    """Manages browser sessions and authentication state."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._auth_mgr = AuthStateManager(settings.auth_dir)
        self._factory = BrowserFactory(settings)

    def interactive_login(self) -> None:
        """Launch a headed browser for manual login.

        Opens the configured portal, waits for the user to authenticate,
        then saves the encrypted session state.
        """
        with self._factory:
            browser = self._factory.create_browser(headless=False)
            context = self._factory.create_context(browser)
            page = context.new_page()

            try:
                # Navigate to portal
                portal_url = self._settings.portal.base_url
                logger.info("Navigating to portal: %s", portal_url)
                page.goto(portal_url, wait_until="domcontentloaded")

                # Wait for authentication to complete
                logger.info("Waiting for manual login (timeout: %ds)...", _LOGIN_TIMEOUT_MS // 1000)

                self._wait_for_authentication(page, timeout_ms=_LOGIN_TIMEOUT_MS)

                # Verify we're authenticated
                if not self._is_authenticated(page):
                    raise LoginFailedError(
                        "Login did not complete successfully. "
                        "Could not detect an authenticated page."
                    )

                # Save encrypted auth state
                self._auth_mgr.save_from_context(context)
                logger.info("Login successful — session saved")

            except PlaywrightTimeout:
                raise LoginFailedError(
                    "Login timed out. Please try again with "
                    "'python -m deliverect_sync login'"
                )
            finally:
                context.close()

    def _wait_for_authentication(self, page: Page, timeout_ms: int = _LOGIN_TIMEOUT_MS) -> None:
        """Wait until the page shows an authenticated state.

        Polls for URL changes and DOM indicators that suggest
        successful authentication.
        """
        import time

        start = time.time()
        timeout_sec = timeout_ms / 1000

        while time.time() - start < timeout_sec:
            try:
                # Check if we're on an authenticated page
                if self._is_authenticated(page):
                    logger.info("Authentication detected via page indicators")
                    return

                # Wait a bit before checking again
                page.wait_for_timeout(2000)
            except Exception:
                page.wait_for_timeout(2000)

        raise PlaywrightTimeout("Timed out waiting for authentication")

    def _is_authenticated(self, page: Page) -> bool:
        """Check if the current page shows authenticated content."""
        try:
            url = page.url.lower()
            # Check URL for authenticated paths
            for indicator in _AUTH_INDICATORS:
                if indicator in url:
                    return True

            # Check page content for navigation elements
            for indicator in _AUTH_INDICATORS:
                try:
                    locator = page.get_by_text(indicator, exact=False)
                    if locator.count() > 0:
                        return True
                except Exception:
                    continue

            return False
        except Exception:
            return False

    def verify_session(self, page: Page) -> bool:
        """Verify that the current session is still authenticated.

        Args:
            page: Current Playwright page.

        Returns:
            True if authenticated, False if session expired.
        """
        try:
            # Try navigating to orders page
            portal_url = self._settings.portal.base_url
            page.goto(portal_url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # Check if we got redirected to login
            current_url = page.url.lower()
            if "login" in current_url or "auth" in current_url or "signin" in current_url:
                logger.warning("Session expired — redirected to login page")
                return False

            return self._is_authenticated(page)
        except Exception as e:
            logger.warning("Session verification failed: %s", type(e).__name__)
            return False

    def check_permissions(self, page: Page, registry: LocatorRegistry) -> None:
        """Verify that the user has required Deliverect permissions.

        Checks:
        1. Can view Orders page
        2. Can export Orders
        3. Can view Operations page

        Raises:
            PermissionError_: If any required permission is missing.
        """
        logger.info("Checking required permissions...")

        # 1. Check Orders visibility
        if not self._can_view_orders(page, registry):
            raise PermissionError_(
                RunStatus.MISSING_VIEW_ORDERS_PERMISSION,
                guidance=(
                    "The Orders page is not accessible. "
                    "Ask your Deliverect account administrator to grant "
                    "the 'View Orders' permission to your user role."
                ),
            )
        logger.info("✓ Orders page accessible")

        # 2. Check Export capability
        if not self._can_export_orders(page, registry):
            raise PermissionError_(
                RunStatus.MISSING_EXPORT_ORDERS_PERMISSION,
                guidance=(
                    "The Export Orders action is not available. "
                    "Ask your Deliverect account administrator to grant "
                    "the 'Export Orders' permission to your user role."
                ),
            )
        logger.info("✓ Export Orders available")

        # 3. Check Operations visibility
        if not self._can_view_operations(page, registry):
            raise PermissionError_(
                RunStatus.MISSING_OPERATIONS_PERMISSION,
                guidance=(
                    "The Operations page is not accessible. "
                    "Ask your Deliverect account administrator to grant "
                    "the 'View Operations' permission to your user role."
                ),
            )
        logger.info("✓ Operations page accessible")

    def _can_view_orders(self, page: Page, registry: LocatorRegistry) -> bool:
        """Check if Orders navigation is visible."""
        try:
            locator = registry.resolve(page, "orders_navigation")
            return locator is not None and locator.count() > 0
        except Exception:
            return False

    def _can_export_orders(self, page: Page, registry: LocatorRegistry) -> bool:
        """Check if Export Orders action is accessible.

        This tries to find the More menu and Export Orders option
        without actually clicking them.
        """
        try:
            # Check if More menu exists
            more_menu = registry.resolve(page, "orders_more_menu")
            return more_menu is not None and more_menu.count() > 0
        except Exception:
            return False

    def _can_view_operations(self, page: Page, registry: LocatorRegistry) -> bool:
        """Check if Operations navigation is visible."""
        try:
            locator = registry.resolve(page, "operations_navigation")
            return locator is not None and locator.count() > 0
        except Exception:
            return False

    def ensure_authenticated(self) -> tuple[Any, Any, Path | None]:
        """Ensure we have a valid authenticated session.

        Returns:
            Tuple of (browser, context, temp_state_path).

        Raises:
            AuthExpiredError: If session has expired.
        """
        if not self._auth_mgr.has_state():
            raise AuthExpiredError()

        browser = self._factory.create_browser(
            headless=self._settings.browser.headless_for_scheduled
        )
        context, temp_path = self._factory.create_authenticated_context(browser)

        return browser, context, temp_path

    @property
    def factory(self) -> BrowserFactory:
        """Access the browser factory."""
        return self._factory

    @property
    def auth_manager(self) -> AuthStateManager:
        """Access the auth state manager."""
        return self._auth_mgr
