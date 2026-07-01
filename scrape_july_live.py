# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

import datetime
import random
import time
import sys
import os

# Ensure app modules are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.agent import run_market_scan_tool

# Configuration
LOCATION = "Greencastle"
STAY_NIGHTS = 2
GUEST_COUNTS = [2, 4, 6]

# July Phased Weeks
WEEKS = [
    ("Week 1", datetime.date(2026, 7, 5), datetime.date(2026, 7, 11)),
    ("Week 2", datetime.date(2026, 7, 12), datetime.date(2026, 7, 18)),
    ("Week 3", datetime.date(2026, 7, 19), datetime.date(2026, 7, 25)),
    ("Week 4", datetime.date(2026, 7, 26), datetime.date(2026, 7, 31)),
]

def main():
    print("=" * 60)
    print(" RateRadar: Phased Live July Scan Runner")
    print("=" * 60)
    print("This script scrapes Booking.com in weekly batches.")
    print("To prevent bot blocks and IP blocks, the runner implements:")
    print("  - 5 to 10 seconds random delay between individual scans.")
    print("  - 60 seconds cool-down period between weekly batches.")
    print("=" * 60)

    total_scans = 0
    live_success = 0
    seed_fallback = 0
    failures = 0

    for week_name, start_date, end_date in WEEKS:
        print(f"\n>>> Starting {week_name} Scans ({start_date} to {end_date})")
        print("-" * 50)
        
        # Build dates for this week
        dates = []
        current = start_date
        while current <= end_date:
            dates.append(current)
            current += datetime.timedelta(days=1)

        for date_obj in dates:
            checkin = date_obj.strftime("%Y-%m-%d")
            checkout = (date_obj + datetime.timedelta(days=STAY_NIGHTS)).strftime("%Y-%m-%d")

            for guests in GUEST_COUNTS:
                label = f"{checkin} ({guests} guests)"
                print(f"Scanning {label}...", end="", flush=True)

                # Delay before request to act like human browsing
                sleep_sec = random.uniform(5.0, 10.0)
                time.sleep(sleep_sec)

                try:
                    result = run_market_scan_tool(
                        location=LOCATION,
                        checkin_date=checkin,
                        checkout_date=checkout,
                        guest_count=guests,
                        force_fallback=False,
                    )
                    
                    src = result.get("data_source", "unknown")
                    status = result.get("status", "?")
                    median = result.get("competitor_median", 0)

                    if src == "live":
                        print(f" -> [LIVE DATA] Success! Median=€{median:.0f} Status={status}")
                        live_success += 1
                    else:
                        print(f" -> [SYNTHETIC FALLBACK] Blocked/Unavailable. Status={status}")
                        seed_fallback += 1
                    
                    total_scans += 1

                except Exception as e:
                    print(f" -> [FAILED] Error: {e}")
                    failures += 1
                    time.sleep(15)  # Pause longer on failure
        
        print(f"Finished {week_name}. Cooling down connection for 60 seconds...")
        time.sleep(60)

    print("\n" + "=" * 60)
    print(" Live July Scan Summary")
    print("=" * 60)
    print(f"Total Scans Triggered: {total_scans + failures}")
    print(f"  - Scraped successfully (LIVE):  {live_success}")
    print(f"  - Scraper blocked (FALLBACK):   {seed_fallback}")
    print(f"  - Execution errors:             {failures}")
    print("=" * 60)
    print("Open the dashboard to view the refreshed July data!")

if __name__ == "__main__":
    main()
