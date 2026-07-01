# Skill: Capstone Presentation & Demo Guide (Final Cut)

This skill encodes the highly-polished 5-minute script and walkthrough required for the live capstone presentation video.

## The RateRadar 5-Minute Pitch

### Act 1: The Pitch & Architecture (1 minute)
*   **Screen:** Show `presentation_slides.html` (Full screen in your browser).
*   **Action:** Start on the Title Slide.
*   **Talk Track:** Introduce yourself and **RateRadar**. Explain the core problem: pricing self-catering apartments in Donegal is a manual guessing game. 
*   **Action:** Switch to Slide 2 (Architecture).
*   **Talk Track:** Briefly explain that RateRadar is a multi-agent system built natively on the **Google ADK**. Highlight the SQLite Ledger that acts as the agent's long-term memory. Mention clearly: *"Before finalizing this architecture, the agent was rigorously tested against a 9-question Golden Dataset using the ADK Evaluation framework to guarantee its pricing logic is mathematically sound."*

---

### Act 2: The Dashboard Walkthrough (3 minutes)
*   **Screen:** Switch over to the live dashboard at `http://localhost:8080/dashboard` (Ensure your local server is running with `uv run python app/main.py`).
*   **Action:** Scroll through the top of the page.
*   **Talk Track:** Point out the beautiful UI and the historical price graph. Explain how it clearly visualizes your property's price vs the market median over time. 
*   **Action:** Click the **"Scan Market"** button. 
*   **Talk Track:** Explain that this triggers the `MarketDataCollector` tool, which fetches fresh competitor data and commits it securely to the agent's memory ledger. Point out the telemetry timeline logs on the right side.
*   **Action:** Scroll down to the Business Strategy Assistant (Chatbox). Click the **"What are my occupancy risks?"** quick-prompt button.
*   **Talk Track:** Watch the AI respond. Explain to the judges how the Coordinator Agent analyzed the raw data and perfectly identified your pricing gaps, proving that this isn't just a basic chatbot, but a strategic business partner. 

---

### Act 3: The Wrap Up (1 minute)
*   **Screen:** Stay on the Dashboard.
*   **Talk Track:** Summarize the massive business impact. RateRadar takes pricing from a tedious, error-prone chore and turns it into an automated, data-driven strategy. Mention that it also has safety guardrails built-in to block out-of-scope queries (like Dublin). Thank the Kaggle judges for their time.
