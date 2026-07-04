"""Database schema definitions and migrations.

Manages SQLite table creation and schema evolution.
"""

from __future__ import annotations

import sqlite3


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply all necessary database migrations to reach the target schema."""
    _create_tables_v1(conn)


def _create_tables_v1(conn: sqlite3.Connection) -> None:
    """Create the initial schema."""
    conn.executescript(
        """
        -- Distributed Locks for Sync Processes
        CREATE TABLE IF NOT EXISTS run_locks (
            lock_name TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            owner_pid INTEGER NOT NULL,
            owner_host TEXT NOT NULL,
            lease_version INTEGER NOT NULL,
            acquired_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL
        );

        -- Track overall sync executions
        CREATE TABLE IF NOT EXISTS sync_runs (
            id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            result TEXT,
            imported_rows INTEGER DEFAULT 0,
            new_orders INTEGER DEFAULT 0,
            updated_orders INTEGER DEFAULT 0,
            rejected_rows INTEGER DEFAULT 0
        );

        -- Track downloaded files
        CREATE TABLE IF NOT EXISTS source_files (
            id TEXT PRIMARY KEY,
            sync_run_id TEXT NOT NULL,
            origin TEXT NOT NULL,
            filename TEXT NOT NULL,
            original_path TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL,
            imported_at TEXT NOT NULL,
            FOREIGN KEY(sync_run_id) REFERENCES sync_runs(id)
        );

        -- Track explicit field mappings per file
        CREATE TABLE IF NOT EXISTS field_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file_id TEXT NOT NULL,
            source_header TEXT NOT NULL,
            canonical_field TEXT NOT NULL,
            mapping_method TEXT NOT NULL,
            confidence REAL NOT NULL,
            approved_by_user INTEGER NOT NULL,
            FOREIGN KEY(source_file_id) REFERENCES source_files(id)
        );

        -- Canonical Orders Table (Summarized State)
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            
            -- Identifiers
            business_key TEXT UNIQUE NOT NULL,
            deliverect_order_id TEXT,
            channel_order_id TEXT,
            receipt_id TEXT,
            external_order_id TEXT,
            
            -- Location & Channel
            location_key TEXT,
            location_key_source TEXT,
            channel TEXT,
            delivery_system TEXT,
            
            -- Categorization (Current State)
            current_status TEXT,
            current_status_event_id TEXT,
            current_status_time TEXT,
            current_status_time_quality TEXT,
            
            payment_status TEXT,
            payment_method TEXT,
            
            -- Timestamps
            pickup_time_raw TEXT,
            pickup_time_local TEXT,
            pickup_timezone TEXT,
            pickup_time_utc TEXT,
            source_timezone_explicit INTEGER DEFAULT 0,
            
            -- Financials (TEXT for exact decimal representation)
            subtotal TEXT,
            discount_total TEXT,
            delivery_fee TEXT,
            service_charge TEXT,
            tip TEXT,
            tax_total TEXT,
            order_total TEXT,
            currency TEXT,
            
            -- Encrypted PII (BLOBs)
            customer_name_encrypted BLOB,
            customer_phone_original_encrypted BLOB,
            customer_phone_e164_encrypted BLOB,
            customer_email_encrypted BLOB,
            delivery_address_encrypted BLOB,
            
            -- Metadata
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            source_file_id TEXT NOT NULL,
            
            FOREIGN KEY(source_file_id) REFERENCES source_files(id)
        );

        -- Order Events (Append-only History)
        CREATE TABLE IF NOT EXISTS order_events (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            event_kind TEXT NOT NULL,
            normalized_status TEXT,
            raw_status TEXT,
            
            event_time TEXT,
            event_time_source TEXT NOT NULL,
            event_time_inferred INTEGER NOT NULL,
            observed_at TEXT NOT NULL,
            
            source_file_id TEXT NOT NULL,
            source_row_number INTEGER NOT NULL,
            source_observation_key TEXT UNIQUE NOT NULL,
            canonical_event_hash TEXT,
            
            -- Cross-file deduplication for canonical events
            UNIQUE(order_id, canonical_event_hash),
            
            FOREIGN KEY(order_id) REFERENCES orders(id),
            FOREIGN KEY(source_file_id) REFERENCES source_files(id)
        );

        -- Order Item Snapshots
        CREATE TABLE IF NOT EXISTS order_item_snapshots (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            source_file_id TEXT NOT NULL,
            snapshot_time TEXT NOT NULL,
            is_complete INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            snapshot_hash TEXT NOT NULL,
            
            FOREIGN KEY(order_id) REFERENCES orders(id),
            FOREIGN KEY(source_file_id) REFERENCES source_files(id)
        );

        -- Canonical Order Items Table
        CREATE TABLE IF NOT EXISTS order_items (
            id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            order_id TEXT NOT NULL,
            item_fingerprint TEXT NOT NULL,
            occurrence_index INTEGER NOT NULL,
            
            source_row_number INTEGER NOT NULL,
            plu TEXT,
            item_name TEXT,
            quantity INTEGER,
            unit_price TEXT,
            line_total TEXT,
            
            UNIQUE(order_id, item_fingerprint, occurrence_index),
            FOREIGN KEY(snapshot_id) REFERENCES order_item_snapshots(id),
            FOREIGN KEY(order_id) REFERENCES orders(id)
        );

        -- Raw Export Rows for auditing
        CREATE TABLE IF NOT EXISTS raw_export_rows (
            id TEXT PRIMARY KEY,
            source_file_id TEXT NOT NULL,
            source_row_number INTEGER NOT NULL,
            row_hash TEXT NOT NULL,
            redacted_json TEXT NOT NULL,
            encrypted_original_json BLOB,
            imported_at TEXT NOT NULL,
            validation_status TEXT,
            
            UNIQUE(source_file_id, source_row_number),
            FOREIGN KEY(source_file_id) REFERENCES source_files(id)
        );

        -- Track validation and import errors
        CREATE TABLE IF NOT EXISTS import_errors (
            id TEXT PRIMARY KEY,
            sync_run_id TEXT NOT NULL,
            source_file_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            stage TEXT NOT NULL,
            order_id TEXT,
            row_number INTEGER,
            error_code TEXT NOT NULL,
            error_message TEXT NOT NULL,
            FOREIGN KEY(sync_run_id) REFERENCES sync_runs(id),
            FOREIGN KEY(source_file_id) REFERENCES source_files(id)
        );
        
        -- Indexes for common lookups
        CREATE INDEX IF NOT EXISTS idx_orders_business_key ON orders(business_key);
        CREATE INDEX IF NOT EXISTS idx_orders_deliverect_id ON orders(deliverect_order_id);
        CREATE INDEX IF NOT EXISTS idx_order_events_order_id ON order_events(order_id);
        CREATE INDEX IF NOT EXISTS idx_raw_rows_hash ON raw_export_rows(source_file_id, row_hash);
        """
    )
