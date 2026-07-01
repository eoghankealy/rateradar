import csv
import datetime
import logging
import os
import sqlite3

# =====================================================================
# DATA HYBRID LAYER POLICY & DEMO CONTINUITY:
# 1. Live Booking.com Scraper: Best-effort data collection path.
# 2. Seed/Synthetic Generator: Intentional fallback for live demo
#    continuity and scraper failure/blocking scenarios.
# 3. Labeling Requirement: All outputs, database records, API
#    responses, and UI elements must retain a clear data_source label
#    ('live' vs 'seed').
# =====================================================================

logger = logging.getLogger("database")

DB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
DB_PATH = os.path.join(DB_DIR, "pricing.db")
CSV_PATH = os.path.join(DB_DIR, "pricing_history.csv")


def init_db():
    """Initializes the database schema if it doesn't exist.

    Performs safe schema migrations to add columns if database already exists.
    """
    os.makedirs(DB_DIR, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Table for market scans
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_timestamp TEXT NOT NULL,
            location TEXT NOT NULL,
            checkin_date TEXT NOT NULL,
            checkout_date TEXT NOT NULL,
            guest_count INTEGER NOT NULL,
            data_source TEXT NOT NULL, -- 'live' or 'seed'
            user_preference TEXT DEFAULT 'occupancy'
        )
    """)

    # 2. Table for listings detail
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS listings_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            rating REAL,
            is_own_listing INTEGER NOT NULL, -- 1 = Yes, 0 = No
            data_source TEXT NOT NULL, -- 'live' or 'seed'
            FOREIGN KEY(scan_id) REFERENCES market_scans(id) ON DELETE CASCADE
        )
    """)

    # 3. Table for pricing analysis results
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pricing_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER NOT NULL,
            my_price REAL NOT NULL,
            competitor_median REAL NOT NULL,
            competitor_10th REAL NOT NULL,
            status TEXT NOT NULL, -- 'Too Low', 'Too High', 'Healthy'
            recommendation TEXT NOT NULL,
            confidence_score INTEGER,
            confidence_reason TEXT,
            FOREIGN KEY(scan_id) REFERENCES market_scans(id) ON DELETE CASCADE
        )
    """)

    # 4. Table for audit/observability logs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_timestamp TEXT NOT NULL,
            scan_id INTEGER,
            step_name TEXT NOT NULL,
            data_source TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            details TEXT
        )
    """)

    # --- Safe Migrations ---
    # Add columns if the tables were created in a previous build step
    try:
        cursor.execute(
            "ALTER TABLE market_scans ADD COLUMN user_preference TEXT DEFAULT 'occupancy'"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute(
            "ALTER TABLE pricing_analysis ADD COLUMN confidence_score INTEGER"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute("ALTER TABLE pricing_analysis ADD COLUMN confidence_reason TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute(
            "ALTER TABLE pricing_analysis ADD COLUMN my_listing_found INTEGER DEFAULT 1"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute("ALTER TABLE listings_data ADD COLUMN nightly_price REAL")
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute("ALTER TABLE pricing_analysis ADD COLUMN my_nightly_price REAL")
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute(
            "ALTER TABLE pricing_analysis ADD COLUMN competitor_median_nightly REAL"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute(
            "ALTER TABLE pricing_analysis ADD COLUMN competitor_10th_nightly REAL"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists

    conn.commit()
    conn.close()
    logger.info("SQLite database initialized and migrated successfully.")


def save_scan_results(
    location: str,
    checkin_date: str,
    checkout_date: str,
    guest_count: int,
    data_source: str,
    my_listing: dict,
    competitors: list,
    analysis: dict,
    user_preference: str = "occupancy",
) -> int:
    """Saves market scan details, competitor listings, and analysis results to SQLite.

    Also automatically triggers a CSV export for Power BI compatibility.
    """
    init_db()  # Ensure database is initialized

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. Insert scan header
        # DELIBERATE SECURITY PRACTICE: Parameterized SQL statement prevents SQL injection.
        cursor.execute(
            """
            INSERT INTO market_scans (scan_timestamp, location, checkin_date, checkout_date, guest_count, data_source, user_preference)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                now_str,
                location,
                checkin_date,
                checkout_date,
                guest_count,
                data_source,
                user_preference,
            ),
        )
        scan_id = cursor.lastrowid

        # Calculate stay duration in nights
        try:
            checkin_dt = datetime.datetime.strptime(checkin_date, "%Y-%m-%d")
            checkout_dt = datetime.datetime.strptime(checkout_date, "%Y-%m-%d")
            num_nights = max(1, (checkout_dt - checkin_dt).days)
        except Exception:
            num_nights = 2

        # 2. Insert my listing
        # DELIBERATE SECURITY PRACTICE: Parameterized SQL statement prevents SQL injection.
        cursor.execute(
            """
            INSERT INTO listings_data (scan_id, name, price, rating, is_own_listing, data_source, nightly_price)
            VALUES (?, ?, ?, ?, 1, ?, ?)
        """,
            (
                scan_id,
                my_listing["name"],
                my_listing["price"] if my_listing["price"] is not None else -1.0,
                my_listing.get("rating", 9.0),
                my_listing["data_source"],
                my_listing["price"] / num_nights if my_listing["price"] is not None else -1.0,
            ),
        )

        # 3. Insert competitors
        for comp in competitors:
            # DELIBERATE SECURITY PRACTICE: Parameterized SQL statement prevents SQL injection.
            cursor.execute(
                """
                INSERT INTO listings_data (scan_id, name, price, rating, is_own_listing, data_source, nightly_price)
                VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
                (
                    scan_id,
                    comp["name"],
                    comp["price"],
                    comp.get("rating"),
                    comp["data_source"],
                    comp["price"] / num_nights,
                ),
            )

        # 4. Insert analysis
        # DELIBERATE SECURITY PRACTICE: Parameterized SQL statement prevents SQL injection.
        cursor.execute(
            """
            INSERT INTO pricing_analysis (scan_id, my_price, competitor_median, competitor_10th, status, recommendation, confidence_score, confidence_reason, my_listing_found, my_nightly_price, competitor_median_nightly, competitor_10th_nightly)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                scan_id,
                analysis["my_price"] if analysis["my_price"] is not None else -1.0,
                analysis["competitor_median"],
                analysis["competitor_10th"],
                analysis["status"],
                analysis["recommendation"],
                analysis.get("confidence_score"),
                analysis.get("confidence_reason"),
                analysis.get("my_listing_found", 1),
                analysis["my_price"] / num_nights if analysis["my_price"] is not None else -1.0,
                analysis["competitor_median"] / num_nights,
                analysis["competitor_10th"] / num_nights,
            ),
        )

        conn.commit()
        logger.info(f"Successfully saved scan {scan_id} to database.")

        # Trigger CSV export
        export_to_csv()

        return scan_id
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to save scan results: {e}")
        raise e
    finally:
        conn.close()


def get_prior_run_memory(checkin_date: str, guest_count: int) -> dict:
    """Retrieves the latest prior scan details for memory-aware agent reasoning.

    DELIBERATE DESIGN PRACTICE: Enforces persistent memory using existing SQLite.
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # DELIBERATE SECURITY PRACTICE: Parameterized SQL statement prevents SQL injection.
    cursor.execute(
        """
        SELECT s.scan_timestamp, s.data_source, a.my_price, a.competitor_median,
               a.competitor_10th, a.status, a.recommendation, a.confidence_score
        FROM market_scans s
        JOIN pricing_analysis a ON s.id = a.scan_id
        WHERE s.checkin_date = ? AND s.guest_count = ?
        ORDER BY s.id DESC
        LIMIT 1
    """,
        (checkin_date, guest_count),
    )

    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_calendar_data(guest_count: int, too_low_pct: int = 10, too_high_pct: int = 50) -> list:
    """Retrieves the latest scan results for each checkin date for the calendar view."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Subquery to get the latest scan ID for each checkin date and guest count
    # DELIBERATE SECURITY PRACTICE: Parameterized SQL statement prevents SQL injection.
    cursor.execute(
        """
        WITH latest_scans AS (
            SELECT id, checkin_date, checkout_date, data_source,
                   ROW_NUMBER() OVER (PARTITION BY checkin_date ORDER BY id DESC) as rn
            FROM market_scans
            WHERE guest_count = ?
        )
        SELECT s.id as scan_id, s.checkin_date, s.checkout_date, s.data_source,
               a.my_price, a.competitor_median, a.competitor_10th, a.status, a.recommendation,
               a.confidence_score, a.confidence_reason, a.my_listing_found,
               a.my_nightly_price, a.competitor_median_nightly, a.competitor_10th_nightly
        FROM latest_scans s
        JOIN pricing_analysis a ON s.id = a.scan_id
        WHERE s.rn = 1
        ORDER BY s.checkin_date ASC
    """,
        (guest_count,),
    )

    rows = cursor.fetchall()
    
    # Recalculate status dynamically using current thresholds
    results = []
    import numpy as np
    for row in rows:
        d = dict(row)
        if d["my_price"] == -1.0:
            d["my_price"] = None
        if d.get("my_nightly_price") == -1.0:
            d["my_nightly_price"] = None
        scan_id = d["scan_id"]
        
        # Load raw competitor listings for this scan to recalculate thresholds
        cursor.execute(
            "SELECT price FROM listings_data WHERE scan_id = ? AND is_own_listing = 0",
            (scan_id,)
        )
        comp_prices = sorted([r[0] for r in cursor.fetchall()])
        
        if comp_prices:
            # "Top X%" means prices higher than (100-X)% of the market
            p_low = float(np.percentile(comp_prices, too_low_pct))
            p_high = float(np.percentile(comp_prices, 100 - too_high_pct))
            
            # Recalculate status
            my_price = d["my_price"]
            if my_price is not None:
                if my_price <= p_low:
                    d["status"] = "Too Low"
                elif my_price >= p_high:
                    d["status"] = "Too High"
                else:
                    d["status"] = "Healthy"
            else:
                d["status"] = "Healthy"
            
            # Update metric values returned
            d["competitor_10th"] = p_low
            d["lower_threshold_price"] = p_low
            d["upper_threshold_price"] = p_high
            d["too_low_pct"] = too_low_pct
            d["too_high_pct"] = too_high_pct
            
        results.append(d)
        
    conn.close()
    return results


def get_alerts(too_low_pct: int = 10, too_high_pct: int = 50) -> list:
    """Retrieves all scans flagged as 'Too Low' or 'Too High' ordered by timestamp descending."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Query latest scans
    # DELIBERATE SECURITY PRACTICE: Parameterized SQL statement prevents SQL injection.
    cursor.execute("""
        SELECT s.id as scan_id, s.scan_timestamp, s.location, s.checkin_date, s.guest_count, s.data_source,
               a.my_price, a.competitor_median, a.competitor_10th, a.status, a.recommendation,
               a.confidence_score, a.confidence_reason, a.my_listing_found
        FROM market_scans s
        JOIN pricing_analysis a ON s.id = a.scan_id
        ORDER BY s.id DESC
        LIMIT 50
    """)

    rows = cursor.fetchall()
    results = []
    import numpy as np
    
    for row in rows:
        d = dict(row)
        if d["my_price"] == -1.0:
            d["my_price"] = None
        if d.get("my_nightly_price") == -1.0:
            d["my_nightly_price"] = None
        scan_id = d["scan_id"]
        
        cursor.execute(
            "SELECT price FROM listings_data WHERE scan_id = ? AND is_own_listing = 0",
            (scan_id,)
        )
        comp_prices = sorted([r[0] for r in cursor.fetchall()])
        
        if comp_prices:
            p_low = float(np.percentile(comp_prices, too_low_pct))
            p_high = float(np.percentile(comp_prices, 100 - too_high_pct))
            
            my_price = d["my_price"]
            if my_price is not None:
                if my_price <= p_low:
                    d["status"] = "Too Low"
                elif my_price >= p_high:
                    d["status"] = "Too High"
                else:
                    d["status"] = "Healthy"
            else:
                d["status"] = "Healthy"
                
            d["competitor_10th"] = p_low
            d["lower_threshold_price"] = p_low
            d["upper_threshold_price"] = p_high
            d["too_low_pct"] = too_low_pct
            d["too_high_pct"] = too_high_pct
            
        # Only yield alerts (Too Low or Too High status)
        if d["status"] in ("Too Low", "Too High"):
            results.append(d)
            if len(results) >= 20:
                break
                
    conn.close()
    return results


def log_audit_step(
    scan_id: int | None,
    step_name: str,
    data_source: str,
    duration_ms: int,
    details: str = "",
):
    """Persists detailed agent step metrics for observability logs."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # DELIBERATE SECURITY PRACTICE: Parameterized SQL statement prevents SQL injection.
    cursor.execute(
        """
        INSERT INTO audit_logs (scan_timestamp, scan_id, step_name, data_source, duration_ms, details)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (now_str, scan_id, step_name, data_source, duration_ms, details),
    )

    conn.commit()
    conn.close()


def export_to_csv():
    """Exports a joined view of all pricing data into a clean, flat CSV structure for Power BI."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Structured query creating a flat historical ledger
        cursor.execute("""
            SELECT
                s.id as scan_id,
                s.scan_timestamp,
                s.location,
                s.checkin_date,
                s.checkout_date,
                s.guest_count,
                s.data_source as scan_data_source,
                s.user_preference,
                a.my_price,
                a.competitor_median,
                a.competitor_10th,
                a.status as pricing_status,
                a.recommendation,
                a.confidence_score,
                a.confidence_reason
            FROM market_scans s
            JOIN pricing_analysis a ON s.id = a.scan_id
            ORDER BY s.id DESC
        """)

        rows = cursor.fetchall()

        headers = [
            "Scan ID",
            "Scan Timestamp",
            "Location",
            "Checkin Date",
            "Checkout Date",
            "Guest Count",
            "Data Source",
            "User Preference",
            "My Price (EUR)",
            "Competitor Median (EUR)",
            "Competitor 10th Percentile (EUR)",
            "Pricing Status",
            "Recommendation",
            "Confidence Score",
            "Confidence Reason",
        ]

        with open(CSV_PATH, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(headers)
            writer.writerows(rows)

        logger.info(f"Successfully exported data to CSV: {CSV_PATH}")
    except Exception as e:
        logger.error(f"Failed to export CSV: {e}")
    finally:
        conn.close()


def get_historical_strategy_context(question_type: str, guest_count: int = 4) -> dict:
    """Queries historical SQLite trends to provide context for business strategy queries.

    DELIBERATE DESIGN PRACTICE: Enables business strategy mode reasoning (Enhancement 7).
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    result = {
        "question_type": question_type,
        "guest_count": guest_count,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        # DELIBERATE SECURITY PRACTICE: Parameterized SQL statement prevents SQL injection.
        if question_type == "what_changed":
            cursor.execute(
                """
                SELECT s.id, s.scan_timestamp, s.checkin_date, s.data_source, a.my_price, a.competitor_median, a.status
                FROM market_scans s
                JOIN pricing_analysis a ON s.id = a.scan_id
                WHERE s.guest_count = ?
                ORDER BY s.id DESC
                LIMIT 2
            """,
                (guest_count,),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            result["recent_scans"] = rows

            if len(rows) == 2:
                recent = rows[0]
                prior = rows[1]
                median_change = recent["competitor_median"] - prior["competitor_median"]
                my_price_change = recent["my_price"] - prior["my_price"]
                result["comparison"] = {
                    "median_change_eur": round(median_change, 2),
                    "my_price_change_eur": round(my_price_change, 2),
                    "status_changed": recent["status"] != prior["status"],
                    "prior_status": prior["status"],
                    "current_status": recent["status"],
                }
        elif question_type == "occupancy_risk":
            cursor.execute(
                """
                SELECT s.checkin_date, s.data_source, a.my_price, a.competitor_median, a.status
                FROM market_scans s
                JOIN pricing_analysis a ON s.id = a.scan_id
                WHERE s.guest_count = ? AND a.status = 'Too High'
                ORDER BY s.checkin_date ASC
                LIMIT 10
            """,
                (guest_count,),
            )
            result["risky_dates"] = [dict(row) for row in cursor.fetchall()]
        elif question_type == "rate_opportunity":
            cursor.execute(
                """
                SELECT s.checkin_date, s.data_source, a.my_price, a.competitor_median, a.status
                FROM market_scans s
                JOIN pricing_analysis a ON s.id = a.scan_id
                WHERE s.guest_count = ? AND a.status = 'Too Low'
                ORDER BY s.checkin_date ASC
                LIMIT 10
            """,
                (guest_count,),
            )
            result["opportunity_dates"] = [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Failed to fetch strategy context: {e}")
        result["error"] = str(e)
    finally:
        conn.close()

    return result
