"""Order importer — parses CSV rows into Order, OrderItemSnapshot, and OrderEvents.

Handles item-split rows, monetary Decimal parsing, customer PII filtering, 
event appending with precedence logic, and raw row preservation with idempotency.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from deliverect_sync.config import AppSettings
from deliverect_sync.importers.csv_detector import detect_csv
from deliverect_sync.importers.header_mapper import HeaderMapper
from deliverect_sync.importers.validators import validate_row
from deliverect_sync.logging_config import get_logger
from deliverect_sync.models import (
    EventTimeQuality,
    ImportErrorRecord,
    LocationKeySource,
    Order,
    OrderEvent,
    OrderItem,
    OrderItemSnapshot,
    RawExportRow,
    RecordKind,
    SourceFile,
    SourceFileOrigin,
)
from deliverect_sync.normalization.money import parse_money
from deliverect_sync.normalization.dates import parse_datetime
from deliverect_sync.normalization.phones import normalize_phone
from deliverect_sync.security.pii import PIIFieldEncryption
from deliverect_sync.security.encryption import get_pii_encryption
from deliverect_sync.storage.database import DatabaseManager, RunLock

logger = get_logger("order_importer")


@dataclass
class ImportResult:
    """Summary of an import operation."""
    imported_rows: int = 0
    new_orders: int = 0
    updated_orders: int = 0
    rejected_rows: int = 0
    errors: list[ImportErrorRecord] = field(default_factory=list)


class OrderImporter:
    """Imports Deliverect CSV exports into the SQLite database."""

    def __init__(self, settings: AppSettings, db: DatabaseManager, lock: RunLock | None = None) -> None:
        self._settings = settings
        self._db = db
        self._pii = PIIFieldEncryption()
        self._encryption = get_pii_encryption()
        self._mapper = HeaderMapper()
        self._lock = lock

    def _compute_row_hash(self, row: dict[str, str]) -> str:
        row_str = json.dumps(row, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(row_str.encode("utf-8")).hexdigest()

    def _fingerprint_item(self, plu: str | None, name: str | None, qty: int | None, price: Decimal | None, total: Decimal | None) -> str:
        fp_str = f"{plu or ''}|{name or ''}|{qty or ''}|{price or ''}|{total or ''}"
        return hashlib.sha256(fp_str.encode("utf-8")).hexdigest()

    def import_csv(
        self, 
        filepath: Path, 
        run_id: str, 
        origin: SourceFileOrigin = SourceFileOrigin.USER_SUPPLIED
    ) -> ImportResult:
        result = ImportResult()

        if not self._lock:
            # For CLI manual imports, create a temporary lock
            self._lock = self._db.acquire_lock("import_lock", run_id, force=True)
            owns_lock = True
        else:
            owns_lock = False

        try:
            detection = detect_csv(filepath)
            self._mapper.map_headers(detection.header_row)

            file_bytes = filepath.read_bytes()
            file_hash = hashlib.sha256(file_bytes).hexdigest()
            file_id = str(uuid.uuid4())
            
            existing_file = self._db.get_source_file_by_hash(file_hash)
            source_file = SourceFile(
                id=file_id,
                sync_run_id=run_id,
                origin=origin,
                filename=filepath.name,
                original_path=str(filepath.absolute()),
                file_hash=file_hash,
                file_size_bytes=len(file_bytes)
            )
            self._db.create_source_file(source_file)

            if detection.has_bom and detection.encoding == "utf-8":
                file_bytes = file_bytes[3:]

            text = file_bytes.decode(detection.encoding, errors="replace")
            reader = csv.DictReader(io.StringIO(text), delimiter=detection.delimiter)

            orders_by_key: dict[str, Order] = {}
            parsed_items_by_order: dict[str, list[OrderItem]] = {}

            for row_num, row in enumerate(reader, start=2):
                try:
                    row_hash = self._compute_row_hash(row)
                    if self._db.raw_row_exists(file_id, row_num):
                        continue

                    errors = validate_row(row, self._mapper, row_num)
                    if errors:
                        for error in errors:
                            error.sync_run_id = run_id
                            error.source_file_id = file_id
                            self._db.create_import_error(error)
                            result.errors.append(error)
                        result.rejected_rows += 1
                        continue

                    order, event, item = self._parse_row(row, row_num, file_id, row_hash)
                    
                    if order.business_key.startswith("IDENTITY_UNRESOLVED"):
                        # We still log the error and reject
                        err = ImportErrorRecord(
                            id=str(uuid.uuid4()), sync_run_id=run_id, source_file_id=file_id,
                            row_number=row_num, stage="parse", error_code="IDENTITY_UNRESOLVED",
                            error_message="Could not resolve a stable business key for this order."
                        )
                        self._db.create_import_error(err)
                        result.errors.append(err)
                        result.rejected_rows += 1
                        continue

                    # Store Raw Row
                    redacted_row = dict(row)
                    pii_canonicals = {"customer_name", "customer_phone", "customer_email", "delivery_address", "order_notes"}
                    for pii_canon in pii_canonicals:
                        col_name = self._mapper.get_column_name(pii_canon)
                        if col_name and col_name in redacted_row:
                            redacted_row[col_name] = "[REDACTED]"
                            
                    redacted_json = json.dumps(redacted_row, ensure_ascii=False)
                    encrypted_json = None
                    if self._settings.privacy.retain_encrypted_raw_rows:
                        original_json = json.dumps(row, ensure_ascii=False).encode("utf-8")
                        encrypted_json = self._encryption.encrypt(original_json)

                    raw_row = RawExportRow(
                        id=str(uuid.uuid4()),
                        source_file_id=file_id,
                        source_row_number=row_num,
                        row_hash=row_hash,
                        redacted_json=redacted_json,
                        encrypted_original_json=encrypted_json,
                        imported_at=datetime.now(tz=timezone.utc),
                        validation_status="VALID"
                    )
                    self._db.create_raw_row(raw_row)

                    # Upsert Order Metadata
                    bk = order.business_key
                    if bk not in orders_by_key:
                        orders_by_key[bk] = order
                        is_new = self._db.upsert_order(order, self._lock)
                        if is_new:
                            result.new_orders += 1
                        else:
                            result.updated_orders += 1
                    else:
                        existing = orders_by_key[bk]
                        existing.last_seen_at = datetime.now(tz=timezone.utc)
                        order.id = existing.id # Link to primary ID
                        event.order_id = existing.id
                        if item:
                            item.order_id = existing.id

                    # Append Event and update Status
                    if event.event_kind != RecordKind.UNKNOWN:
                        appended = self._db.append_order_event(event, self._lock)
                        if appended:
                            self._db.update_order_current_status(
                                order.id, event, EventTimeQuality(event.event_time_source), self._lock
                            )

                    # Accumulate Items for Snapshot
                    if item:
                        if bk not in parsed_items_by_order:
                            parsed_items_by_order[bk] = []
                        
                        # Assign occurrence index
                        same_fingerprint = [i for i in parsed_items_by_order[bk] if i.item_fingerprint == item.item_fingerprint]
                        item.occurrence_index = len(same_fingerprint) + 1
                        parsed_items_by_order[bk].append(item)

                    result.imported_rows += 1

                except Exception as e:
                    error_record = ImportErrorRecord(
                        id=str(uuid.uuid4()), sync_run_id=run_id, source_file_id=file_id,
                        row_number=row_num, stage="parse", error_code="PARSE_ERROR",
                        error_message=str(e)[:500]
                    )
                    self._db.create_import_error(error_record)
                    result.errors.append(error_record)
                    result.rejected_rows += 1
                    logger.debug("Row %d parse error: %s", row_num, type(e).__name__)

            # Create authoritative item snapshots per order
            for bk, items in parsed_items_by_order.items():
                order = orders_by_key[bk]
                snapshot_id = str(uuid.uuid4())
                
                # Combine item fingerprints to hash the snapshot
                fingerprints = sorted([f"{i.item_fingerprint}:{i.occurrence_index}" for i in items])
                snapshot_hash = hashlib.sha256("".join(fingerprints).encode("utf-8")).hexdigest()
                
                snapshot = OrderItemSnapshot(
                    id=snapshot_id,
                    order_id=order.id,
                    source_file_id=file_id,
                    snapshot_time=datetime.now(tz=timezone.utc),
                    is_complete=True, # Assuming export contains all rows for order
                    created_at=datetime.now(tz=timezone.utc),
                    snapshot_hash=snapshot_hash
                )
                
                created = self._db.create_item_snapshot(snapshot, self._lock)
                if created:
                    for item in items:
                        item.snapshot_id = snapshot.id
                    self._db.insert_order_items(items, self._lock)

            logger.info("Import complete: %d imported, %d new, %d updated, %d rejected",
                        result.imported_rows, result.new_orders, result.updated_orders, result.rejected_rows)
            return result

        finally:
            if owns_lock and self._lock:
                self._db.release_lock(self._lock)


    def _parse_row(
        self,
        row: dict[str, str],
        row_num: int,
        source_file_id: str,
        row_hash: str,
    ) -> tuple[Order, OrderEvent, OrderItem | None]:
        def get(canonical: str) -> str | None:
            col_name = self._mapper.get_column_name(canonical)
            if col_name and col_name in row:
                val = row[col_name].strip()
                return val if val else None
            return None

        def get_money(canonical: str) -> Decimal | None:
            raw_val = get(canonical)
            return parse_money(raw_val) if raw_val else None

        order_id = str(uuid.uuid4())
        account_key = "default" # Or from settings/profile

        deliverect_order_id = get("deliverect_order_id")
        channel_order_id = get("channel_order_id")
        receipt_id = get("receipt_id")
        external_order_id = get("external_order_id")
        
        location_id = get("location_id")
        location_name = get("location")
        channel = get("channel")
        
        # Location Key Resolution
        location_key = location_id
        location_key_source = LocationKeySource.LOCATION_ID
        if not location_key:
            location_key = location_name
            location_key_source = LocationKeySource.NORMALIZED_NAME
            
        # Business Key Hierarchy
        business_key = "IDENTITY_UNRESOLVED"
        if deliverect_order_id:
            business_key = f"deliverect:{account_key}:{deliverect_order_id}"
        elif channel_order_id and channel and location_key:
            business_key = f"channel:{account_key}:{channel}:{location_key}:{channel_order_id}"
        elif receipt_id and channel and location_key:
            business_key = f"receipt:{account_key}:{channel}:{location_key}:{receipt_id}"

        # Status & Events
        status_raw = get("order_status")
        status_upper = status_raw.upper() if status_raw else ""
        record_kind = RecordKind.UNKNOWN
        if "CANCEL" in status_upper or "ملغى" in status_upper:
            record_kind = RecordKind.CANCELLATION
        elif "REMAKE" in status_upper:
            record_kind = RecordKind.REMAKE
        elif status_raw:
            record_kind = RecordKind.NORMAL
            
        pickup_raw = get("pickup_time")
        pickup_time_local = None
        pickup_time_utc = None
        pickup_timezone = self._settings.export.timezone
        source_timezone_explicit = False # Unless parsed strictly with tzinfo
        
        if pickup_raw:
            dt = parse_datetime(pickup_raw, pickup_timezone)
            if dt:
                pickup_time_local = dt.strftime("%Y-%m-%d %H:%M:%S")
                pickup_time_utc = dt.astimezone(timezone.utc).isoformat()
        
        now = datetime.now(tz=timezone.utc)
        
        # Event time defaults to pickup_time if no explicit event time provided
        event_time_source = EventTimeQuality.OBSERVED_ONLY.value
        event_time = now
        event_time_inferred = True
        
        if pickup_time_utc:
            event_time = datetime.fromisoformat(pickup_time_utc)
            event_time_source = EventTimeQuality.INFERRED.value # Using pickup time as inferred event time
        
        # Generate Event deduplication keys
        source_observation_key = f"{source_file_id}:{row_num}:{record_kind.value}"
        canonical_event_hash = None
        if event_time_source in (EventTimeQuality.AUTHORITATIVE.value, EventTimeQuality.INFERRED.value):
            evt_str = f"{business_key}:{record_kind.value}:{status_raw}:{event_time.isoformat()}"
            canonical_event_hash = hashlib.sha256(evt_str.encode("utf-8")).hexdigest()

        event = OrderEvent(
            id=str(uuid.uuid4()),
            order_id=order_id,
            event_kind=record_kind,
            normalized_status=status_raw, # Need mapping table
            raw_status=status_raw,
            event_time=event_time,
            event_time_source=event_time_source,
            event_time_inferred=event_time_inferred,
            observed_at=now,
            source_file_id=source_file_id,
            source_row_number=row_num,
            source_observation_key=source_observation_key,
            canonical_event_hash=canonical_event_hash
        )

        subtotal = get_money("subtotal")
        discount = get_money("discount_total")
        delivery_fee = get_money("delivery_fee")
        service_charge = get_money("service_charge")
        tip_val = get_money("tip")
        tax = get_money("tax_total")
        total = get_money("order_total")

        customer_name_enc = None
        customer_phone_orig_enc = None
        customer_phone_e164_enc = None
        customer_email_enc = None
        delivery_addr_enc = None

        if self._settings.privacy.include_customer_name:
            cn = get("customer_name")
            if cn: customer_name_enc = self._pii.encrypt_field(cn)

        if self._settings.privacy.include_customer_phone:
            cp = get("customer_phone")
            if cp:
                customer_phone_orig_enc = self._pii.encrypt_field(cp)
                e164 = normalize_phone(cp)
                if e164: customer_phone_e164_enc = self._pii.encrypt_field(e164)

        if self._settings.privacy.include_customer_email:
            ce = get("customer_email")
            if ce: customer_email_enc = self._pii.encrypt_field(ce)

        if self._settings.privacy.include_delivery_address:
            da = get("delivery_address")
            if da: delivery_addr_enc = self._pii.encrypt_field(da)

        order = Order(
            id=order_id,
            business_key=business_key,
            deliverect_order_id=deliverect_order_id,
            channel_order_id=channel_order_id,
            receipt_id=receipt_id,
            external_order_id=external_order_id,
            location_key=location_key,
            location_key_source=location_key_source,
            channel=channel,
            delivery_system=get("delivery_system"),
            payment_status=get("payment_status"),
            payment_method=get("payment_method"),
            pickup_time_raw=pickup_raw,
            pickup_time_local=pickup_time_local,
            pickup_timezone=pickup_timezone,
            pickup_time_utc=pickup_time_utc,
            source_timezone_explicit=source_timezone_explicit,
            subtotal=str(subtotal) if subtotal is not None else None,
            discount_total=str(discount) if discount is not None else None,
            delivery_fee=str(delivery_fee) if delivery_fee is not None else None,
            service_charge=str(service_charge) if service_charge is not None else None,
            tip=str(tip_val) if tip_val is not None else None,
            tax_total=str(tax) if tax is not None else None,
            order_total=str(total) if total is not None else None,
            currency=get("currency"),
            customer_name_encrypted=customer_name_enc,
            customer_phone_original_encrypted=customer_phone_orig_enc,
            customer_phone_e164_encrypted=customer_phone_e164_enc,
            customer_email_encrypted=customer_email_enc,
            delivery_address_encrypted=delivery_addr_enc,
            first_seen_at=now,
            last_seen_at=now,
            source_file_id=source_file_id,
        )

        item: OrderItem | None = None
        item_name = get("item_name")
        plu = get("plu")

        if item_name or plu:
            unit_price = get_money("unit_price")
            line_total = get_money("line_total")
            qty_raw = get("quantity")
            quantity: int | None = None
            if qty_raw:
                try: quantity = int(float(qty_raw))
                except ValueError: pass

            fp = self._fingerprint_item(plu, item_name, quantity, unit_price, line_total)

            item = OrderItem(
                id=str(uuid.uuid4()),
                snapshot_id="", # Set by caller
                order_id=order_id,
                item_fingerprint=fp,
                occurrence_index=1, # Updated by caller
                source_row_number=row_num,
                plu=plu,
                item_name=item_name,
                quantity=quantity,
                unit_price=str(unit_price) if unit_price is not None else None,
                line_total=str(line_total) if line_total is not None else None,
            )

        return order, event, item
