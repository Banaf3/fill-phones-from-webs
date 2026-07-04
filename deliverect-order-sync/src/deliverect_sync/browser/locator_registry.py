"""Locator registry for resolving UI elements with fallback alternatives.

Loads locator definitions from selectors.yaml and tries alternatives
in priority order: get_by_role → get_by_label → get_by_placeholder →
test ID → text → CSS selector.
"""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import Locator, Page

from deliverect_sync.config import load_selectors
from deliverect_sync.exceptions import CalibrationRequiredError, UIChangedError
from deliverect_sync.logging_config import get_logger

logger = get_logger("locator_registry")


class LocatorAlternative:
    """A single locator strategy for an element."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.strategy: str = data["strategy"]
        self.role: str | None = data.get("role")
        self.name_regex: str | None = data.get("name_regex")
        self.label_regex: str | None = data.get("label_regex")
        self.placeholder_regex: str | None = data.get("placeholder_regex")
        self.text_regex: str | None = data.get("text_regex")
        self.text: str | None = data.get("text")
        self.test_id: str | None = data.get("test_id")
        self.selector: str | None = data.get("selector")

    def __repr__(self) -> str:
        if self.strategy == "role":
            return f"LocatorAlt(role={self.role}, name={self.name_regex})"
        elif self.strategy == "css":
            return f"LocatorAlt(css={self.selector})"
        elif self.strategy == "text":
            return f"LocatorAlt(text={self.text_regex or self.text})"
        return f"LocatorAlt(strategy={self.strategy})"


class LocatorDefinition:
    """A logical UI element with multiple locator alternatives."""

    def __init__(self, name: str, data: dict[str, Any]) -> None:
        self.name = name
        self.alternatives: list[LocatorAlternative] = []

        alts_data = data.get("alternatives", [])
        for alt_data in alts_data:
            self.alternatives.append(LocatorAlternative(alt_data))

    def __repr__(self) -> str:
        return f"LocatorDef({self.name}, {len(self.alternatives)} alternatives)"


class LocatorRegistry:
    """Registry of logical UI elements with fallback locator strategies.

    Loads definitions from selectors.yaml and resolves them against
    a live page by trying alternatives in order.
    """

    def __init__(self, selectors_path: str | None = None) -> None:
        """Initialize the registry from a selectors YAML file.

        Args:
            selectors_path: Path to selectors.yaml. If None, uses default discovery.
        """
        try:
            raw = load_selectors(selectors_path)
        except FileNotFoundError:
            raise CalibrationRequiredError()

        self._definitions: dict[str, LocatorDefinition] = {}
        self._ui_profile: dict[str, Any] = raw.get("ui_profile", {})

        # Parse all element definitions (skip ui_profile)
        for key, value in raw.items():
            if key == "ui_profile" or not isinstance(value, dict):
                continue
            if "alternatives" in value:
                self._definitions[key] = LocatorDefinition(key, value)

        logger.debug("Loaded %d locator definitions", len(self._definitions))

    @property
    def ui_profile(self) -> dict[str, Any]:
        """UI profile metadata from calibration."""
        return self._ui_profile

    @property
    def element_names(self) -> list[str]:
        """List of all registered element names."""
        return list(self._definitions.keys())

    def has_definition(self, element_name: str) -> bool:
        """Check if a definition exists for the element."""
        return element_name in self._definitions

    def resolve(
        self,
        page: Page,
        element_name: str,
        *,
        template_vars: dict[str, str] | None = None,
        timeout_ms: int = 5000,
    ) -> Locator:
        """Resolve a logical element name to a Playwright Locator.

        Tries each alternative in order until one matches visible elements.

        Args:
            page: Current Playwright page.
            element_name: Logical name from selectors.yaml.
            template_vars: Variable substitutions for templated locators.
            timeout_ms: Timeout for checking each alternative.

        Returns:
            The first matching Playwright Locator.

        Raises:
            UIChangedError: If no alternative matches.
            CalibrationRequiredError: If no definition exists.
        """
        definition = self._definitions.get(element_name)
        if not definition:
            raise UIChangedError(
                element_name,
                details=f"No locator definition for '{element_name}' in selectors.yaml",
            )

        errors: list[str] = []

        for i, alt in enumerate(definition.alternatives):
            try:
                locator = self._create_locator(page, alt, template_vars)
                # Check if the locator matches any elements
                count = locator.count()
                if count > 0:
                    logger.debug(
                        "Resolved '%s' with alternative %d/%d (%s) — %d matches",
                        element_name, i + 1, len(definition.alternatives), alt, count,
                    )
                    return locator
                else:
                    errors.append(f"Alt {i+1} ({alt}): 0 matches")
            except Exception as e:
                errors.append(f"Alt {i+1} ({alt}): {type(e).__name__}")
                continue

        # All alternatives failed
        error_detail = "; ".join(errors)
        logger.warning(
            "Failed to resolve '%s' — all %d alternatives exhausted: %s",
            element_name, len(definition.alternatives), error_detail,
        )
        raise UIChangedError(element_name, details=error_detail)

    def try_resolve(
        self,
        page: Page,
        element_name: str,
        *,
        template_vars: dict[str, str] | None = None,
    ) -> Locator | None:
        """Try to resolve a locator without raising on failure.

        Returns:
            Locator if found, None otherwise.
        """
        try:
            return self.resolve(page, element_name, template_vars=template_vars)
        except (UIChangedError, CalibrationRequiredError):
            return None

    def _create_locator(
        self,
        page: Page,
        alt: LocatorAlternative,
        template_vars: dict[str, str] | None = None,
    ) -> Locator:
        """Create a Playwright Locator from a locator alternative.

        Args:
            page: Current page.
            alt: The locator alternative definition.
            template_vars: Variable substitutions (e.g., {field_name}).

        Returns:
            Playwright Locator (may match 0 or more elements).
        """
        def _substitute(s: str | None) -> str | None:
            if s is None:
                return None
            if template_vars:
                for k, v in template_vars.items():
                    s = s.replace(f"{{{k}}}", v)
            return s

        strategy = alt.strategy

        if strategy == "role":
            role = alt.role
            if not role:
                raise ValueError("Role strategy requires 'role' field")
            name_pattern = _substitute(alt.name_regex)
            kwargs: dict[str, Any] = {}
            if name_pattern:
                kwargs["name"] = re.compile(name_pattern)
            return page.get_by_role(role, **kwargs)

        elif strategy == "label":
            label_pattern = _substitute(alt.label_regex)
            if label_pattern:
                return page.get_by_label(re.compile(label_pattern))
            raise ValueError("Label strategy requires 'label_regex'")

        elif strategy == "placeholder":
            ph_pattern = _substitute(alt.placeholder_regex)
            if ph_pattern:
                return page.get_by_placeholder(re.compile(ph_pattern))
            raise ValueError("Placeholder strategy requires 'placeholder_regex'")

        elif strategy == "test_id":
            test_id = _substitute(alt.test_id)
            if test_id:
                return page.get_by_test_id(test_id)
            raise ValueError("Test ID strategy requires 'test_id'")

        elif strategy == "text":
            text_pattern = _substitute(alt.text_regex)
            text_exact = _substitute(alt.text)
            if text_pattern:
                return page.get_by_text(re.compile(text_pattern))
            elif text_exact:
                return page.get_by_text(text_exact, exact=True)
            raise ValueError("Text strategy requires 'text_regex' or 'text'")

        elif strategy == "css":
            css_selector = _substitute(alt.selector)
            if css_selector:
                return page.locator(css_selector)
            raise ValueError("CSS strategy requires 'selector'")

        else:
            raise ValueError(f"Unknown locator strategy: {strategy}")

    def validate_all(self, page: Page) -> dict[str, bool]:
        """Validate all locator definitions against the current page.

        Returns:
            Dict mapping element names to whether they resolved successfully.
        """
        results: dict[str, bool] = {}
        for name in self._definitions:
            try:
                locator = self.resolve(page, name)
                results[name] = locator.count() > 0
            except (UIChangedError, CalibrationRequiredError):
                results[name] = False
        return results
