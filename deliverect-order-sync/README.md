# Deliverect Order Scraper

A robust, user-assisted automation tool for scraping customer phone numbers and branch information directly from the Deliverect Enterprise orders dashboard into formatted Excel files.

## Features

- **Assisted Scraping Workflow (`scrape`)**: The primary feature of this repository. It automatically calculates the number of pages, expands order details, and securely extracts the customer phone numbers and branch locations from Deliverect's Enterprise UI.
- **Perfect Excel Formatting**: Generates a clean `.xlsx` file natively formatted with Arabic column headers, sequential indexing, and chronologically ordered from oldest to newest.
- **Smart Navigation**: Automatically flips through pagination and waits for UI elements to render completely without brittle timeouts.

## Requirements

- Python 3.11+
- Windows
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

Copy the example configuration file to set your default URLs or output locations (if necessary):

```powershell
Copy-Item config.example.yaml config.yaml
```

Update `config.yaml` to ensure the portal is set to `enterprise` and the base URL is `https://enterprise.deliverect.com/`.

## Usage

The application is controlled via the `run.bat` helper script.

### 1. Scrape Phone Numbers

This is the main workflow of the app. To start scraping:

```powershell
.\run.bat scrape
```

**Workflow:**
1. A Chromium browser will open.
2. The terminal will pause. You must use the browser to log in (if prompted), navigate to the Orders page, and apply any filters you need.
3. Once the first page of the filtered orders is visible, return to the terminal and press **ENTER**.
4. The system will automatically calculate the number of pages, iterate through all of them, extract the data, and output an Excel file into the `data/exports` folder.

### 2. Reauthenticate

If you encounter session issues or Deliverect logs you out, you can renew your local session cookies by running:

```powershell
.\run.bat reauthenticate
```

## Troubleshooting

- In PowerShell, batch files must be prefixed with `.\` (e.g., `.\run.bat scrape`).
- The script expects a virtual environment specifically named `.venv`.
- If the bot extracts an empty file, ensure you actually waited for the first page to load completely before pressing Enter.
