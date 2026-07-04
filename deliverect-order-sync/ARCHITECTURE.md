# Deliverect Order Sync — Current System Architecture

This document reflects the implementation that currently exists in the repository. It is intended to be a practical reference for developers and AI assistants working on the project.

---

## 1. Purpose and Scope

Deliverect Order Sync is a Windows-first desktop automation tool that:

- opens a browser session to Deliverect,
- logs in manually or reuses saved auth state,
- navigates to the Orders and Operations pages,
- requests an export,
- downloads the resulting CSV,
- imports the CSV into a local SQLite database,
- stores encrypted PII when enabled,
- and produces an Excel workbook and a small dashboard.

The system is implemented as a Python package with a Typer-based CLI and a Playwright-driven browser workflow.

---

## 2. Current Runtime Architecture

### 2.1 CLI entrypoint
The main user-entry module is [src/deliverect_sync/cli.py](src/deliverect_sync/cli.py).

It exposes the following commands:

- `login`: starts a visible browser for manual authentication
- `calibrate`: runs the selector-discovery wizard
- `run`: executes the full workflow
- `export`: performs export and download only
- `import-file`: imports an existing CSV without browser automation
- `status`: shows current run status and lock state
- `reauthenticate`: clears or refreshes auth state for a new login
- `reset-database`: creates a backup and resets the SQLite database
- `purge-expired`: removes expired operational artifacts

The project also ships with [run.bat](run.bat), which activates the local `.venv` environment and forwards the command to the Python CLI.

### 2.2 Configuration layer
Configuration comes from [src/deliverect_sync/config.py](src/deliverect_sync/config.py).

The application loads:

- [config.yaml](config.yaml) for runtime behavior,
- [selectors.yaml](selectors.yaml) for browser locators,
- environment variables for override paths when present.

Key configuration groups include:

- `portal`: portal URL and portal type
- `export`: date mode, locations, channels, statuses, requested fields, item splitting
- `privacy`: PII inclusion and retention behavior
- `output`: Excel output location and naming
- `browser`: headless behavior and timeouts
- `polling`: export polling intervals
- `diagnostics`: logging and screenshots

### 2.3 Browser and session automation
The browser layer lives under [src/deliverect_sync/browser](src/deliverect_sync/browser).

Main modules:

- [src/deliverect_sync/browser/browser_factory.py](src/deliverect_sync/browser/browser_factory.py): creates Playwright browsers and contexts
- [src/deliverect_sync/browser/session_manager.py](src/deliverect_sync/browser/session_manager.py): handles login, session verification, and permission checks
- [src/deliverect_sync/browser/calibration.py](src/deliverect_sync/browser/calibration.py): interactive calibration wizard
- [src/deliverect_sync/browser/locator_registry.py](src/deliverect_sync/browser/locator_registry.py): resolves selectors from YAML
- [src/deliverect_sync/browser/pages](src/deliverect_sync/browser/pages): page objects for login, orders, operations, export dialog, and order detail views

The current implementation is stateful and browser-driven. It relies on selector-based automation rather than a formal API integration.

---

## 3. End-to-End Workflow

### 3.1 Authentication
The workflow begins by checking whether encrypted auth state exists. If it does not, the user must authenticate manually.

Authentication is handled by:

- [src/deliverect_sync/security/auth_state.py](src/deliverect_sync/security/auth_state.py)
- [src/deliverect_sync/security/encryption.py](src/deliverect_sync/security/encryption.py)

The auth state is serialized to JSON, encrypted, and stored in the user profile area. The implementation uses Fernet encryption managed through the OS credential manager.

### 3.2 Calibration
Calibration is a guided process that asks the user to navigate Deliverect pages and confirms the relevant UI elements. The results are written to [selectors.yaml](selectors.yaml).

### 3.3 Export workflow
The orchestrator is [src/deliverect_sync/workflow/export_workflow.py](src/deliverect_sync/workflow/export_workflow.py).

The workflow currently performs these stages:

1. create a sync run record
2. acquire a database lock
3. authenticate and open a browser context
4. verify the session and permissions
5. open the Orders page
6. apply configured filters
7. capture the pre-export operations state
8. request the export from the export dialog
9. locate the newly created export job in the Operations page
10. wait for the export operation to complete
11. download the CSV
12. import the CSV into SQLite
13. generate an Excel report
14. optionally delete the raw automated download

### 3.4 Import workflow
The importer is [src/deliverect_sync/importers/order_importer.py](src/deliverect_sync/importers/order_importer.py).

It:

- detects CSV format and delimiter,
- maps headers to canonical names with [src/deliverect_sync/importers/header_mapper.py](src/deliverect_sync/importers/header_mapper.py),
- validates rows,
- parses dates and money into normalized Python values,
- redacts PII for audit storage,
- stores raw rows, import errors, and field mappings,
- creates or updates orders,
- appends status events,
- builds item snapshots and line items.

### 3.5 Excel export
The Excel exporter is [src/deliverect_sync/exporters/excel_exporter.py](src/deliverect_sync/exporters/excel_exporter.py).

The current exporter:

- reads orders and items from SQLite,
- decrypts enabled PII fields for output,
- converts financial values into Decimal-aware data,
- sanitizes values that could become formulas in Excel,
- writes one or more sheets to an `.xlsx` file.

### 3.6 Dashboard
A read-only dashboard is implemented in [src/deliverect_sync/dashboard/app.py](src/deliverect_sync/dashboard/app.py).

It presents:

- overview metrics,
- recent sync history,
- data quality and import errors.

---

## 4. Core Data Model

The canonical domain objects are defined in [src/deliverect_sync/models.py](src/deliverect_sync/models.py).

### 4.1 Core enums
The model layer includes enums for:

- sync result state,
- run status,
- record kind,
- mapping status,
- source file origin,
- event time quality,
- location key source.

### 4.2 Main dataclasses
The main entities are:

- `SyncRun`: metadata for a single workflow execution
- `SourceFile`: metadata for a downloaded or user-supplied CSV
- `Order`: the canonical order summary used for reporting
- `OrderEvent`: append-only history of state changes
- `OrderItemSnapshot`: a versioned snapshot of order items
- `OrderItem`: a line item belonging to a snapshot
- `RawExportRow`: redacted audit row
- `ImportErrorRecord`: a persisted validation or import error
- `RunLock`: database-backed lock for workflow concurrency control

---

## 5. Storage and Database Design

The SQLite layer is implemented in [src/deliverect_sync/storage/database.py](src/deliverect_sync/storage/database.py) and the schema is built by [src/deliverect_sync/storage/migrations.py](src/deliverect_sync/storage/migrations.py).

### 5.1 Database characteristics
The database uses:

- WAL mode for better concurrent reads/writes,
- SQLite transactions for atomic updates,
- explicit lock records to prevent overlapping workflow runs,
- text storage for money values to preserve Decimal precision.

### 5.2 Main tables

- `run_locks`: tracks active execution locks
- `sync_runs`: stores run metadata and final result
- `source_files`: records imported or downloaded files
- `field_mappings`: stores header mapping decisions per source file
- `orders`: canonical order summary table
- `order_events`: append-only event log for state changes
- `order_item_snapshots`: versioned item snapshots
- `order_items`: flattened line items for the active snapshot
- `raw_export_rows`: redacted/raw-row audit table
- `import_errors`: validation and parse errors

### 5.3 Current persistence behavior
The current implementation favors a simple local-first design over a distributed or service architecture. The database is expected to live under the user profile directory and is not designed for multi-user access.

---

## 6. Security and Privacy Model

### 6.1 Authentication state
Browser authentication state is encrypted and stored locally.

### 6.2 PII protection
PII (customer name, phone, email, address) is encrypted at rest when enabled in the configuration. The encryption logic is implemented in:

- [src/deliverect_sync/security/pii.py](src/deliverect_sync/security/pii.py)
- [src/deliverect_sync/security/encryption.py](src/deliverect_sync/security/encryption.py)

### 6.3 Redaction and export safety
The system redacts PII in raw-row storage and sanitizes values to reduce Excel formula injection risk. The exporter uses defensive handling for strings that start with formula-like characters.

### 6.4 Key management
Encryption keys are stored through the OS credential manager using `keyring`.

---

## 7. Important Current Implementation Notes

The current implementation is functional but intentionally pragmatic. Some important characteristics are:

- it is Windows-oriented and assumes a local desktop environment,
- authentication is manual and browser-based,
- the workflow relies on Deliverect UI selectors and page structure,
- the importer is designed around CSV structure rather than a formal Deliverect API,
- the database is local SQLite rather than a server-backed system,
- the system is optimized for reliability and auditability more than for high-throughput or multi-tenant deployment.

---

## 8. Extension Points

The most natural extension points are:

1. adding new CSV columns by updating the header-map aliases and importer parsing logic,
2. adding new browser page interactions by extending the page-object layer,
3. improving selector robustness through richer calibration and fallback handling,
4. expanding the dashboard with more analytics,
5. adding retention policies and cleanup automation around raw files and screenshots,
6. improving the import pipeline to support richer status normalization and duplicate resolution.
