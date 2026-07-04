"""Error report generation.

Generates a CSV or Excel report of validation/import errors for user review.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from deliverect_sync.config import AppSettings
from deliverect_sync.logging_config import get_logger
from deliverect_sync.storage.database import DatabaseManager

logger = get_logger("error_report")


def generate_error_report(settings: AppSettings, db: DatabaseManager, run_id: str | None = None) -> Path | None:
    """Generate an error report CSV for the given run or all runs."""
    errors = db.get_import_errors(run_id)
    if not errors:
        return None
        
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = f"_{run_id[:8]}" if run_id else ""
    filename = f"deliverect_sync_errors{suffix}_{timestamp}.csv"
    output_path = settings.output_dir / filename
    
    fieldnames = ["timestamp", "stage", "order_id", "row_number", "error_code", "error_message"]
    
    try:
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for err in errors:
                writer.writerow({
                    "timestamp": err.get("timestamp", ""),
                    "stage": err.get("stage", ""),
                    "order_id": err.get("order_id", ""),
                    "row_number": err.get("row_number", ""),
                    "error_code": err.get("error_code", ""),
                    "error_message": err.get("error_message", ""),
                })
        logger.info("Error report generated: %s", output_path)
        return output_path
    except Exception as e:
        logger.error("Failed to generate error report: %s", type(e).__name__)
        return None
