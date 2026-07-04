# fill-phones-from-webs (Deliverect Order Sync)

This repository contains the **Deliverect Order Sync** application, which automates the extraction, encryption, normalization, and export of restaurant orders from Deliverect.

## How to use the system

The easiest way to use this project on Windows is through the included launcher in the project folder. The most common pitfall is using the wrong command form in PowerShell; batch files must be invoked with `./` or `.\\`.

### 1. Windows PowerShell quick start
Open PowerShell in the project folder and run:

```powershell
cd deliverect-order-sync
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
playwright install chromium
```

If PowerShell blocks the activation script, run this once in the same terminal before activating:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 2. Create the configuration files
Run these once before the first use:

```powershell
Copy-Item config.example.yaml config.yaml
Copy-Item selectors.example.yaml selectors.yaml
```

You can then edit `config.yaml` to choose the channels, statuses, date range, and privacy settings you want to use.

### 3. Calibrate on first run
Because Deliverect can change its website layout, the tool needs a calibration step the first time you use it, or whenever the interface changes.

```powershell
.\run.bat calibrate
```

What happens:
- A browser window opens.
- You log in to Deliverect manually.
- The wizard asks you to navigate to the relevant pages and press Enter.
- The detected element locations are saved to `selectors.yaml`.

If the app says your session has expired, run:

```powershell
.\run.bat reauthenticate
```

### 4. Run a sync
To download orders, process them, encrypt customer data, and generate the Excel report:

```powershell
.\run.bat sync
```

Useful options:
- `./run.bat sync --dry-run`
- `./run.bat sync --export-only`

### 5. Open the dashboard
To inspect sync history and data quality:

```powershell
.\run.bat dashboard
```

### Important notes
- In PowerShell, always use `./run.bat` or `.\\run.bat` instead of `run.bat`.
- The launcher expects a virtual environment named `.venv`.
- If you created `venv` instead of `.venv`, recreate it as `.venv` or update the launcher to match your environment name.