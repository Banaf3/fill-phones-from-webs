"""Core domain models for the Deliverect Order Sync system.

Defines the canonical data structures used throughout the pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class SyncResult(Enum):
    """Result of a sync run."""
    IN_PROGRESS = "IN_PROGRESS"
    SUCCESS = "SUCCESS"
    SUCCESS_WITH_WARNINGS = "SUCCESS_WITH_WARNINGS"
    FAILED = "FAILED"


class RunStatus(Enum):
    """Terminal status values for a sync run."""
    IN_PROGRESS = "IN_PROGRESS"
    SUCCESS = "SUCCESS"
    SUCCESS_WITH_WARNINGS = "SUCCESS_WITH_WARNINGS"
    FAILED = "FAILED"
    NO_ORDERS = "NO_ORDERS"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    LOGIN_FAILED = "LOGIN_FAILED"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    CALIBRATION_REQUIRED = "CALIBRATION_REQUIRED"
    UI_CHANGED = "UI_CHANGED"
    EXPORT_REQUEST_FAILED = "EXPORT_REQUEST_FAILED"
    EXPORT_OPERATION_NOT_FOUND = "EXPORT_OPERATION_NOT_FOUND"
    AMBIGUOUS_EXPORT_OPERATION = "AMBIGUOUS_EXPORT_OPERATION"
    EXPORT_TIMEOUT = "EXPORT_TIMEOUT"
    EXPORT_FAILED = "EXPORT_FAILED"
    DOWNLOAD_LINK_FAILED = "DOWNLOAD_LINK_FAILED"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    INVALID_DOWNLOAD = "INVALID_DOWNLOAD"
    CSV_SCHEMA_UNKNOWN = "CSV_SCHEMA_UNKNOWN"
    CSV_PARSE_FAILED = "CSV_PARSE_FAILED"
    DATABASE_FAILED = "DATABASE_FAILED"
    EXCEL_EXPORT_FAILED = "EXCEL_EXPORT_FAILED"
    CANCELED_BY_USER = "CANCELED_BY_USER"
    MISSING_VIEW_ORDERS_PERMISSION = "MISSING_VIEW_ORDERS_PERMISSION"
    MISSING_EXPORT_ORDERS_PERMISSION = "MISSING_EXPORT_ORDERS_PERMISSION"
    MISSING_OPERATIONS_PERMISSION = "MISSING_OPERATIONS_PERMISSION"


class RecordKind(Enum):
    """Business categorization of an order record."""
    NORMAL = "NORMAL"
    CANCELLATION = "CANCELLATION"
    REMAKE = "REMAKE"
    STATUS_UPDATE = "STATUS_UPDATE"
    UNKNOWN = "UNKNOWN"


class MappingStatus(Enum):
    """Status of a CSV header mapping."""
    EXACT = "EXACT"
    ALIAS = "ALIAS"
    FUZZY = "FUZZY"
    AUTO_APPROVED = "AUTO_APPROVED"
    USER_APPROVED = "USER_APPROVED"
    REQUIRED_REVIEW = "REQUIRED_REVIEW"
    UNMAPPED = "UNMAPPED"
    UNRESOLVED = "UNRESOLVED"


class SourceFileOrigin(Enum):
    """Origin of a source file."""
    AUTOMATED_DOWNLOAD = "AUTOMATED_DOWNLOAD"
    USER_SUPPLIED = "USER_SUPPLIED"


class EventTimeQuality(Enum):
    """Trustworthiness of an event time."""
    AUTHORITATIVE = "AUTHORITATIVE"
    INFERRED = "INFERRED"
    OBSERVED_ONLY = "OBSERVED_ONLY"
    UNKNOWN = "UNKNOWN"


class LocationKeySource(Enum):
    """Source of a location identity."""
    LOCATION_ID = "LOCATION_ID"
    APPROVED_MAPPING = "APPROVED_MAPPING"
    NORMALIZED_NAME = "NORMALIZED_NAME"


@dataclass
class SyncRun:
    """Tracks a complete synchronization execution."""
    id: str
    started_at: datetime
    finished_at: datetime | None = None
    result: SyncResult = SyncResult.IN_PROGRESS
    imported_rows: int = 0
    new_orders: int = 0
    updated_orders: int = 0
    rejected_rows: int = 0
    
    # In-memory runtime tracking fields (not persisted to sync_runs table)
    portal: str = ""
    locations: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    downloaded_filename: str | None = None
    file_hash: str | None = None
    error_message: str | None = None
    export_operation_id: str | None = None


@dataclass
class SourceFile:
    """Represents a downloaded or supplied CSV file."""
    id: str
    sync_run_id: str
    origin: SourceFileOrigin
    filename: str
    original_path: str
    file_hash: str
    file_size_bytes: int
    encoding: str = "utf-8"
    delimiter: str = ","
    header_count: int = 0
    row_count: int = 0
    imported_at: datetime = field(default_factory=datetime.utcnow)
    schema_version: str = "v1"


@dataclass
class ImportErrorRecord:
    """Record of an error during import."""
    id: str
    sync_run_id: str
    source_file_id: str
    row_number: int
    stage: str
    error_code: str
    error_message: str
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class RawExportRow:
    """Represents a row exactly as exported, with PII redacted."""
    id: str
    source_file_id: str
    source_row_number: int
    row_hash: str
    redacted_json: str
    encrypted_original_json: bytes | None
    imported_at: datetime
    validation_status: str | None = None


@dataclass
class OrderItemSnapshot:
    """An authoritative or observational snapshot of an order's items."""
    id: str
    order_id: str
    source_file_id: str
    snapshot_time: datetime
    is_complete: bool
    created_at: datetime
    snapshot_hash: str


@dataclass
class OrderItem:
    """A line item within an order snapshot."""
    id: str
    snapshot_id: str
    order_id: str
    item_fingerprint: str
    occurrence_index: int
    
    # Original data
    source_row_number: int
    plu: str | None = None
    item_name: str | None = None
    quantity: int | None = None
    unit_price: str | None = None
    line_total: str | None = None


@dataclass
class OrderEvent:
    """An event mutating an order's status or metadata."""
    id: str
    order_id: str
    event_kind: RecordKind
    normalized_status: str | None
    raw_status: str | None
    
    event_time: datetime | None
    event_time_source: str
    event_time_inferred: bool
    observed_at: datetime
    
    source_file_id: str
    source_row_number: int
    source_observation_key: str
    canonical_event_hash: str | None


@dataclass
class Order:
    """The canonical order representation."""
    id: str
    
    # Identifiers
    business_key: str
    deliverect_order_id: str | None = None
    channel_order_id: str | None = None
    receipt_id: str | None = None
    external_order_id: str | None = None
    
    # Location & Channel
    location_key: str | None = None
    location_key_source: LocationKeySource | None = None
    channel: str | None = None
    delivery_system: str | None = None
    
    # Status (Summarized state)
    current_status: str | None = None
    current_status_event_id: str | None = None
    current_status_time: datetime | None = None
    current_status_time_quality: EventTimeQuality = EventTimeQuality.UNKNOWN
    
    payment_status: str | None = None
    payment_method: str | None = None
    
    # Timestamps
    pickup_time_raw: str | None = None
    pickup_time_local: str | None = None
    pickup_timezone: str | None = None
    pickup_time_utc: str | None = None
    source_timezone_explicit: bool = False
    
    # Financials (Stored as string to preserve precision)
    subtotal: str | None = None
    discount_total: str | None = None
    delivery_fee: str | None = None
    service_charge: str | None = None
    tip: str | None = None
    tax_total: str | None = None
    order_total: str | None = None
    currency: str | None = None
    
    # Encrypted PII (Stored as BLOBs)
    customer_name_encrypted: bytes | None = None
    customer_phone_original_encrypted: bytes | None = None
    customer_phone_e164_encrypted: bytes | None = None
    customer_email_encrypted: bytes | None = None
    delivery_address_encrypted: bytes | None = None
    
    # Metadata
    first_seen_at: datetime = field(default_factory=datetime.utcnow)
    last_seen_at: datetime = field(default_factory=datetime.utcnow)
    source_file_id: str = ""
    
    def is_same_business_entity(self, other: Order) -> bool:
        """Check if another order record represents the exact same business entity state."""
        return self.business_key == other.business_key


@dataclass
class RunLock:
    """Represents a database-backed distributed lock."""
    lock_name: str
    run_id: str
    owner_pid: int
    owner_host: str
    lease_version: int
    acquired_at: datetime
    heartbeat_at: datetime
