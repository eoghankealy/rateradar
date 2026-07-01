# Skill: Booking.com Market Collection

This skill encodes the operational knowledge required to collect, sanitize, and manage competitor apartment pricing data in Co. Donegal.

## Agent Responsibility

This skill enables the Pricing Analyst Agent to acquire structured market data before pricing analysis.

Its responsibilities are to:
- Collect competitor listings
- Normalize market data
- Identify the data source
- Sanitize external content
- Produce a structured dataset for downstream analysis
- Preserve provenance and trust metadata

## Hybrid Data Collection Strategy

Data collection is implemented as a hybrid, rate-limited collection system designed to handle the high volatility of scraping public web sources and defend against IP blocks:
1. **Live Booking.com Scraper (Best-Effort)**: The primary path. It utilizes Playwright (with a requests + BeautifulSoup4 fallback) to scrape live Booking.com listing cards. The scraper queries target destination IDs and applies URL filter parameters (`nflt`) to isolate relevant property types: *Apartments, Holiday homes, and Entire homes*. Live result parsing is intentionally treated as best-effort because third-party websites can change their markup, availability logic, or bot protection mechanisms over time.
2. **Weekly Phased Scheduler & Rate-Limiting Guardrails**:
   *   *Weekly Batching*: The weekly scan scheduler chunks the 3-month scanning window into weekly blocks rather than executing all dates in one aggressive session.
   *   *Randomized Delays*: Introduces a random delay of 5 to 10 seconds between individual date scans to match human browsing profiles.
   *   *Batch Cool-downs*: Pauses for a longer cool-down period of 60 seconds between weekly blocks to clear the travel portal's IP tracking filters.
   *   *Early-Stop Safety Override*: If 3 consecutive scans return seed fallback data, the system automatically overrides the scraper and sets `force_fallback=True` for the remainder of the weekly scan to protect system resources and avoid spamming.
3. **Synthetic Fallback Generator**: The secondary path ensuring demo continuity. If the live scraper fails (due to bot-detection blocks, network timeouts, or rate limits), the system automatically falls back to a deterministic synthetic market generator. It produces realistic local properties based on historical Donegal pricing baselines.

Both paths enforce a strict database schema. Every database record, API response, and dashboard element should retain a clear `data_source` label (`live` vs `seed`) for complete transparency.

## Pricing Semantics and Booking Window

To ensure pricing trust and clear comparison, the collector adheres to the following rules:
- **Stay Duration**: All scans default to a **2-night minimum stay** (matching the host's own property constraints).
- **Rate Definitions**: The database stores both the total stay rate and the nightly-equivalent rate (total divided by 2). Comparison calculations should use nightly-equivalent rates to normalize any minor stay variances.
- **Listing Availability**: If the host's own listing ("Harbour Bar Apartment") is not returned by the live scraper, it is treated as a normal booking state rather than a scraper failure. The scraper still persists the competitor dataset, and the analyst agent provides pricing boundaries based on the surrounding market.

## Security & Sanitization Guardrails

Web scraped content is treated as untrusted external data. Relying on simple keyword blocklists (like filtering "ignore" or "override") is insufficient to prevent prompt injection. The system implements a defense-in-depth architecture:
- **Parsing and Casting**: Scraped prices, titles, and details are validated and cast to structured models using Pydantic before database insertion or LLM exposure.
- **Structural Text Isolation**: Raw text from scraped listings is isolated within structured data blocks (XML/JSON tags) inside the prompt context, and the analyst LLM's system instructions explicitly warn against treating values inside these blocks as operational instructions.
- **Length Constraints**: Scraped text fields are truncated to maximum length limits to prevent payload-stuffing attacks.

## Historical Context

After collection completes, historical market summaries are retrieved from the memory layer to compare today's market against previous scans.

## Telemetry

Each collection run records:
- Scan start/end timestamps and collection duration
- Scraper success/failure and fallback activation status
- Comparable listing count and data source origin

## Data Source Trust Signaling

To convey data trust to the user, the dashboard highlights the data origin:
- **Live Data**: Active when a scan successfully returns live scraped listings. The system stamps the record with a `live` label, and the UI displays a green `✓ LIVE DATA` badge indicating high trustworthiness.
- **Synthetic Fallback**: Active when live scraping is blocked or unavailable. The system stamps the record with a `seed` label, and the UI displays an amber/red `⚠ SYNTHETIC FALLBACK` warning to indicate that rates are estimated from seasonal baselines and should be treated with caution.

## Design Rationale

The market collection capability is intentionally isolated from pricing analysis. This separation ensures that future data sources (e.g. Airbnb, Vrbo) can be introduced without changing the Pricing Analyst Agent's reasoning logic, allowing downstream components to consume market data consistently regardless of its origin.

## Verification & Live Scraper Ingestion

To test the collection module independently:

```bash
uv run python -c "
from scraper import MarketDataCollector
c = MarketDataCollector()
res = c.collect_market_data(location='Greencastle', checkin_date='2026-07-15', checkout_date='2026-07-17', guest_count=4)
print(f'Data Source: {res[\"data_source\"]}')
print(f'Listings Count: {len(res[\"listings\"])}')
"
```
