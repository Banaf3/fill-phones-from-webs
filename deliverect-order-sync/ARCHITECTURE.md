# Deliverect Order Sync — Comprehensive System Architecture

This document serves as the **Single Source of Truth** for the system's design, architecture, and data structures. It provides developers (and AI assistants) with enough context to understand, maintain, and extend the system without reading the entire codebase.

---

## 1. System Overview & Philosophy

The Deliverect Order Sync application is a **local, secure, browser-automation tool** designed to download and normalize order data from a restaurant's Deliverect portal. 

**Core Principles:**
1. **Event Sourcing & Immutability:** Order statuses are treated as immutable events in an `order_events` append-only log. The canonical `orders` table only maintains the summarized current state, updated via strict timestamp precedence rules.
2. **Authoritative Item Snapshots:** Order items are deduplicated using a snapshot versioning policy. A newer, complete export snapshot will authoritatively replace an older active item set.
3. **Security & Privacy (PII):** Passwords are never stored. Session cookies are serialized and encrypted. Customer PII (Name, Phone, Email, Address) is redacted from raw JSON and encrypted at rest in SQLite `BLOB` columns using `Fernet` keys held in the Windows Credential Manager. 
4. **Resilient Locking & Concurrency:** A database-backed distributed lock (`run_locks`) prevents concurrent sync conflicts, utilizing lease versions, heartbeats, and safe stale-lock recovery.

---

## 2. Component Architecture

### 2.1. CLI Layer (`src/deliverect_sync/cli.py`)
Exposes commands:
- `sync`: Runs the end-to-end extraction and normalization pipeline using Playwright.
- `import-file [PATH]`: Manually imports a downloaded CSV, operating completely offline.
- `calibrate`: Interactive wizard to re-map UI locators if Deliverect updates its interface.
- `login` / `reauthenticate`: Standalone commands to handle manual auth flow.
- `status`: Displays current job locks and synchronization state.
- `reset-database`: Creates a safe checkpointed SQLite backup before wiping the database (requires confirmation).
- `purge-expired`: Cleans up historical data based on retention policies.

### 2.2. Browser & Workflow Orchestration
- **Session Manager (`session_manager.py`)**: Serializes Playwright context state to JSON, encrypts it using `cryptography.fernet`, and stores it locally under the `deliverect_sync_auth_state_key` namespace.
- **Download & File Lifecycle (`download_manager.py`, `export_workflow.py`)**: 
  - Tracks files by `SourceFileOrigin` (`AUTOMATED_DOWNLOAD` vs `USER_SUPPLIED`).
  - **Deletion Policy:** Automatically downloaded CSVs are strictly deleted *only* after validation succeeds, hashes are recorded, DB commits, and no further workflow stages need them. User-supplied files are *never* modified or deleted.

### 2.3. Data Ingestion & Normalization (`src/deliverect_sync/importers/` & `normalization/`)
- **Header Mapper (`header_mapper.py`)**: Uses strict boundaries: exact matches, explicit aliases, and restricted fuzzy matching. Ambiguous mappings for critical fields require explicit review (`REQUIRED_REVIEW`).
- **Order Importer (`order_importer.py`)**:
  - Generates a **Canonical Business Key** in the following fallback hierarchy to prevent multi-tenant and location collisions:
    1. `deliverect:{account_key}:{deliverect_order_id}`
    2. `channel:{account_key}:{channel}:{location_key}:{channel_order_id}`
    3. `receipt:{account_key}:{channel}:{location_key}:{receipt_id}`
  - If identity is unresolvable, the row is flagged as `IDENTITY_UNRESOLVED` (requires review). Do not use row numbers as identity.
  - **Item Fingerprints**: Resolves exact items via a fingerprint hash of (PLU, normalized name, quantity, unit price, line total, parent relationship), assigning a deterministic `occurrence_index` to handle identical duplicates correctly.
- **Normalizers**:
  - Parses Arabic digits (`١٥٠.٥٠`) into strict Python `Decimal` objects.
  - Normalizes numbers to E.164 (`+966...`).

### 2.4. Storage & Export (`src/deliverect_sync/storage/` & `exporters/`)
- **SQLite Database (`database.py`)**: Uses WAL mode.
- **Excel Exporter (`excel_exporter.py`)**:
  - Formats monetary columns strictly as numeric Excel cells to preserve `Decimal` precision without Pandas floating-point coercion.
  - Prepends `'` (Formula Injection protection) strictly to untrusted text cells (not explicit numeric or boolean fields).

---

## 3. Data Models & Schemas

Key SQLite tables include:

- **`run_locks`**: Distributed lock table tracking `lock_name`, `owner_pid`, `owner_host`, `lease_version`, `acquired_at`, and `heartbeat_at`.
- **`source_files`**: Tracks downloaded CSV files, distinguishing `AUTOMATED_DOWNLOAD` from `USER_SUPPLIED` via the `origin` field.
- **`orders`**: Canonical representation of an order (Summarized state).
  - *Identities*: `business_key`, `location_key`, `location_key_source`.
  - *Current Status*: `current_status`, `current_status_time_quality` (Updated via precedence: `AUTHORITATIVE` > `INFERRED` > `OBSERVED_ONLY` > `UNKNOWN`).
  - *Financials*: `order_total`, `subtotal`, etc. (Stored as TEXT in SQLite for exact Decimal precision).
  - *Encrypted PII*: `customer_name_encrypted`, `customer_phone_e164_encrypted`, `delivery_address_encrypted` (Stored strictly as SQLite `BLOB`s).
- **`order_events`**: Append-only log of status history.
  - *Deduplication*: `UNIQUE(source_file_id, source_row_number, event_kind)` (same-file), `UNIQUE(order_id, canonical_event_hash)` (cross-file).
- **`order_item_snapshots`**: Authoritative snapshot versions of an order's items (`is_complete` flag).
- **`order_items`**: Line items belonging to a snapshot. Unique constraint: `UNIQUE(order_id, item_fingerprint, occurrence_index)`.
- **`raw_export_rows`**: Auditing table storing `redacted_json` (TEXT) and optionally `encrypted_original_json` (BLOB) if retention config permits.

---

## 4. Security & Cryptography Details

- **PII Encryption (`security/pii.py`)**: Customer PII fields are encrypted at rest using `cryptography.fernet.Fernet`. The Fernet tokens are stored as raw `bytes` in Python and `BLOB` in SQLite, avoiding implicit base64 string conversion bugs.
- **Key Management**: Keys are stored in the OS Credential Manager under:
  - `deliverect_sync_auth_state_key` (Playwright session state)
  - `deliverect_sync_pii_key` (Customer PII and raw rows)

---

## 5. Development Guide & Extension Points

1. **Adding a new column from the CSV**:
   - Add the canonical field to `HEADER_ALIASES` in `importers/header_mapper.py`. 
   - Update the `Order` dataclass in `models.py`.
   - Update `upsert_order()` schema in `storage/database.py` and `migrations.py`.
   - Map it during `_parse_row()` in `importers/order_importer.py`.

2. **Handling Offline Import**:
   - Use `run.bat import-file path/to/file.csv`. This bypasses Playwright entirely and feeds the CSV directly into `OrderImporter`. The file origin is marked as `USER_SUPPLIED` and is guaranteed not to be deleted.

3. **Status Precedence Logic**:
   - Do not assume chronological row order or import time equates to actual status event sequence. When adding logic that resolves state, rely on `EventTimeQuality` in `database.py`.
