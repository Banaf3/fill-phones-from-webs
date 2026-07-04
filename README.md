# fill-phones-from-webs (Deliverect Order Sync)

This repository contains the **Deliverect Order Sync** application, which automates the extraction, encryption, normalization, and export of restaurant orders from Deliverect.

## How to use the system

Using the system is designed to be straightforward via the included `run.bat` script inside the `deliverect-order-sync` directory, which provides a simple command-line interface. 

### 1. Initial Setup & Configuration
First, make sure you have navigated into the project directory and created your configuration files by copying the examples (you only need to do this once):
```cmd
cd deliverect-order-sync
copy config.example.yaml config.yaml
copy selectors.example.yaml selectors.yaml
```
You can edit `config.yaml` to specify which channels (e.g., Deliveroo, HungerStation) and statuses you want to export, and configure privacy settings like PII encryption.

### 2. UI Calibration (First Run)
Because Deliverect can sometimes change its website layout, the system uses a "calibration" step to learn where buttons are. You **must** run this the very first time you use the tool, or if the tool ever fails to find a button on the Deliverect website.

In your terminal, run:
```cmd
run.bat calibrate
```
- A visible browser window will open.
- Log in to your Deliverect account manually.
- The command prompt will then ask you to navigate to specific pages (like Orders) and press Enter. It will auto-detect the buttons and save their exact locations to `selectors.yaml`.

### 3. Running a Sync (Daily Use)
Whenever you want to download new orders, parse them, encrypt customer data, and generate your final Excel report, simply run:
```cmd
run.bat sync
```
**What happens when you run this:**
1. The tool silently opens a browser in the background.
2. It navigates to the Orders page and requests a CSV export for your configured date range.
3. It waits for Deliverect to generate the file, downloads it, and parses it.
4. It safely encrypts customer details and saves everything to a local database.
5. It spits out a beautifully formatted Excel workbook (`.xlsx`) in the `output/` folder!

*(If you ever just want to download the raw CSV without processing it into the database, you can run `run.bat sync --export-only`)*.

### 4. Monitoring the System
If you want to view a visual summary of your downloaded orders, check for any data errors, or see your sync history, you can start the local dashboard:

```cmd
run.bat dashboard
```
This will open a Streamlit web page in your browser showing graphs and tables of all your processed data.