# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# =====================================================================
# DATA HYBRID LAYER POLICY & DEMO CONTINUITY:
# 1. Live Booking.com Scraper: Best-effort data collection path.
# 2. Seed/Synthetic Generator: Intentional fallback for live demo
#    continuity and scraper failure/blocking scenarios.
# 3. Labeling Requirement: All outputs, database records, API
#    responses, and UI elements must retain a clear data_source label
#    ('live' vs 'seed').
# =====================================================================

import os
import re
import time
import logging
import datetime
import numpy as np
import google.auth

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types
from google.adk.plugins.base_plugin import BasePlugin

from app.scraper import MarketDataCollector
from app.database import (
    save_scan_results,
    get_prior_run_memory,
    log_audit_step,
    get_historical_strategy_context,
)

# Setup logger
logger = logging.getLogger("agent")

# Set default GCP credentials for ADK Vertex client (DISABLED for local API Key usage)
# try:
#     _, project_id = google.auth.default()
#     os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
#     os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
#     os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
# except Exception as auth_err:
#     logger.warning(
#         f"Could not load Google Cloud credentials: {auth_err}. Local runner will require GOOGLE_API_KEY environment variable."
#     )

# Initialize the market data collector
collector = MarketDataCollector()


def run_market_scan_tool(
    location: str,
    checkin_date: str,
    checkout_date: str,
    guest_count: int,
    force_fallback: bool = False,
    too_low_pct: int = 10,
    too_high_pct: int = 50,
) -> dict:
    """Executes a complete pricing scan for a location, dates, and guest count.

    Performs the live/fallback market pricing scrape, fetches our own listing price,
    calculates competitor percentiles based on adjustable thresholds, retrieves prior scan memory,
    calculates a confidence score, updates the SQLite database, and returns the
    structured results.

    Args:
        location: Donegal location (e.g. 'Moville', 'Stroove', 'Greencastle', 'Culdaff')
        checkin_date: Start date YYYY-MM-DD
        checkout_date: End date YYYY-MM-DD (typically 2 nights after checkin_date)
        guest_count: Number of guests (2, 4, or 6)
        force_fallback: Force synthetic data generator
        too_low_pct: Bottom percentile alert threshold (default 10)
        too_high_pct: Top percentile alert threshold (default 50)

    Returns:
        A dictionary of pricing metrics, status, memory, and confidence parameters.
    """
    logger.info(
        f"Running deterministic market scan tool for {location} (Guests: {guest_count}) with thresholds (low: {too_low_pct}%, high: {too_high_pct}%)"
    )
    t_start = time.perf_counter()

    # 1. Retrieve persistent memory from prior runs (Instrumented)
    t_mem = time.perf_counter()
    prior_memory = get_prior_run_memory(checkin_date, guest_count)
    d_mem = int((time.perf_counter() - t_mem) * 1000)
    log_audit_step(
        None,
        "memory_retrieval",
        "sqlite",
        d_mem,
        "Retrieved prior scan baseline from memory.",
    )

    # 2. Collect competitor market listings (Instrumented)
    t_scrap = time.perf_counter()
    market_data = collector.collect_market_data(
        location=location,
        checkin_date=checkin_date,
        checkout_date=checkout_date,
        guest_count=guest_count,
        force_fallback=force_fallback,
    )
    competitors = market_data["listings"]
    data_source = market_data["data_source"]
    d_scrap = int((time.perf_counter() - t_scrap) * 1000)
    log_audit_step(
        None,
        "market_scraping",
        data_source,
        d_scrap,
        f"Collected {len(competitors)} competitor rates (Mode: {data_source.upper()}).",
    )

    # 3. Identify my own listing from scraped results (Instrumented)
    t_own = time.perf_counter()
    my_listing_found = 1
    my_listing_obj = None

    # Search for our specific listing in the collected dataset
    for comp in competitors:
        if comp.get("is_my_listing"):
            my_listing_obj = comp
            break

    if my_listing_obj:
        my_price = my_listing_obj["price"]
        # Remove our listing from the competitors list so it isn't analyzed as a competitor
        competitors = [c for c in competitors if not c.get("is_my_listing")]
        my_listing = {
            "name": "Harbour Bar Apartment",
            "price": my_price,
            "rating": my_listing_obj.get("rating", 9.2),
            "data_source": data_source,
            "my_listing_found": 1,
        }
    else:
        # Listing not found on Booking.com for this date (common in live runs if booked out)
        logger.warning(
            f"Harbour Bar Apartment listing not found on Booking.com for check-in: {checkin_date}. Using seasonal fallback pricing."
        )
        my_listing_found = 0
        my_price = collector.fetch_my_listing_price(
            checkin_date, checkout_date, guest_count
        )
        my_listing = {
            "name": "Harbour Bar Apartment (NOT FOUND)",
            "price": my_price,
            "rating": 9.2,
            "data_source": data_source,
            "my_listing_found": 0,
        }

    d_own = int((time.perf_counter() - t_own) * 1000)
    log_audit_step(
        None,
        "my_listing_lookup",
        data_source,
        d_own,
        f"Located own listing ({'FOUND' if my_listing_found else 'NOT FOUND'}). Price: €{my_price}.",
    )

    # 4. Calculate percentiles (math is done deterministically)
    comp_prices = sorted([c["price"] for c in competitors])
    if comp_prices:
        # "Top X%" means prices higher than (100-X)% of the
        # market, so we calculate the (100-X)th percentile.
        # e.g. "top 30%" -> 70th percentile
        p_low = float(np.percentile(comp_prices, too_low_pct))
        p_high = float(np.percentile(comp_prices, 100 - too_high_pct))
        p50 = float(np.percentile(comp_prices, 50))  # Preserved market median separately
    else:
        p_low = my_price * 0.9
        p_high = my_price * 1.1
        p50 = my_price

    # 5. Calculate confidence score (0-100)
    if data_source == "seed":
        confidence_score = 40
        confidence_reason = (
            "Using fallback synthetic market data due to Booking.com rate limits."
        )
    else:
        confidence_score = 80
        num_listings = len(competitors)
        # Add +5 points per competitor found (up to +15 max)
        confidence_score += min(15, num_listings * 5)
        # Add +5 bonus points if prior memory exists
        if prior_memory:
            confidence_score += 5
        confidence_score = min(100, confidence_score)
        confidence_reason = f"Using fresh, live Booking.com scraper data. Found {num_listings} comparable listings."

    # Calculate stay duration
    try:
        checkin_dt = datetime.datetime.strptime(checkin_date, "%Y-%m-%d")
        checkout_dt = datetime.datetime.strptime(checkout_date, "%Y-%m-%d")
        num_nights = max(1, (checkout_dt - checkin_dt).days)
    except Exception:
        num_nights = 2

    # 6. Determine pricing status (Alert rules calculated in deterministic Python layer)
    my_nightly = my_price / num_nights
    p_low_nightly = p_low / num_nights
    p_high_nightly = p_high / num_nights
    p50_nightly = p50 / num_nights

    if my_listing_found:
        if my_price <= p_low:
            status = "Too Low"
            recommendation = (
                f"Your total price for this {num_nights}-night stay is €{my_price:.2f} (nightly equivalent: €{my_nightly:.2f}), "
                f"which is underpriced compared to competitors. Recommend increasing your rate by €{round(p_low - my_price + 10, 2):.2f} "
                f"to match your custom bottom {too_low_pct}% threshold (€{p_low:.2f} total / €{p_low_nightly:.2f} nightly) of the market."
            )
        elif my_price >= p_high:
            status = "Too High"
            recommendation = (
                f"Your total price for this {num_nights}-night stay is €{my_price:.2f} (nightly equivalent: €{my_nightly:.2f}), "
                f"which is overpriced compared to competitors. Recommend lowering your rate by €{round(my_price - p_high + 5, 2):.2f} "
                f"to align closer to your custom top {too_high_pct}% threshold (€{p_high:.2f} total / €{p_high_nightly:.2f} nightly) of the market."
            )
        else:
            status = "Healthy"
            recommendation = (
                f"Your total price for this {num_nights}-night stay is €{my_price:.2f} (nightly equivalent: €{my_nightly:.2f}). "
                f"This rate is healthy and competitive with local market rates."
            )
    else:
        # Own listing not found → seasonal seed price used as fallback
        # Calculate status the same way as when found, so it's consistent with database.py recalculation
        if my_price <= p_low:
            status = "Too Low"
        elif my_price >= p_high:
            status = "Too High"
        else:
            status = "Healthy"
        recommendation = (
            f"Harbour Bar Apartment was not found in the search results for this {num_nights}-night stay. "
            f"Likely reason: The listing is already booked, blocked by host controls, or restricted for this specific date range. "
            f"Competitor data was successfully collected: Typical market price (median) is €{p50:.2f} total stay (nightly equivalent: €{p50_nightly:.2f}). "
            f"If your listing were active, our recommended baseline rate would be €{my_price:.2f} total (nightly equivalent: €{my_nightly:.2f})."
        )

    analysis = {
        "my_price": my_price,
        "competitor_median": p50,
        "competitor_10th": p_low, # Store p_low as competitor_10th in SQLite to preserve backward compatibility
        "status": status,
        "recommendation": recommendation,
        "confidence_score": confidence_score,
        "confidence_reason": confidence_reason,
        "my_listing_found": my_listing_found,
    }

    # 7. Save results to database and auto-export CSV (Instrumented)
    t_save = time.perf_counter()
    scan_id = save_scan_results(
        location=location,
        checkin_date=checkin_date,
        checkout_date=checkout_date,
        guest_count=guest_count,
        data_source=data_source,
        my_listing=my_listing,
        competitors=competitors,
        analysis=analysis,
    )
    d_save = int((time.perf_counter() - t_save) * 1000)
    log_audit_step(
        scan_id,
        "save_results",
        data_source,
        d_save,
        f"Scans and competitor data written to SQLite (Scan ID: {scan_id}).",
    )

    # 8. Log overall execution metrics (Instrumented)
    d_total = int((time.perf_counter() - t_start) * 1000)
    log_audit_step(
        scan_id,
        "scan_completed",
        data_source,
        d_total,
        f"Competitor pricing scan completed in {d_total}ms.",
    )

    return {
        "location": location,
        "checkin_date": checkin_date,
        "checkout_date": checkout_date,
        "guest_count": guest_count,
        "data_source": data_source,
        "my_price": my_price,
        "my_listing_found": my_listing_found,
        "competitor_median": p50,
        "competitor_10th": p_low, # Populating legacy key for backward compatibility
        "lower_threshold_price": p_low,
        "upper_threshold_price": p_high,
        "too_low_pct": too_low_pct,
        "too_high_pct": too_high_pct,
        "status": status,
        "recommendation": recommendation,
        "competitor_count": len(competitors),
        "confidence_score": confidence_score,
        "confidence_reason": confidence_reason,
        "prior_run_memory": prior_memory,
    }


def get_strategy_context_tool(question_type: str, guest_count: int) -> dict:
    """Retrieves business strategy history context from SQLite database.

    DELIBERATE DESIGN PRACTICE: Enables business strategy mode reasoning (Enhancement 7).

    Args:
        question_type: The type of strategy check: 'what_changed', 'occupancy_risk', or 'rate_opportunity'
        guest_count: Number of guests (2, 4, or 6)

    Returns:
        A dictionary with historical comparisons and variances.
    """
    logger.info(
        f"Running strategy context tool for type: {question_type} (Guests: {guest_count})"
    )
    return get_historical_strategy_context(question_type, guest_count)


class PIIAndScopeGuardrailPlugin(BasePlugin):
    """Custom ADK plugin for input validation and output PII protection.

    Prevents leak of owner details (phone, email, bank details) and restricts location
    scoping strictly to Co. Donegal, Ireland.
    """

    def __init__(self):
        super().__init__(name="pii_and_scope_guardrail")

    async def before_model_callback(self, *, callback_context, llm_request):
        # 1. Parse prompt text
        prompt = ""
        for content in llm_request.contents:
            if hasattr(content, "parts"):
                for part in content.parts:
                    if hasattr(part, "text") and part.text:
                        prompt += part.text

        # 2. Block locations outside our Donegal domain
        disallowed = ["dublin", "galway", "cork", "belfast", "london", "paris"]
        for city in disallowed:
            if city in prompt.lower():
                logger.warning(
                    f"Guardrail triggered: Blocked request referencing '{city}'"
                )
                from google.adk.models.llm_response import LlmResponse

                return LlmResponse(
                    content=types.Content(
                        parts=[
                            types.Part(
                                text="Guardrail Alert: This assistant is strictly scoped to Booking.com pricing monitoring for Co. Donegal properties (Moville, Stroove, Greencastle, Culdaff). Queries regarding other regions are disallowed."
                            )
                        ]
                    )
                )
        return None

    async def after_model_callback(self, *, callback_context, llm_response):
        # Sanitize model output to protect PII
        if not llm_response.content or not llm_response.content.parts:
            return None

        for part in llm_response.content.parts:
            if hasattr(part, "text") and part.text:
                text = part.text
                # Redact phone numbers
                text = re.sub(
                    r"\+?\d{3}[-\s]?\d{3,4}[-\s]?\d{4}", "[REDACTED PHONE NUMBER]", text
                )
                # Redact email addresses
                text = re.sub(r"[\w\.-]+@[\w\.-]+\.\w+", "[REDACTED EMAIL]", text)
                # Redact bank details (IBAN/BIC)
                text = re.sub(
                    r"[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7,20}", "[REDACTED BANK DETAILS]", text
                )
                part.text = text

        return None


# ==========================================
# Agent Hierarchy Definitions
# ==========================================

# 1. Pricing Analyst Agent (Consumes persistent memory and outputs detailed structured explanations)
pricing_analyst_agent = Agent(
    name="pricing_analyst_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction="""You are a competitive pricing analyst for self-catering apartments in Co. Donegal.
Your role is to run a pricing scan using 'run_market_scan_tool' to get the market data.
Based on the results, formulate a logical pricing strategy. Review the competitor counts, typical market price (median), custom alert thresholds, and my price.

Note that scans are based on a 2-night stay by default. All competitive pricing analysis and recommendations must mention both the total stay rate and the nightly equivalent rate.

If my own listing 'Harbour Bar Apartment' is not found in the search results, this is a normal business scenario (the property is likely booked out, blocked by host controls, or restricted for this period). It is NOT a scraping failure. You must explain to the user that competitor rates were successfully collected but their own listing is likely unavailable/booked, and present recommended pricing boundaries for reference.

In your output, you MUST return a structured response with two distinct sections:
### **Pricing Analysis Recommendation**
[A concise summary of the pricing status (Too Low, Too High, or Healthy) and the recommended price change]

### **Structured Analyst Explanation**
A detailed explanation that MUST explicitly cover:
1. The competitor typical market price (median) and the custom lower (bottom X%) and upper (top Y%) thresholds for this date and guest count. Reference the custom thresholds used in that run.
2. My apartment's position relative to the custom thresholds.
3. The guest count expressed in plain English words (e.g. 'four guests', 'two guests', 'six guests').
4. Whether the target date is a weekday or weekend.
5. The data source using the explicit 'live' or 'seed' label (e.g. "Data source is seed").
6. The confidence score (0-100) and the reason explaining it.
7. ANY notable changes compared to the prior run memory if provided (e.g., if my price, the market median, status, or data source has changed since the previous scan timestamp).

If the query asks about broad business strategy changes, risks, or rates opportunities (e.g. 'What changed since the last run?', 'What are my occupancy risks?', 'What are my rate opportunities?'), call 'get_strategy_context_tool' to retrieve context and summarize it clearly for the owner. Assume guest_count is 4 if the user does not specify.

Do not invent data; only analyze what the tool returns.""",
    tools=[run_market_scan_tool, get_strategy_context_tool],
    description="Analyzes competitor pricing data, runs market scans, and provides price strategy recommendations.",
)

# 2. Notification Agent
notification_agent = Agent(
    name="notification_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction="""You are an automated alert notification manager.
Analyze the pricing status provided (Too Low, Too High, or Healthy).
If the status is 'Too Low' or 'Too High', format a concise alert.
Include the date, current price, competitor typical market price (median) and active thresholds, and the direct recommended action.
If the status is 'Healthy', output: 'Pricing is healthy for this date.'""",
    description="Generates alert notifications and formatting payloads for pricing alerts.",
)

# 3. Coordinator Agent (Root)
coordinator_agent = Agent(
    name="coordinator_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction="""You are the Booking Pricing Coordinator for a self-catering apartment in Donegal.
Your job is to manage market pricing queries.
- For running scans, pricing analysis, or checking strategy and comparative changes, delegate to 'pricing_analyst_agent'.
- For alerts, drafting notification payloads, or alert formatting, delegate to 'notification_agent'.
Always be professional and focused on Donegal properties. Keep your response brief.""",
    sub_agents=[pricing_analyst_agent, notification_agent],
)

# Create the ADK App instance, registering the root coordinator and safety plugin
app = App(
    root_agent=coordinator_agent, name="app", plugins=[PIIAndScopeGuardrailPlugin()]
)

# Export root_agent alias for integration test modules
root_agent = coordinator_agent
