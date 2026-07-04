"""SQLite database manager.

Manages all database operations with WAL mode, robust locking,
event appending, item snapshot deduplication, and precise Decimal handling.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from deliverect_sync.logging_config import get_logger
from deliverect_sync.models import (
    EventTimeQuality,
    FieldMapping,
    ImportErrorRecord,
    Order,
    OrderEvent,
    OrderItem,
    OrderItemSnapshot,
    RawExportRow,
    RunLock,
    SourceFile,
    SourceFileOrigin,
    SyncResult,
    SyncRun,
)
from deliverect_sync.storage.migrations import apply_migrations

logger = get_logger("database")


def _adapt_decimal(d: Decimal) -> str:
    """SQLite adapter: Decimal → TEXT."""
    return str(d)


def _convert_decimal(s: bytes) -> Decimal:
    """SQLite converter: TEXT → Decimal."""
    return Decimal(s.decode("utf-8"))


# Register decimal adapter globally
sqlite3.register_adapter(Decimal, _adapt_decimal)
sqlite3.register_converter("DECIMAL", _convert_decimal)


class DatabaseLockError(Exception):
    """Raised when a database lock cannot be acquired or is stale."""
    pass


class DatabaseManager:
    """SQLite database manager for Deliverect Order Sync."""

    def __init__(self, db_path: Path, busy_timeout_ms: int = 5000) -> None:
        self._db_path = db_path
        self._busy_timeout_ms = busy_timeout_ms
        self._conn: sqlite3.Connection | None = None

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a database connection."""
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self._db_path),
                detect_types=sqlite3.PARSE_DECLTYPES,
                timeout=self._busy_timeout_ms / 1000.0,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def initialize(self) -> None:
        """Initialize the database schema."""
        conn = self._get_conn()
        apply_migrations(conn)
        logger.debug("Database initialized: %s", self._db_path)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # --- Run management & Locking ---

    def acquire_lock(self, lock_name: str, run_id: str, force: bool = False) -> RunLock:
        """Acquire a distributed run lock."""
        conn = self._get_conn()
        pid = os.getpid()
        import socket
        host = socket.gethostname()
        now = datetime.now(tz=timezone.utc)
        
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute("SELECT * FROM run_locks WHERE lock_name = ?", (lock_name,)).fetchone()
            if existing:
                # Check for stale lock
                hb = datetime.fromisoformat(existing["heartbeat_at"])
                stale_threshold = 60 # seconds
                is_stale = (now - hb).total_seconds() > stale_threshold
                
                # Check if process is dead on same host
                process_dead = False
                if existing["owner_host"] == host:
                    try:
                        os.kill(existing["owner_pid"], 0)
                    except OSError:
                        process_dead = True
                        
                can_takeover = force or (is_stale and process_dead)
                if not can_takeover:
                    raise DatabaseLockError(
                        f"Lock {lock_name} held by {existing['owner_host']}:{existing['owner_pid']} "
                        f"(heartbeat age: {(now - hb).total_seconds():.1f}s). Force takeover not permitted."
                    )
                
                new_version = existing["lease_version"] + 1
                conn.execute(
                    """UPDATE run_locks SET run_id = ?, owner_pid = ?, owner_host = ?, lease_version = ?, acquired_at = ?, heartbeat_at = ?
                       WHERE lock_name = ?""",
                    (run_id, pid, host, new_version, now.isoformat(), now.isoformat(), lock_name)
                )
            else:
                new_version = 1
                conn.execute(
                    """INSERT INTO run_locks (lock_name, run_id, owner_pid, owner_host, lease_version, acquired_at, heartbeat_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (lock_name, run_id, pid, host, new_version, now.isoformat(), now.isoformat())
                )
            conn.commit()
            return RunLock(
                lock_name=lock_name, run_id=run_id, owner_pid=pid, owner_host=host, 
                lease_version=new_version, acquired_at=now, heartbeat_at=now
            )
        except Exception:
            conn.rollback()
            raise

    def heartbeat_lock(self, lock: RunLock) -> None:
        """Update the heartbeat for the held lock."""
        conn = self._get_conn()
        now = datetime.now(tz=timezone.utc)
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute("SELECT lease_version FROM run_locks WHERE lock_name = ?", (lock.lock_name,)).fetchone()
            if not existing or existing["lease_version"] != lock.lease_version:
                raise DatabaseLockError("Lease version mismatch or lock deleted. Cannot write heartbeat.")
            conn.execute(
                "UPDATE run_locks SET heartbeat_at = ? WHERE lock_name = ? AND lease_version = ?",
                (now.isoformat(), lock.lock_name, lock.lease_version)
            )
            conn.commit()
            lock.heartbeat_at = now
        except Exception:
            conn.rollback()
            raise

    def release_lock(self, lock: RunLock) -> None:
        """Release the lock."""
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM run_locks WHERE lock_name = ? AND lease_version = ?",
            (lock.lock_name, lock.lease_version)
        )
        conn.commit()

    def has_active_locks(self) -> bool:
        """Check if any run locks exist."""
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) as cnt FROM run_locks").fetchone()
        return row["cnt"] > 0

    def is_run_active(self) -> bool:
        """Backwards compatibility alias for has_active_locks."""
        return self.has_active_locks()

    def create_run(self, run: SyncRun) -> None:
        """Create a new sync run record."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO sync_runs
               (id, started_at, result)
               VALUES (?, ?, ?)""",
            (
                run.id,
                run.started_at.isoformat(),
                run.result.value,
            ),
        )
        conn.commit()

    def update_run(self, run: SyncRun) -> None:
        """Update an existing sync run record."""
        conn = self._get_conn()
        conn.execute(
            """UPDATE sync_runs SET
               finished_at = ?, imported_rows = ?, new_orders = ?, 
               updated_orders = ?, rejected_rows = ?, result = ?
               WHERE id = ?""",
            (
                run.finished_at.isoformat() if run.finished_at else None,
                run.imported_rows,
                run.new_orders,
                run.updated_orders,
                run.rejected_rows,
                run.result.value,
                run.id,
            ),
        )
        conn.commit()

    def get_last_run(self) -> SyncRun | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        d = dict(row)
        return SyncRun(
            id=d["id"],
            started_at=datetime.fromisoformat(d["started_at"]),
            finished_at=datetime.fromisoformat(d["finished_at"]) if d.get("finished_at") else None,
            imported_rows=d.get("imported_rows", 0),
            new_orders=d.get("new_orders", 0),
            updated_orders=d.get("updated_orders", 0),
            rejected_rows=d.get("rejected_rows", 0),
            result=SyncResult(d.get("result", "IN_PROGRESS")),
        )

    # --- Orders & Events ---

    def _event_quality_rank(self, quality: str) -> int:
        ranks = {"AUTHORITATIVE": 4, "INFERRED": 3, "OBSERVED_ONLY": 2, "UNKNOWN": 1}
        return ranks.get(quality, 0)

    def append_order_event(self, event: OrderEvent, lock: RunLock) -> bool:
        """Append an event and update order current_status if it wins precedence."""
        self.heartbeat_lock(lock)
        conn = self._get_conn()
        
        # Check idempotency
        if event.canonical_event_hash:
            existing_evt = conn.execute(
                "SELECT id FROM order_events WHERE order_id = ? AND canonical_event_hash = ?",
                (event.order_id, event.canonical_event_hash)
            ).fetchone()
            if existing_evt:
                return False
                
        existing_src = conn.execute(
            "SELECT id FROM order_events WHERE source_observation_key = ?",
            (event.source_observation_key,)
        ).fetchone()
        if existing_src:
            return False

        conn.execute(
            """INSERT INTO order_events
               (id, order_id, event_kind, normalized_status, raw_status, event_time, 
                event_time_source, event_time_inferred, observed_at, source_file_id, 
                source_row_number, source_observation_key, canonical_event_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id, event.order_id, event.event_kind.value, event.normalized_status,
                event.raw_status, event.event_time.isoformat() if event.event_time else None,
                event.event_time_source, 1 if event.event_time_inferred else 0,
                event.observed_at.isoformat(), event.source_file_id, event.source_row_number,
                event.source_observation_key, event.canonical_event_hash
            )
        )
        conn.commit()
        return True

    def upsert_order(self, order: Order, lock: RunLock) -> bool:
        """Insert a new order or update metadata (not status). Use append_order_event for status."""
        self.heartbeat_lock(lock)
        conn = self._get_conn()

        existing = conn.execute(
            "SELECT id FROM orders WHERE business_key = ?",
            (order.business_key,),
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE orders SET
                   last_seen_at = ?,
                   payment_status = COALESCE(?, payment_status),
                   payment_method = COALESCE(?, payment_method),
                   order_total = COALESCE(?, order_total)
                   WHERE id = ?""",
                (
                    datetime.now(tz=timezone.utc).isoformat(),
                    order.payment_status,
                    order.payment_method,
                    str(order.order_total) if order.order_total else None,
                    existing["id"],
                ),
            )
            conn.commit()
            order.id = existing["id"]
            return False
        else:
            conn.execute(
                """INSERT INTO orders
                   (id, business_key, deliverect_order_id, channel_order_id, receipt_id, external_order_id,
                    location_key, location_key_source, channel, delivery_system,
                    payment_status, payment_method,
                    pickup_time_raw, pickup_time_local, pickup_timezone, pickup_time_utc, source_timezone_explicit,
                    subtotal, discount_total, delivery_fee, service_charge,
                    tip, tax_total, order_total, currency,
                    customer_name_encrypted, customer_phone_original_encrypted,
                    customer_phone_e164_encrypted, customer_email_encrypted, delivery_address_encrypted,
                    first_seen_at, last_seen_at, source_file_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order.id, order.business_key, order.deliverect_order_id, order.channel_order_id, order.receipt_id, order.external_order_id,
                    order.location_key, order.location_key_source.value if order.location_key_source else None, order.channel, order.delivery_system,
                    order.payment_status, order.payment_method,
                    order.pickup_time_raw, order.pickup_time_local, order.pickup_timezone, order.pickup_time_utc, 1 if order.source_timezone_explicit else 0,
                    str(order.subtotal) if order.subtotal is not None else None,
                    str(order.discount_total) if order.discount_total is not None else None,
                    str(order.delivery_fee) if order.delivery_fee is not None else None,
                    str(order.service_charge) if order.service_charge is not None else None,
                    str(order.tip) if order.tip is not None else None,
                    str(order.tax_total) if order.tax_total is not None else None,
                    str(order.order_total) if order.order_total is not None else None,
                    order.currency,
                    order.customer_name_encrypted, order.customer_phone_original_encrypted,
                    order.customer_phone_e164_encrypted, order.customer_email_encrypted, order.delivery_address_encrypted,
                    order.first_seen_at.isoformat() if order.first_seen_at else None,
                    order.last_seen_at.isoformat() if order.last_seen_at else None,
                    order.source_file_id,
                ),
            )
            conn.commit()
            return True

    def update_order_current_status(self, order_id: str, new_event: OrderEvent, quality: EventTimeQuality, lock: RunLock) -> None:
        """Update current status after checking precedence against existing."""
        self.heartbeat_lock(lock)
        conn = self._get_conn()
        
        curr = conn.execute(
            "SELECT current_status_time, current_status_time_quality FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()
        
        update = False
        if not curr or not curr["current_status_time"]:
            update = True
        else:
            curr_q = self._event_quality_rank(curr["current_status_time_quality"])
            new_q = self._event_quality_rank(quality.value)
            
            if new_q > curr_q:
                update = True
            elif new_q == curr_q and new_event.event_time:
                curr_time = datetime.fromisoformat(curr["current_status_time"])
                if new_event.event_time > curr_time:
                    update = True
                    
        if update:
            conn.execute(
                """UPDATE orders SET
                   current_status = ?, current_status_event_id = ?, 
                   current_status_time = ?, current_status_time_quality = ?
                   WHERE id = ?""",
                (
                    new_event.normalized_status or new_event.raw_status,
                    new_event.id,
                    new_event.event_time.isoformat() if new_event.event_time else None,
                    quality.value,
                    order_id
                )
            )
            conn.commit()

    def get_all_orders(self) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM orders ORDER BY pickup_time_utc DESC").fetchall()
        return [dict(r) for r in rows]

    # --- Item Snapshots & Items ---

    def create_item_snapshot(self, snapshot: OrderItemSnapshot, lock: RunLock) -> bool:
        """Create a new item snapshot if it doesn't already exist."""
        self.heartbeat_lock(lock)
        conn = self._get_conn()
        
        existing = conn.execute(
            "SELECT id FROM order_item_snapshots WHERE snapshot_hash = ?",
            (snapshot.snapshot_hash,)
        ).fetchone()
        if existing:
            return False
            
        conn.execute(
            """INSERT INTO order_item_snapshots
               (id, order_id, source_file_id, snapshot_time, is_complete, created_at, snapshot_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot.id, snapshot.order_id, snapshot.source_file_id, 
                snapshot.snapshot_time.isoformat(), 1 if snapshot.is_complete else 0,
                snapshot.created_at.isoformat(), snapshot.snapshot_hash
            )
        )
        conn.commit()
        return True

    def insert_order_items(self, items: list[OrderItem], lock: RunLock) -> None:
        """Insert a batch of order items."""
        self.heartbeat_lock(lock)
        conn = self._get_conn()
        for item in items:
            conn.execute(
                """INSERT OR IGNORE INTO order_items
                   (id, snapshot_id, order_id, item_fingerprint, occurrence_index,
                    source_row_number, plu, item_name, quantity, unit_price, line_total)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.id, item.snapshot_id, item.order_id, item.item_fingerprint, item.occurrence_index,
                    item.source_row_number, item.plu, item.item_name, item.quantity,
                    str(item.unit_price) if item.unit_price is not None else None,
                    str(item.line_total) if item.line_total is not None else None,
                ),
            )
        conn.commit()

    def get_all_order_items(self) -> list[dict[str, Any]]:
        # Usually we only want items from the LATEST complete snapshot per order.
        # For Excel export, we should join against the latest complete snapshot.
        conn = self._get_conn()
        query = """
            WITH LatestSnapshots AS (
                SELECT order_id, MAX(snapshot_time) as latest_time
                FROM order_item_snapshots
                WHERE is_complete = 1
                GROUP BY order_id
            ),
            ActiveSnapshots AS (
                SELECT s.id, s.order_id
                FROM order_item_snapshots s
                JOIN LatestSnapshots ls ON s.order_id = ls.order_id AND s.snapshot_time = ls.latest_time
            )
            SELECT i.* FROM order_items i
            JOIN ActiveSnapshots act ON i.snapshot_id = act.id
            ORDER BY i.order_id, i.occurrence_index
        """
        rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]

    # --- Source Files ---

    def create_source_file(self, source_file: SourceFile) -> None:
        """Record a source file."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO source_files
               (id, sync_run_id, origin, filename, original_path, file_hash, file_size_bytes, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source_file.id, source_file.sync_run_id, source_file.origin.value, 
                source_file.filename, source_file.original_path, source_file.file_hash, 
                source_file.file_size_bytes, source_file.imported_at.isoformat()
            ),
        )
        conn.commit()

    def get_source_file_by_hash(self, file_hash: str) -> SourceFile | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM source_files WHERE file_hash = ?", (file_hash,)).fetchone()
        if not row:
            return None
        return SourceFile(
            id=row["id"], sync_run_id=row["sync_run_id"], origin=SourceFileOrigin(row["origin"]),
            filename=row["filename"], original_path=row["original_path"], file_hash=row["file_hash"],
            file_size_bytes=row["file_size_bytes"], imported_at=datetime.fromisoformat(row["imported_at"])
        )

    # --- Raw Rows ---

    def raw_row_exists(self, source_file_id: str, row_number: int) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id FROM raw_export_rows WHERE source_file_id = ? AND source_row_number = ?", 
            (source_file_id, row_number)
        ).fetchone()
        return row is not None

    def create_raw_row(self, row: RawExportRow) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO raw_export_rows
               (id, source_file_id, source_row_number, row_hash, redacted_json, encrypted_original_json, imported_at, validation_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (row.id, row.source_file_id, row.source_row_number, row.row_hash, row.redacted_json, row.encrypted_original_json, row.imported_at.isoformat(), row.validation_status),
        )
        conn.commit()

    # --- Import Errors & Mappings ---
    def create_import_error(self, error: ImportErrorRecord) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO import_errors
               (id, sync_run_id, source_file_id, timestamp, stage, order_id, row_number, error_code, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                error.id, error.sync_run_id, error.source_file_id, error.timestamp.isoformat(),
                error.stage, None, error.row_number, error.error_code, error.error_message
            ),
        )
        conn.commit()

    def store_field_mappings(self, source_file_id: str, mappings: list[FieldMapping]) -> None:
        conn = self._get_conn()
        for m in mappings:
            conn.execute(
                """INSERT INTO field_mappings
                   (source_file_id, source_header, canonical_field,
                    mapping_method, confidence, approved_by_user)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    source_file_id, m.source_header, m.canonical_field or "",
                    m.mapping_method, m.confidence, m.approved_by_user,
                ),
            )
        conn.commit()

    # --- Backup / Maintenance ---
    def reset_database(self, backup_dir: Path) -> Path | None:
        """Safely checkpoint and reset the database, producing a backup."""
        if self.has_active_locks():
            raise DatabaseLockError("Cannot reset database while a sync lock is active.")
            
        conn = self._get_conn()
        # Truncate WAL to ensure everything is in main DB file
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"deliverect_sync_backup_{timestamp}.sqlite"
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        import shutil
        self.close()
        try:
            shutil.copy2(self.db_path, backup_path)
            # Remove main, wal, shm
            self.db_path.unlink(missing_ok=True)
            wal = self.db_path.with_name(self.db_path.name + "-wal")
            shm = self.db_path.with_name(self.db_path.name + "-shm")
            wal.unlink(missing_ok=True)
            shm.unlink(missing_ok=True)
        except Exception as e:
            logger.error("Failed to backup and reset: %s", e)
            return None
            
        # Re-initialize
        self.initialize()
        return backup_path
