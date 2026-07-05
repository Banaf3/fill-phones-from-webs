# fill-phones-from-webs (Deliverect Phone Number Scraper)

This repository contains an automated scraping tool designed to perfectly extract customer phone numbers and branch locations from Deliverect's Enterprise order dashboard into a clean Excel spreadsheet.

## How to use the system

The easiest way to use this project on Windows is through the included launcher in the project folder. 

### 1. Quick Start / Installation
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

### 2. Extract Phone Numbers to Excel (Main Feature)
To automatically scrape all order phone numbers and branches from the Deliverect screen into an Excel file, simply run:

```powershell
.\run.bat scrape
```

**How it works:**
1. A browser window will open to the Deliverect Enterprise Orders page.
2. The terminal will pause and ask you to log in (if needed) and manually apply your desired filters (e.g. pickup time, location, delivery channel).
3. Once you are looking at the first page of results you want to scrape, **go back to the terminal and press ENTER**.
4. The bot will take over! It will automatically:
   - Identify the total number of pages.
   - Expand every order.
   - Extract the customer's phone number and the branch name.
   - Click to the next page and repeat.
   - Generate a perfectly formatted `.xlsx` file containing the data from oldest order to newest order!

### 3. Re-Authenticating
If your login expires or Deliverect logs you out, you can run this command to safely log back in:

```powershell
.\run.bat reauthenticate
```

### Important notes
- In PowerShell, always use `.\run.bat` (with the dot-slash) instead of just typing `run.bat`.
- Do not touch the mouse or keyboard while the bot is automatically flipping through the pages (after you press Enter).