"""
Daily scheduler for the Pricing Monitor agent.

Runs once per day and scrapes Booking.com for every upcoming check-in date
in the configured 3-month window, for all guest capacities.

Usage:
    uv run python scheduler.py                  # runs in foreground, logs to console
    uv run python scheduler.py --once           # single immediate run then exit
    uv run python scheduler.py --dry-run        # print what would be scraped, don't scrape

The scheduler fires at DAILY_RUN_TIME (default 08:00 local time) every day.
Set the SCHEDULER_RUN_TIME env var to override, e.g. SCHEDULER_RUN_TIME=06:30.

To run in the background (macOS):
    nohup uv run python scheduler.py > logs/scheduler.log 2>&1 &
"""

import argparse
import datetime
import logging
import os
import sys
import time

# Ensure app modules are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.agent import run_market_scan_tool

# ── Configuration ────────────────────────────────────────────────────────────

LOCATION = "Greencastle"

# 3-month window: Jul 5 → Sep 30 2026
WINDOW_START = datetime.date(2026, 7, 5)
WINDOW_END = datetime.date(2026, 9, 30)

# Scrape every check-in date in the window (every day)
STEP_DAYS = 1

# Guest capacities to scan
GUEST_COUNTS = [2, 4, 6]

# Stay length (nights) — Harbour Bar has 2-night minimum
STAY_NIGHTS = 2

# Daily run time (HH:MM, 24hr, local timezone)
_raw_time = os.getenv("SCHEDULER_RUN_TIME", "08:00")
_hour, _minute = _raw_time.split(":")
DAILY_HOUR = int(_hour)
DAILY_MINUTE = int(_minute)

# ── Logging ──────────────────────────────────────────────────────────────────

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/scheduler.log"),
    ],
)
logger = logging.getLogger("scheduler")


# ── Core scan job ─────────────────────────────────────────────────────────────

def build_scan_dates() -> list[tuple[str, str]]:
    """Return all (checkin, checkout) pairs in the window."""
    dates = []
    current = WINDOW_START
    while current <= WINDOW_END:
        checkin = current.strftime("%Y-%m-%d")
        checkout = (current + datetime.timedelta(days=STAY_NIGHTS)).strftime("%Y-%m-%d")
        dates.append((checkin, checkout))
        current += datetime.timedelta(days=STEP_DAYS)
    return dates


def run_daily_scan(dry_run: bool = False):
    """
    Main job: scrape each date in the window for all guest counts.
    Called by the scheduler once per day.
    
    ROBUSTNESS PRACTICES:
      1. Groups upcoming dates into weekly chunks.
      2. Introduces 5 to 10 seconds random delay between individual scans.
      3. Introduces a 60 seconds cool-down period between weekly chunks.
      4. Early-stop / Safety block detection: If 3 consecutive scans return fallback seed data,
         switches force_fallback=True to preserve system resource usage and avoid spamming the travel site.
    """
    import random
    today = datetime.date.today()
    dates = build_scan_dates()

    # Only scan dates that are still in the future (or today)
    upcoming = [(ci, co) for ci, co in dates
                if datetime.date.fromisoformat(ci) >= today]

    logger.info(
        f"Daily scan started — {len(upcoming)} upcoming dates × "
        f"{len(GUEST_COUNTS)} guest counts = "
        f"{len(upcoming) * len(GUEST_COUNTS)} total scans."
    )

    # Chunk upcoming dates into weekly batches (7 dates per batch)
    weekly_chunks = [upcoming[i:i + 7] for i in range(0, len(upcoming), 7)]
    
    successes = 0
    failures = 0
    live_count = 0
    seed_count = 0
    consecutive_seed_count = 0
    force_fallback_run = False

    for chunk_idx, chunk in enumerate(weekly_chunks):
        logger.info(f"Processing Weekly Batch {chunk_idx + 1}/{len(weekly_chunks)} ({len(chunk)} dates)...")
        
        for checkin, checkout in chunk:
            for guests in GUEST_COUNTS:
                label = f"{checkin} / {guests} guests"

                if dry_run:
                    logger.info(f"  [DRY RUN] Would scan: {label}")
                    continue

                # Act like human browsing: random delay between 5 to 10 seconds
                time.sleep(random.uniform(5.0, 10.0))

                try:
                    # If safety mode triggered, automatically force fallback to avoid IP ban
                    active_fallback = force_fallback_run
                    
                    result = run_market_scan_tool(
                        location=LOCATION,
                        checkin_date=checkin,
                        checkout_date=checkout,
                        guest_count=guests,
                        force_fallback=active_fallback,
                    )
                    src = result.get("data_source", "unknown")
                    status = result.get("status", "?")
                    conf = result.get("confidence_score", 0)
                    median = result.get("competitor_median", 0)

                    logger.info(
                        f"  ✓ {label} → [{src.upper()}] "
                        f"status={status} conf={conf} median=€{median:.0f}"
                    )
                    successes += 1
                    if src == "live":
                        live_count += 1
                        consecutive_seed_count = 0
                    else:
                        seed_count += 1
                        consecutive_seed_count += 1
                        
                        # Early-stop / Safety block detection
                        if consecutive_seed_count >= 3 and not force_fallback_run:
                            logger.warning(
                                "  [SAFETY GUARDRAIL] 3 consecutive scans hit fallback/bot block. "
                                "Switching to synthetic fallback mode for the rest of the daily scan."
                            )
                            force_fallback_run = True

                except Exception as e:
                    logger.error(f"  ✗ {label} → FAILED: {e}")
                    failures += 1
                    consecutive_seed_count += 1
                    
                    # Pause slightly longer on error
                    time.sleep(15.0)

        # Cool down the network lease between weekly batches
        if chunk_idx < len(weekly_chunks) - 1:
            logger.info("Weekly batch complete. Cooling down connection for 60 seconds...")
            time.sleep(60.0)

    logger.info(
        f"Daily scan complete. "
        f"Successes={successes} (live={live_count}, seed={seed_count}) "
        f"Failures={failures}"
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Pricing Monitor daily scheduler")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one scan cycle immediately then exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be scraped without scraping",
    )
    args = parser.parse_args()

    dates = build_scan_dates()
    logger.info(f"Pricing Monitor Scheduler starting up.")
    logger.info(f"  Location:     {LOCATION}")
    logger.info(f"  Window:       {WINDOW_START} → {WINDOW_END}")
    logger.info(f"  Total dates:  {len(dates)} check-in dates")
    logger.info(f"  Guest counts: {GUEST_COUNTS}")
    logger.info(f"  Run time:     {DAILY_HOUR:02d}:{DAILY_MINUTE:02d} every Sunday (Weekly)")

    if args.once or args.dry_run:
        logger.info("Running single scan cycle now...")
        run_daily_scan(dry_run=args.dry_run)
        return

    # Blocking scheduler — fires at configured time once a week (Sunday)
    scheduler = BlockingScheduler(timezone="Europe/Dublin")
    scheduler.add_job(
        run_daily_scan,
        trigger=CronTrigger(day_of_week='sun', hour=DAILY_HOUR, minute=DAILY_MINUTE),
        id="weekly_pricing_scan",
        name="Weekly Booking.com pricing scan",
        misfire_grace_time=3600,   # allow up to 1hr late fire
        coalesce=True,             # if multiple misfires pile up, run once
    )

    logger.info(
        f"Scheduler ready. Next fire: "
        f"{scheduler.get_jobs()[0].next_run_time}"
    )
    logger.info("Press Ctrl+C to stop.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
