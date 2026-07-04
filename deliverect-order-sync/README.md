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

The setup below is written for Windows PowerShell.

1. Open PowerShell in the project folder.
2. Create and activate a virtual environment named `.venv`:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```powershell
   pip install -e ".[dev]"
   playwright install chromium
   ```

If PowerShell blocks activation, run this once in the same terminal first:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## Configuration

Copy the example configuration files and edit them to match your setup:

```powershell
Copy-Item config.example.yaml config.yaml
Copy-Item selectors.example.yaml selectors.yaml
```

Update `config.yaml` with your preferred locations, channels, and statuses to filter by.

## Usage

The application is controlled via the CLI or the `run.bat` helper script.

### 1. Calibrate Locators

Before the first run, or if the Deliverect interface changes, run the calibration wizard:

```powershell
.\run.bat calibrate
```

This opens a browser, prompts you to log in, and guides you through identifying the buttons and menus used by Deliverect. The results are saved to `selectors.yaml`.

### 2. Run Sync

To start a full sync operation (export, download, import, format to Excel):

```powershell
.\run.bat sync
```

Useful variants:

```powershell
.\run.bat sync --dry-run
.\run.bat sync --export-only
```

### 3. View Dashboard

To view the synchronization history and data quality metrics:

```powershell
.\run.bat dashboard
```

## Troubleshooting

- In PowerShell, batch files must be prefixed with `.\\` or `./`.
- The launcher expects a virtual environment named `.venv`.
- If you see an authentication/session error, run `./run.bat reauthenticate`.
- If you created a different virtual environment name, recreate it as `.venv` or adjust the launcher accordingly.

## Security Note

This tool **never** asks for or stores your Deliverect password. It uses an interactive browser window for you to log in manually, then saves the session state (cookies) securely encrypted using your OS keychain. If the session expires, it will prompt you to log in again.
