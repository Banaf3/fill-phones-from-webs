"""Logging configuration with PII redaction.

Two handlers:
  - Console: INFO level, PII-redacted
  - File: DEBUG level, PII-redacted

PII patterns (phone numbers, emails, names in known contexts)
are replaced with [REDACTED] before being written.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


# PII patterns to redact from log messages
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Email addresses
    (re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"), "[EMAIL_REDACTED]"),
    # Phone numbers: international format (+966..., +1..., etc.)
    (re.compile(r"\+?\d{1,4}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{2,4}[\s\-]?\d{2,4}[\s\-]?\d{0,4}"),
     "[PHONE_REDACTED]"),
    # Arabic-Indic phone digits
    (re.compile(r"[٠-٩۰-۹]{7,15}"), "[PHONE_REDACTED]"),
    # Saudi mobile patterns: 05xxxxxxxx
    (re.compile(r"\b0[5]\d{8}\b"), "[PHONE_REDACTED]"),
    # Cookie values (key=value in cookie headers)
    (re.compile(r"(cookie[s]?\s*[:=]\s*)([^\s;]+)", re.IGNORECASE), r"\1[REDACTED]"),
    # Authorization headers
    (re.compile(r"(authorization\s*[:=]\s*)(\S+)", re.IGNORECASE), r"\1[REDACTED]"),
    # Token values
    (re.compile(r"(token\s*[:=]\s*)([^\s,;]+)", re.IGNORECASE), r"\1[REDACTED]"),
    # Password references (should never appear, but safety net)
    (re.compile(r"(password\s*[:=]\s*)(\S+)", re.IGNORECASE), r"\1[REDACTED]"),
]


class PIIRedactionFilter(logging.Filter):
    """Logging filter that redacts PII from log messages and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact PII from the log record message."""
        # Redact the formatted message
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)

        # Redact string arguments
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._redact(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._redact(a) if isinstance(a, str) else a for a in record.args
                )

        return True

    @staticmethod
    def _redact(text: str) -> str:
        """Apply all PII redaction patterns to a text string."""
        for pattern, replacement in _PII_PATTERNS:
            text = pattern.sub(replacement, text)
        return text


def redact_pii(text: str) -> str:
    """Redact PII from a string. Public utility function."""
    return PIIRedactionFilter._redact(text)


def setup_logging(
    log_level: str = "INFO",
    logs_dir: Path | None = None,
    run_id: str | None = None,
) -> logging.Logger:
    """Configure application-wide logging with PII redaction.

    Args:
        log_level: Console log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        logs_dir: Directory for log files. If None, file logging is disabled.
        run_id: Optional run ID to include in log format.

    Returns:
        The root application logger.
    """
    logger = logging.getLogger("deliverect_sync")
    logger.setLevel(logging.DEBUG)

    # Remove existing handlers to allow reconfiguration
    logger.handlers.clear()

    # PII filter applied to all handlers
    pii_filter = PIIRedactionFilter()

    # Console handler
    console_fmt = "%(asctime)s │ %(levelname)-8s │ %(message)s"
    if run_id:
        console_fmt = f"%(asctime)s │ {run_id[:8]} │ %(levelname)-8s │ %(message)s"

    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    console_handler.setFormatter(logging.Formatter(console_fmt, datefmt="%H:%M:%S"))
    console_handler.addFilter(pii_filter)
    logger.addHandler(console_handler)

    # File handler
    if logs_dir:
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_filename = f"sync_{run_id}.log" if run_id else "deliverect_sync.log"
        log_path = logs_dir / log_filename

        file_fmt = (
            "%(asctime)s │ %(name)s │ %(levelname)-8s │ "
            "%(funcName)s:%(lineno)d │ %(message)s"
        )
        if run_id:
            file_fmt = (
                f"%(asctime)s │ {run_id} │ %(name)s │ %(levelname)-8s │ "
                "%(funcName)s:%(lineno)d │ %(message)s"
            )

        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(file_fmt))
        file_handler.addFilter(pii_filter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the application namespace."""
    return logging.getLogger(f"deliverect_sync.{name}")
