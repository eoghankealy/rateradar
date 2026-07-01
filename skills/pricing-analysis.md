# Skill: Pricing Recommendation & Market Analysis

This skill encodes the operational logic for performing market price analysis, applying mathematical alert thresholds, and generating structured strategic pricing recommendations.

## Agent Responsibility

This skill enables the Pricing Analyst Agent to evaluate the host's current rate against competitor market data, identify optimization opportunities, and output strategic recommendations. The Pricing Analyst Agent combines deterministic market analytics with LLM reasoning: all numerical calculations are performed programmatically before being passed to the LLM, ensuring recommendations remain grounded in verifiable market statistics. The LLM is responsible for interpretation and communication, not numerical decision-making.

Its responsibilities are to:
- Calculate market percentiles (10th percentile and 50th/median percentile)
- Determine pricing health categories using deterministic business rules based on market percentiles
- Retrieve and compare historical trends from the database memory layer
- Generate structured, explainable pricing reasoning for the host
- Delegate alert formatting actions to downstream agents

## Configurable Pricing Alert Rules

To balance occupancy and daily rates, the system evaluates the user's listing price against competitor rates using configurable alert thresholds, supporting different positioning strategies:

1. **Underpricing Alert (Bottom X%)**:
   - **Condition**: Our listing price $\le$ bottom $X\%$ percentile of competitors (default 10%).
   - **Risk**: Leaving money on the table (high occupancy but low revenue).
   - **Action**: Alert the user and recommend an increase to match the lower threshold.

2. **Overpricing Alert (Top Y% / Upper Tail)**:
   - **Condition**: Our listing price $\ge$ top $Y\%$ threshold (calculated as the $(100 - Y)\text{th}$ percentile, default 50%).
   - **Risk**: Low occupancy and high vacancy (priced too high for the area).
   - **Action**: Alert the user and recommend reducing the rate to match the overpricing limit.

3. **Healthy Pricing**:
   - **Condition**: Price is between the underpricing and overpricing thresholds.
   - **Status**: The competitive "sweet spot" for Donegal listings. No action required.

*Note: Percentile mathematics are calculated programmatically in Python before being passed to the LLM agent, ensuring status classification remains strictly deterministic.*

## Persistent Memory Ledger

Before performing the analysis, the `run_market_scan_tool` checks the SQLite database for a prior run matching the target check-in date and guest count. If found, the previous market median, pricing status, recommendation, and data source are injected into the analyst agent's prompt context to enable explainable trend analysis.

## Strategic Advisory

Beyond producing nightly price recommendations, this skill supports higher-level business strategy:
- Weekday versus weekend pricing adjustments.
- Highlighting occupancy-focused strategies vs rate-maximization strategies.
- Identifying and alerting on periods of elevated pricing risk.
- Analyzing changes since the previous scan.

The same analytical workflow is reused so strategic recommendations remain grounded in the same market data and business rules.

## Output & Telemetry Contract

### Structured Analyst Explanations
Explanations must be generated as structured explanation output detailing:
- **Pricing Status Header**: Highlight status (`Too Low`, `Too High`, `Healthy`).
- **Data Source Indicator**: Explicitly state if based on `✓ LIVE DATA` or `⚠ SYNTHETIC FALLBACK` data.
- **Weekday/Weekend Context**: Indicate weekday vs weekend adjustments.
- **Plain-English Numbers**: Express guest capacities as text (e.g., "four guests" instead of "4").
- **Memory Comparison**: State the price change relative to the prior run if memory is available.

### Telemetry Events
Each analysis run persists a database audit record recording:
- Target check-in date, guest count, and execution latency (ms)
- Output status, price deviation, and data source (`live` vs `seed`)
- Memory retrieval indicator (yes/no) and generated action plan

## Multi-Agent Execution Path

- **Coordinator Agent (Root)**: Entrypoint. Directs pricing query intent. Delegates scanning and analysis to the analyst agent.
- **Pricing Analyst Agent**: Invokes raw price collection, evaluates alert rules, reads memory context, and writes the reasoning explanation.
- **Notification Agent**: Evaluates status output. For non-healthy states (`Too Low`, `Too High`), formats structured alert notification payloads.

## Design Rationale

The analysis engine is built as a rule-guided agent rather than a black-box LLM predictor. This separation of concerns ensures that alert formatting and coordination are decoupled from the analytical math, making the system easily extendable with new notification channels without changing the core reasoning.

## Evaluation Support

This skill is evaluated using deterministic tests and LLM-as-judge evaluations:
- Verification of percentile math and recommendation boundaries.
- Quality auditing of explanation readability and memory usage.
- Business rule compliance.
- Verification of live vs seed data handling and prompt-injection resilience.
