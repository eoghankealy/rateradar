import asyncio
import datetime

from app.agent import run_market_scan_tool


async def main():
    print("Initializing Database and pre-populating with SEED fallback data...")

    # We will generate scans for check-in dates spanning next month
    base_date = datetime.date(2026, 7, 10)
    dates = [
        (base_date + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(0, 25, 5)
    ]
    guest_capacities = [2, 4, 6]

    for checkin in dates:
        # Checkout is checkin + 2 days
        checkin_dt = datetime.datetime.strptime(checkin, "%Y-%m-%d")
        checkout = (checkin_dt + datetime.timedelta(days=2)).strftime("%Y-%m-%d")

        for guests in guest_capacities:
            print(
                f" seeding scan for Location: Greencastle, Date: {checkin} to {checkout}, Guests: {guests}..."
            )
            try:
                # We execute the scan tool directly to trigger fallback math and database persistence
                result = run_market_scan_tool(
                    location="Greencastle",
                    checkin_date=checkin,
                    checkout_date=checkout,
                    guest_count=guests,
                    force_fallback=True,
                )
                print(
                    f"   Success. Source: {result['data_source']}, Median: €{result['competitor_median']:.2f}"
                )
            except Exception as err:
                print(f"   Failed to seed scan: {err}")

    print("Database seeding completed successfully.")


if __name__ == "__main__":
    asyncio.run(main())
