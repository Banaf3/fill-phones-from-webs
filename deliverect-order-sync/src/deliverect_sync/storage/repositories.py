"""Repository layer — higher-level database operations.

Thin wrapper kept for forward compatibility; primary operations
are already in DatabaseManager.
"""

from __future__ import annotations

from deliverect_sync.storage.database import DatabaseManager


class OrderRepository:
    """Higher-level order operations."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def count_orders(self) -> int:
        """Count total orders in the database."""
        conn = self._db._get_conn()
        row = conn.execute("SELECT COUNT(*) as cnt FROM orders").fetchone()
        return row["cnt"] if row else 0

    def count_by_status(self) -> dict[str, int]:
        """Count orders grouped by status."""
        conn = self._db._get_conn()
        rows = conn.execute(
            "SELECT order_status, COUNT(*) as cnt FROM orders GROUP BY order_status"
        ).fetchall()
        return {r["order_status"]: r["cnt"] for r in rows if r["order_status"]}

    def count_by_channel(self) -> dict[str, int]:
        """Count orders grouped by channel."""
        conn = self._db._get_conn()
        rows = conn.execute(
            "SELECT channel, COUNT(*) as cnt FROM orders GROUP BY channel"
        ).fetchall()
        return {r["channel"]: r["cnt"] for r in rows if r["channel"]}

    def count_by_location(self) -> dict[str, int]:
        """Count orders grouped by location."""
        conn = self._db._get_conn()
        rows = conn.execute(
            "SELECT location, COUNT(*) as cnt FROM orders GROUP BY location"
        ).fetchall()
        return {r["location"]: r["cnt"] for r in rows if r["location"]}
