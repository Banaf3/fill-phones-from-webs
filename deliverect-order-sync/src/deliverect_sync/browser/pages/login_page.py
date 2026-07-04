"""Login page object — handles manual authentication flow.

Does NOT automate password entry. Only waits for the user to
complete authentication manually.
"""

from __future__ import annotations

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from deliverect_sync.browser.locator_registry import LocatorRegistry
from deliverect_sync.logging_config import get_logger

logger = get_logger("pages.login")

# URL patterns indicating a login page
_LOGIN_URL_PATTERNS = [
    "login",
    "auth",
    "signin",
    "sign-in",
    "accounts.google.com",
]

# URL patterns indicating successful authentication
_AUTHENTICATED_URL_PATTERNS = [
    "/orders",
    "/dashboard",
    "/operations",
    "/settings",
]


class LoginPage:
    """Page Object for the Deliverect login page.

    This page object does NOT automate credential entry.
    It only detects login state and waits for manual authentication.
    """

    def __init__(self, page: Page, registry: LocatorRegistry | None = None) -> None:
        self._page = page
        self._registry = registry

    @property
    def page(self) -> Page:
        return self._page

    def navigate(self, base_url: str) -> None:
        """Navigate to the Deliverect portal.

        Args:
            base_url: The portal URL (e.g., https://frontend.deliverect.com/).
        """
        logger.info("Navigating to portal: %s", base_url)
        self._page.goto(base_url, wait_until="domcontentloaded")
        self._page.wait_for_timeout(2000)

    def is_login_page(self) -> bool:
        """Check if we're currently on a login page."""
        url = self._page.url.lower()
        return any(pattern in url for pattern in _LOGIN_URL_PATTERNS)

    def is_authenticated(self) -> bool:
        """Check if the current page shows authenticated content."""
        url = self._page.url.lower()

        # Check URL for authenticated paths
        if any(pattern in url for pattern in _AUTHENTICATED_URL_PATTERNS):
            return True

        # Check for authenticated UI elements
        if self._registry:
            try:
                auth_indicator = self._registry.try_resolve(
                    self._page, "authenticated_indicator"
                )
                if auth_indicator and auth_indicator.count() > 0:
                    return True
            except Exception:
                pass

        # Check for common authenticated indicators
        try:
            nav = self._page.get_by_role("navigation")
            if nav.count() > 0:
                return True
        except Exception:
            pass

        return False

    def wait_for_login_complete(self, timeout_ms: int = 600_000) -> bool:
        """Wait for the user to complete manual login.

        Polls every 2 seconds to check if authentication is complete.

        Args:
            timeout_ms: Maximum wait time in milliseconds (default: 10 minutes).

        Returns:
            True if authentication was detected.

        Raises:
            PlaywrightTimeout: If timeout is reached.
        """
        import time

        logger.info("Waiting for manual login (timeout: %ds)...", timeout_ms // 1000)

        start = time.time()
        timeout_sec = timeout_ms / 1000

        while time.time() - start < timeout_sec:
            if self.is_authenticated():
                logger.info("Authentication detected")
                # Wait a moment for the page to fully load
                self._page.wait_for_timeout(2000)
                return True

            self._page.wait_for_timeout(2000)

        raise PlaywrightTimeout("Login timeout — user did not complete authentication")

    def detect_auth_method(self) -> str:
        """Detect which authentication method is being presented.

        Returns:
            One of: "email_password", "google", "sso", "unknown"
        """
        url = self._page.url.lower()

        if "accounts.google.com" in url:
            return "google"

        try:
            # Look for email/password form
            email_input = self._page.get_by_role("textbox", name="email")
            if email_input.count() > 0:
                return "email_password"
        except Exception:
            pass

        try:
            # Look for SSO indicators
            sso_button = self._page.get_by_text("SSO", exact=False)
            if sso_button.count() > 0:
                return "sso"
        except Exception:
            pass

        return "unknown"
