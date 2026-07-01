# Skill: Dashboard Narrative & Analytics Integration

This skill encodes the operational logic for synchronizing agent pricing runs with the database ledger, exporting structured analytics, and recording execution telemetry.

## Agent Responsibility

This skill enables the Pricing Analyst Agent to persist analytical results and expose them consistently to the presentation and business intelligence layers.

Its responsibilities are to:
- Enforce data contract formatting for downstream API consumption
- Synchronize SQLite transaction records with the flat-file CSV export
- Record granular, step-by-step performance telemetry to the database audit log
- Validate security headers on inbound requests

## Operational Trigger Flow

The agent executes this integration skill at key phases of the scan cycle:

1. **State Initialization**: Validates API request headers and establishes SQLite session connection.
2. **Post-Scraper Auditing**: Commits data source metadata (`live` vs `seed`) and logs latency.
3. **Post-Analysis Commitment**: Writes calculated percentiles, statuses, and explanations to the database.
4. **BI Ledger Sync**: Triggers the CSV serialization to ensure external dashboards reflect the latest scan.
5. **Telemetry Trace**: Appends final execution durations to the database run log.

## Output Synchronization Contract

After completing a market analysis run, the integration layer must synchronize the SQLite database and the flat-file CSV export.
- **CSV Output File**: `data/pricing_history.csv` must be overwritten on each successful scan.
- **Relational Mapping**: The CSV export must flatten database relationships (joining `market_scans` and `pricing_analysis`) into a single record row.
- **Field Integrity**: The flat record must include the checkin date, checkout date, guest count, local rate, competitor median, status category, and explicit data source metadata.

## Telemetry & Audit Logging Contract

To support runtime observability, each step of the analysis pipeline must record an audit entry containing:
- Step name and execution duration (ms)
- Data source category (`live` vs `seed`)
- Specific execution details (e.g. listings found, fallback reason, memory match status)

## Evaluation Support

This integration skill is verified using automated test suits checking:
- API payload schema validation compliance.
- Database-to-CSV synchronization latency and structural match integrity.
- Correct API key authentication verification.
