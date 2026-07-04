# Deliverect Order Sync

A local application for downloading, parsing, and normalizing order data from Deliverect via browser automation.

## Features

- **Automated Export Workflow**: Uses Playwright to interactively log in, navigate to Orders, trigger CSV exports, and download the resulting file.
- **Robust Field Mapping**: Intelligently maps varying Arabic and English CSV headers (e.g., "Order ID", "رقم الطلب", "Channel Order ID") to standard fields.
- **PII Protection**: Field-level AES-256-GCM encryption for customer details (name, phone, email, address) using Windows Credential Manager.
- **Normalization**: Standardizes phone numbers, dates, monetary values (including Arabic-Indic digits), and prevents CSV injection.
- **Item Split Handling**: Flattens nested item exports into separate rows or handles single-row orders.
- **Excel Generation**: Automatically generates a formatted Excel workbook with orders and items.
- **Dashboard**: Includes a Streamlit dashboard to view sync history and data quality.

## Requirements

- Python 3.11+
- Windows (uses Keyring for secure key storage)
- Chromium browser (installed automatically via Playwright)

## Installation

1. Clone this repository.
2. Create and activate a virtual environment:
   ```cmd
   python -m venv venv
   call venv\Scripts\activate
   ```
3. Install dependencies:
   ```cmd
   pip install -e .
   playwright install chromium
   ```

## Configuration

Copy the example configuration files and edit them to match your setup:

```cmd
copy config.example.yaml config.yaml
copy selectors.example.yaml selectors.yaml
```

Update `config.yaml` with your preferred locations, channels, and statuses to filter by.

## Usage

The application is controlled via a command-line interface (CLI) or the `run.bat` helper script.

### 1. Calibrate Locators

Before the first run, or if the Deliverect interface changes, run the calibration wizard to discover the necessary UI elements:

```cmd
run.bat calibrate
```

This will open a browser, prompt you to log in, and guide you through identifying buttons and menus. The results are saved to `selectors.yaml`.

### 2. Run Sync

To start a full sync operation (export, download, import, format to Excel):

```cmd
run.bat sync
```

You can also run a dry-run (won't save data) or export-only:

```cmd
run.bat sync --dry-run
run.bat sync --export-only
```

### 3. View Dashboard

To view the synchronization history and data quality metrics:

```cmd
run.bat dashboard
```

This will start a local Streamlit server and open it in your browser.

## Security Note

This tool **never** asks for or stores your Deliverect password. It uses an interactive browser window for you to log in manually, then saves the session state (cookies) securely encrypted using your OS keychain. If the session expires, it will prompt you to log in again.
