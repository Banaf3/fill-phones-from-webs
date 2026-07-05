import time
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import re
from rich.console import Console
from playwright.sync_api import Page, TimeoutError

from deliverect_sync.browser.browser_factory import BrowserFactory
from deliverect_sync.browser.session_manager import SessionManager
from deliverect_sync.config import AppSettings
from deliverect_sync.logging_config import get_logger

logger = get_logger("direct_scrape")
console = Console()

class DirectScrapeWorkflow:
    def __init__(self, settings: AppSettings):
        self._settings = settings

    def execute(self) -> Path:
        factory = BrowserFactory(self._settings)
        session_mgr = SessionManager(self._settings)
        
        with factory:
            browser = factory.create_browser(headless=False)
            context, temp_path = factory.create_authenticated_context(browser)
            try:
                page = context.new_page()
                page.goto(self._settings.portal.base_url, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                # 1. Manual Navigation & Filtering Setup
                console.print("\n[bold yellow]*** ACTION REQUIRED ***[/bold yellow]")
                console.print("[yellow]1. Please manually navigate to the Orders page.[/yellow]")
                console.print("[yellow]2. Apply your desired filters (pickup time, location, channel).[/yellow]")
                console.print("[yellow]3. When you are on the first page of results, press ENTER here in the terminal.[/yellow]")
                input("Press ENTER to start scraping...")
                
                logger.info("Starting paginated scrape...")
                
                # Upfront Pagination Calculation
                page_text = page.locator("body").inner_text()
                showing_match = re.search(r'(?i)showing\s+(?:\d+\s*-\s*)?(\d+)\s*of\s*(\d+)', page_text)
                
                total_pages = 1
                if showing_match:
                    items_per_page = int(showing_match.group(1))
                    total_items = int(showing_match.group(2))
                    if items_per_page > 0:
                        total_pages = (total_items + items_per_page - 1) // items_per_page
                    logger.info(f"Detected {total_items} total orders ({items_per_page} per page). Will process {total_pages} pages.")
                else:
                    logger.warning("Could not automatically detect total pages from 'Showing X of Y'. Defaulting to 50 pages maximum.")
                    total_pages = 50
                
                results = []
                
                for page_num in range(1, total_pages + 1):
                    logger.info(f"Processing page {page_num} of {total_pages}...")
                    
                    # 2. Expand all orders on the current page
                    collapse_buttons = page.locator(".collapse-arrow-button-container")
                    btn_count = collapse_buttons.count()
                    logger.info(f"Found {btn_count} orders to expand on this page.")
                    
                    for i in range(btn_count):
                        try:
                            # Try to click to expand
                            collapse_buttons.nth(i).click(timeout=3000)
                            page.wait_for_timeout(300) # Short wait for animation/load
                        except Exception as e:
                            logger.debug(f"Could not click expand button {i}: {e}")
                            
                    logger.info("Waiting for order details to fully render...")
                    page.wait_for_timeout(3000) # Increased wait to ensure all details are fully rendered
                    
                    # 3. Extract the data per order row
                    rows = page.locator(".table-list-item-wrapper")
                    details_containers = page.locator(".order-item-detail-main-column-item")
                    row_count = rows.count()
                    details_count = details_containers.count()
                    logger.info(f"Found {row_count} order rows and {details_count} detail containers to parse.")
                    
                    current_page_data = []
                    
                    for i in range(row_count):
                        row = rows.nth(i)
                        
                        phone = ""
                        branch = ""
                        
                        # Extract Phone from the corresponding details container
                        if i < details_count:
                            details_container = details_containers.nth(i)
                            container_text = details_container.inner_text()
                            
                            # First try: Look for exact Phone label inside container
                            labels = details_container.locator(".order-item-detail-label-text")
                            all_labels = []
                            for j in range(labels.count()):
                                text = labels.nth(j).inner_text().strip()
                                all_labels.append(text)
                                if "phone" in text.lower():
                                    phone = text.lower().split("phone")[-1].replace(":", "").strip()
                                    break
                                    
                            # Second try: generic phone regex on entire container text if first failed
                            if not phone:
                                phone_match = re.search(r'(?i)phone\s*[:-]?\s*([+\d\s\-\(\)]+)', container_text)
                                if phone_match:
                                    phone = phone_match.group(1).strip()
                                    
                            # Third try: just find any sequence of 9+ digits starting with + or 0
                            if not phone:
                                raw_match = re.search(r'(?:[+0]\d\s*){9,15}', container_text)
                                if raw_match:
                                    phone = raw_match.group(0).strip()
                                    
                            if not phone and all_labels:
                                logger.warning(f"Row {i} - Could not find phone. Available labels were: {all_labels}")
                                
                        # Extract Branch from the main row wrapper
                        tooltips = row.locator('[data-tooltip-id="tooltip"]')
                        branch = ""
                        if tooltips.count() > 0:
                            attr_content = tooltips.first.get_attribute("data-tooltip-content")
                            branch = attr_content.strip() if attr_content else tooltips.first.inner_text().strip()
                            
                        # Even if empty, we might want to log it if the row was opened, but let's only save if we got something
                        if phone or branch:
                            current_page_data.append((phone, branch))
                            results.append({
                                "م": len(results) + 1,
                                "رقم الجوال Mobile Number": phone,
                                "الفرع Branch": branch
                            })
                            
                    # 4. Check for next page
                    if page_num == total_pages:
                        logger.info("Processed all calculated pages. Stopping.")
                        break
                        
                    next_button = page.locator(".list-pagination-next-container, .chevron-right, [class*='chevron-right']").last
                    
                    if next_button.count() == 0:
                        logger.info("Next button not found on the page. Stopping.")
                        break
                        
                    # Click next page
                    logger.info("Clicking next page...")
                    try:
                        next_button.click(timeout=5000)
                        
                        logger.info("Waiting for next page to finish downloading (fixed 5-second wait)...")
                        page.wait_for_timeout(5000)
                        
                    except Exception as e:
                        logger.warning(f"Could not click next page button: {e}. Stopping here.")
                        break
                
                if not results:
                    raise ValueError("No data found! Scrape completed but no order details/phone numbers were extracted.")
                    
                # 5. Put it in Excel
                timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
                filename = f"scraped_labels_{timestamp}.xlsx"
                output_path = Path(self._settings.output.resolved_directory) / filename
                
                self._settings.output.resolved_directory.mkdir(parents=True, exist_ok=True)
                
                # Create DataFrame
                df = pd.DataFrame(results, columns=["م", "رقم الجوال Mobile Number", "الفرع Branch"])
                
                # Ensure the branch column is treated as a string
                df["الفرع Branch"] = df["الفرع Branch"].astype(str)
                
                # Reset the index column 'م' to be sequential from 1 to N after reversing
                df["م"] = range(1, len(df) + 1)
                
                df.to_excel(output_path, index=False)
                logger.info(f"Saved scraped data to {output_path}")
                
                return output_path
                
            finally:
                if temp_path and temp_path.exists():
                    temp_path.unlink()
