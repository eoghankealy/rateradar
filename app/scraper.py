import asyncio
import datetime
import logging
import random
import re

import requests
from bs4 import BeautifulSoup

# =====================================================================
# DATA HYBRID LAYER POLICY & DEMO CONTINUITY:
# 1. Live Booking.com Scraper: Best-effort data collection path.
# 2. Seed/Synthetic Generator: Intentional fallback for live demo
#    continuity and scraper failure/blocking scenarios.
# 3. Labeling Requirement: All outputs, database records, API
#    responses, and UI elements must retain a clear data_source label
#    ('live' vs 'seed').
# =====================================================================

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scraper")

# Global configurations (module-level constants) to avoid mutable class attribute warnings
LOCATIONS = {
    "greencastle": {"base_rate": 110, "name": "Greencastle"},
    "stroove": {"base_rate": 130, "name": "Stroove"},
    "moville": {"base_rate": 115, "name": "Moville"},
    "culdaff": {"base_rate": 125, "name": "Culdaff"},
    "donegal": {"base_rate": 120, "name": "Co. Donegal"},
}

# Booking.com internal city dest_id values — required for unambiguous location resolution.
# Without dest_id the search string fails for small Irish villages.
# dest_id=-1503269  →  Greencastle, Donegal County, Ireland
# All nearby locations (Moville, Stroove, Culdaff) map to the same Greencastle city search
# because Booking.com groups them under the Greencastle/Inishowen area.
BOOKING_DEST_IDS = {
    "greencastle": {"dest_id": "-1503269", "dest_type": "city", "ss": "Greencastle"},
    "moville":     {"dest_id": "-1503269", "dest_type": "city", "ss": "Greencastle"},
    "stroove":     {"dest_id": "-1503269", "dest_type": "city", "ss": "Greencastle"},
    "culdaff":     {"dest_id": "-1503269", "dest_type": "city", "ss": "Greencastle"},
    "donegal":     {"dest_id": "-1503269", "dest_type": "city", "ss": "Greencastle"},
}

COMPETITORS = {
    "greencastle": [
        "Greencastle Harbour View Apartment",
        "Foyle Side Holiday Home",
        "Inishowen Links Cottage",
    ],
    "stroove": [
        "Stroove Lighthouse Cottage",
        "Sandy Beach Holiday Villa",
        "Inishowen Heights Bed & Breakfast",
    ],
    "moville": [
        "Moville Bay View Suite",
        "Inishowen Pier Apartment",
        "Lough Foyle Retreat",
    ],
    "culdaff": [
        "Culdaff River Cottage",
        "Ballyliffin Coast Lodge",
        "Dunree Beach View Apartment",
    ],
    "donegal": [
        "Donegal Coast Holiday Apartment",
        "Wild Atlantic Way Cabin",
        "Inishowen Luxury Suite",
    ],
}


class ScrapingBlockedException(Exception):
    """Custom exception raised when Booking.com blocks the request."""

    pass


class ScraperError(Exception):
    """Custom exception for other general scraping failures."""

    pass


class MarketDataCollector:
    """Hybrid market data collector supporting live scraping and synthetic fallback."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
        )

    def sanitize_scraped_text(self, text: str) -> str:
        """Sanitizes raw text retrieved from external web elements.

        DELIBERATE SECURITY PRACTICE: Strip HTML tags, escape markdown, and filter
        out injection commands (e.g. system control words) to prevent prompt injection
        attacks via scraped listing data.
        """
        if not text:
            return ""
        # 1. Strip HTML tags
        text = re.sub(r"<[^>]*>", "", text)
        # 2. Filter out known injection keywords
        block_words = [
            "system",
            "ignore prior",
            "ignore instructions",
            "override",
            "you are now",
        ]
        for word in block_words:
            text = re.compile(re.escape(word), re.IGNORECASE).sub("", text)
        # 3. Clean spacing
        text = " ".join(text.split())
        return text

    # ------------------------------------------------------------------
    # Playwright-based live scraper (primary path)
    # ------------------------------------------------------------------

    async def _fetch_playwright(
        self, location: str, checkin_date: str, checkout_date: str, guest_count: int
    ) -> dict:
        """Headless-browser scrape using Playwright + dest_id for unambiguous location.

        Booking.com uses AWS WAF that blocks plain HTTP requests. Playwright runs
        a real Chromium instance that executes the WAF JavaScript challenge and
        renders the search results page correctly.
        """
        from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

        loc_key = location.lower().strip()
        dest_info = BOOKING_DEST_IDS.get(loc_key, BOOKING_DEST_IDS["greencastle"])

        search_url = (
            f"https://www.booking.com/searchresults.en-gb.html"
            f"?ss={dest_info['ss']}"
            f"&dest_id={dest_info['dest_id']}"
            f"&dest_type={dest_info['dest_type']}"
            f"&checkin={checkin_date}"
            f"&checkout={checkout_date}"
            f"&group_adults={guest_count}"
            f"&no_rooms=1"
            f"&group_children=0"
            f"&lang=en-gb"
            # Filter to like-for-like property types only:
            # ht_id=220 = Entire homes & apartments
            # ht_id=201 = Apartments
            # ht_id=222 = Holiday homes
            f"&nflt=ht_id%3D220%3Bht_id%3D201%3Bht_id%3D222"
            f"&sb=1&src_elem=sb&src=hotel"
        )

        logger.info(f"Playwright scrape URL: {search_url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
                locale="en-GB",
                extra_http_headers={
                    "Accept-Language": "en-GB,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
            )
            await context.add_cookies([{
                "name": "pcm_consent",
                "value": "analytical%3Dtrue%26confirmed%3Dtrue%26functional%3Dtrue%26marketing%3Dtrue",
                "domain": ".booking.com",
                "path": "/",
            }])

            page = await context.new_page()

            # Warm-up homepage visit to establish real session cookies
            try:
                await page.goto("https://www.booking.com/", timeout=20000, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)
                # Dismiss cookie banner if present
                for sel in ['#onetrust-accept-btn-handler', 'button:has-text("Accept")', 'button[id*="onetrust-accept"]']:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=1000):
                            await btn.click()
                            await page.wait_for_timeout(800)
                            break
                    except Exception:
                        pass
            except Exception:
                pass

            # Navigate to search page
            try:
                await page.goto(search_url, timeout=35000, wait_until="domcontentloaded")
            except PlaywrightTimeout:
                pass

            await page.wait_for_timeout(3000)
            landed_url = page.url

            # Detect ghost redirect (bot-detection disguised as location not found)
            if "errorc_searchstring_not_found" in landed_url or "searchresults" not in landed_url:
                await browser.close()
                raise ScrapingBlockedException(
                    f"Booking.com ghost redirect detected (bot-detection). Landed: {landed_url[:80]}"
                )

            # Wait for property cards
            card_selector = None
            for selector in [
                '[data-testid="property-card"]',
                '[data-testid="property-card-container"]',
                'div[data-hotelid]',
                '.sr_property_block',
            ]:
                try:
                    await page.wait_for_selector(selector, timeout=10000)
                    card_selector = selector
                    break
                except PlaywrightTimeout:
                    pass

            if not card_selector:
                await browser.close()
                raise ScraperError("No property listing cards found on search results page.")

            cards = await page.query_selector_all(card_selector)
            listings = []

            for card in cards:
                try:
                    # Name
                    name = None
                    for ns in ['[data-testid="title"]', '.sr-hotel__name', 'h3', 'h2']:
                        el = await card.query_selector(ns)
                        if el:
                            t = (await el.inner_text()).strip()
                            if t:
                                name = self.sanitize_scraped_text(t)
                                break
                    if not name:
                        continue

                    # Price — Booking.com search cards show the TOTAL stay price
                    price_text = ""
                    for ps in [
                        '[data-testid="price-and-discounted-price"]',
                        '[data-testid="price"]',
                        '.prco-valign-middle-helper',
                        '[class*="Price"]',
                    ]:
                        el = await card.query_selector(ps)
                        if el:
                            t = (await el.inner_text()).strip()
                            if re.search(r'\d{2,}', t):
                                price_text = t
                                break

                    nums = [int(n) for n in re.findall(r'\d+', price_text.replace(',', '')) if int(n) > 20]
                    if not nums:
                        continue
                    price = float(max(nums))

                    # Rating
                    rating = 8.5
                    rel = await card.query_selector('[data-testid="review-score"]')
                    if rel:
                        rt = await rel.inner_text()
                        rn = re.findall(r'\d+\.\d+', rt)
                        if rn:
                            rating = float(rn[0])

                    is_my_listing = "harbour bar" in name.lower()
                    listings.append({
                        "name": name,
                        "price": price,
                        "rating": rating,
                        "data_source": "live",
                        "is_my_listing": is_my_listing,
                    })
                except Exception as card_err:
                    logger.warning(f"Card parse error: {card_err}")

            await browser.close()

            if not listings:
                raise ScraperError("Playwright scraped page but extracted zero listings.")

            logger.info(f"Playwright scraped {len(listings)} listings from Booking.com")
            return {
                "status": "success",
                "data_source": "live",
                "listings": listings,
                "execution_log": f"Playwright live scrape: {len(listings)} listings from Booking.com (Greencastle, dest_id={dest_info['dest_id']}).",
            }

    def fetch_live_market_prices(
        self, location: str, checkin_date: str, checkout_date: str, guest_count: int
    ) -> dict:
        """Fetches live competitor prices from Booking.com.

        Primary path: Playwright headless browser (handles AWS WAF JS challenge).
        Fallback path: requests + BeautifulSoup (for non-WAF scenarios).
        Raises ScrapingBlockedException if all live paths are blocked.
        """
        logger.info(
            f"Attempting live Booking.com scrape for {location}, "
            f"check-in: {checkin_date}, check-out: {checkout_date}, guests: {guest_count}"
        )

        # --- Primary path: Playwright ---
        # Run in a dedicated thread with its own event loop so this works both
        # when called from a plain sync context (scheduler.py) AND from inside
        # FastAPI's running async event loop (dashboard button → /api/scan).
        try:
            import concurrent.futures

            def _run_in_thread():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(
                        self._fetch_playwright(location, checkin_date, checkout_date, guest_count)
                    )
                finally:
                    loop.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_in_thread)
                return future.result(timeout=120)  # 2-minute hard timeout per scrape

        except ScrapingBlockedException:
            raise  # Re-raise block so agent triggers fallback
        except Exception as pw_err:
            logger.warning(f"Playwright scrape failed: {pw_err}. Attempting requests fallback...")


        # --- Fallback path: requests + BeautifulSoup ---
        loc_query = f"{location}, Co. Donegal, Ireland"
        url = "https://www.booking.com/searchresults.html"
        params = {
            "ss": loc_query,
            "checkin": checkin_date,
            "checkout": checkout_date,
            "group_adults": str(guest_count),
            "no_rooms": "1",
            "group_children": "0",
            "nflt": "ht_id=220;ht_id=201;ht_id=222",
        }

        try:
            # Short timeout to fail-fast and switch to fallback
            response = self.session.get(url, params=params, timeout=12)

            if (
                response.status_code == 403
                or "blocked" in response.text.lower()
                or "captcha" in response.text.lower()
            ):
                raise ScrapingBlockedException(
                    "Booking.com requests blocked (CAPTCHA or 403 Forbidden)."
                )

            if response.status_code != 200:
                raise ScraperError(f"HTTP error status code: {response.status_code}")

            soup = BeautifulSoup(response.text, "html.parser")

            # Look for property cards (different test-ids are used by Booking.com)
            cards = soup.find_all("div", {"data-testid": "property-card"})
            if not cards:
                # Fallback search if class names differ
                cards = soup.select(
                    ".sr_property_block, [data-testid='property-card-container']"
                )

            if not cards:
                # Could be blocked or structure changed
                if (
                    "security check" in response.text.lower()
                    or "distil" in response.text.lower()
                ):
                    raise ScrapingBlockedException("Anti-bot wall detected.")
                raise ScraperError("No property listings found in search results.")

            listings = []
            for card in cards:
                try:
                    # Name extraction
                    name_el = card.find(
                        attrs={"data-testid": "title"}
                    ) or card.select_one(".sr-property-card__title, .fcab3ed911")
                    if not name_el:
                        continue
                    name = self.sanitize_scraped_text(name_el.get_text(strip=True))

                    # Price extraction
                    price_el = card.find(
                        attrs={"data-testid": "price-and-discounted-price"}
                    ) or card.select_one(
                        ".f58197cacb, .f6431b446c, [data-testid='price-and-discounted-price-container']"
                    )
                    if not price_el:
                        continue
                    price_text = price_el.get_text(strip=True)

                    # Parse numeric price (e.g. "€ 150" or "€150")
                    price_numbers = re.findall(
                        r"\d[\d,]*", price_text.replace("\xa0", "")
                    )
                    if not price_numbers:
                        continue
                    price = float(price_numbers[0].replace(",", ""))

                    # Rating extraction (optional)
                    rating_el = card.find(
                        attrs={"data-testid": "review-score-badge"}
                    ) or card.select_one(".b5cd7b3b1e, .d0522b0cca")
                    rating = 8.5  # default rating
                    if rating_el:
                        try:
                            rating = float(rating_el.get_text(strip=True))
                        except ValueError:
                            pass

                    # Identify the Harbour Bar Apartment listing
                    link_el = card.find("a", href=True) or card.select_one("a[href]")
                    link_url = link_el["href"] if link_el else ""

                    is_my_listing = False
                    if (
                        "harbour bar apartment" in name.lower()
                        or "harbour-bar-apartment" in link_url.lower()
                    ):
                        is_my_listing = True

                    listings.append(
                        {
                            "name": name,
                            "price": price,
                            "rating": rating,
                            "data_source": "live",
                            "is_my_listing": is_my_listing,
                        }
                    )
                except Exception as card_err:
                    logger.warning(f"Error parsing property card: {card_err}")
                    continue

            if not listings:
                raise ScraperError(
                    "Failed to extract any listings from property cards."
                )

            logger.info(
                f"Successfully scraped {len(listings)} listings from Booking.com"
            )
            return {
                "status": "success",
                "data_source": "live",
                "listings": listings,
                "execution_log": "Scraped successfully from Booking.com live website.",
            }

        except requests.RequestException as req_err:
            raise ScraperError(f"Network request failed: {req_err}") from req_err

    def calculate_synthetic_baseline(
        self, location: str, checkin_date: str, checkout_date: str, guest_count: int
    ) -> float:
        """Calculates a realistic base price using geographical, seasonal, and guest metrics."""
        loc_key = location.lower().strip()
        loc_info = LOCATIONS.get(loc_key, LOCATIONS["donegal"])

        base_rate = float(loc_info["base_rate"])

        # 1. Parse check-in date
        try:
            date_obj = datetime.datetime.strptime(checkin_date, "%Y-%m-%d")
        except ValueError:
            date_obj = datetime.datetime.now()

        # 2. Seasonality Factor
        month = date_obj.month
        if month in [6, 7, 8]:  # Summer Peak (June, July, August)
            season_factor = 1.45
        elif month in [5, 9, 10]:  # Shoulder Season (May, September, October)
            season_factor = 1.15
        elif month in [11, 12, 1, 2]:  # Winter Low
            season_factor = 0.85
        else:  # Spring (March, April)
            season_factor = 1.00

        # 3. Weekend Markup (Friday and Saturday nights)
        is_weekend = date_obj.weekday() in [4, 5]  # Friday = 4, Saturday = 5
        weekend_factor = 1.25 if is_weekend else 1.00

        # 4. Guest Count Multiplier
        # €25 extra per guest above 2 guests
        guest_factor = max(0, guest_count - 2) * 25.0

        # 5. Combined Math (Total price for 2-night stay)
        daily_rate = (base_rate * season_factor * weekend_factor) + guest_factor
        total_2_nights = daily_rate * 2

        return total_2_nights

    def generate_synthetic_market_prices(
        self, location: str, checkin_date: str, checkout_date: str, guest_count: int
    ) -> dict:
        """Generates realistic market prices for competitors when live scraping is blocked or bypassed."""
        logger.info(
            f"Generating synthetic market data for {location}, guests: {guest_count}"
        )

        loc_key = location.lower().strip()
        comp_names = COMPETITORS.get(loc_key, COMPETITORS["donegal"])

        # Base 2-night competitive price
        baseline_price = self.calculate_synthetic_baseline(
            location, checkin_date, checkout_date, guest_count
        )

        # Generate competitor rates with slight deviations
        listings = []
        for comp_name in comp_names:
            # Seed the random number generator using property name + date for consistency across requests
            seed_val = hash(comp_name + checkin_date + str(guest_count))
            random.seed(seed_val)

            # Competitors range from -15% to +20% of baseline
            deviation = random.uniform(-0.15, 0.20)
            price = round(baseline_price * (1 + deviation), 2)
            rating = round(random.uniform(7.8, 9.6), 1)

            listings.append(
                {
                    "name": comp_name,
                    "price": price,
                    "rating": rating,
                    "data_source": "seed",
                    "is_my_listing": False,
                }
            )

        # Deterministically append our own listing to the synthetic dataset
        my_price = self.fetch_my_listing_price(checkin_date, checkout_date, guest_count)
        listings.append(
            {
                "name": "Your Property",
                "price": my_price,
                "rating": 9.2,
                "data_source": "seed",
                "is_my_listing": True,
            }
        )

        return {
            "status": "success",
            "data_source": "seed",
            "listings": listings,
            "execution_log": "Live scraping unavailable/blocked. Generated seed data using seasonal/guest model.",
        }

    def collect_market_data(
        self,
        location: str,
        checkin_date: str,
        checkout_date: str,
        guest_count: int,
        force_fallback: bool = False,
    ) -> dict:
        """Collects pricing data by trying live scraper first, then falling back to synthetic generator."""
        if force_fallback:
            return self.generate_synthetic_market_prices(
                location, checkin_date, checkout_date, guest_count
            )

        try:
            return self.fetch_live_market_prices(
                location, checkin_date, checkout_date, guest_count
            )
        except (ScrapingBlockedException, ScraperError) as err:
            logger.warning(
                f"Live data collection failed. Reason: {err}. Falling back to seed data generator."
            )
            return self.generate_synthetic_market_prices(
                location, checkin_date, checkout_date, guest_count
            )

    def fetch_my_listing_price(
        self, checkin_date: str, checkout_date: str, guest_count: int
    ) -> float:
        """Determines the pricing for the user's own listing.

        Simulates fetching the user's own listing price (e.g. from their channel manager/PMS or Booking.com).
        For the capstone, it calculates a base rate with slight variations.
        """
        # Calculate my listing baseline
        base_competitor_price = self.calculate_synthetic_baseline(
            "donegal", checkin_date, checkout_date, guest_count
        )

        # Seed the price calculation for repeatability
        seed_val = hash("my_apartment" + checkin_date + str(guest_count))
        random.seed(seed_val)

        # Set my price at a baseline close to market but slightly lower or higher depending on the day
        # Let's say my listing baseline is €220 for 2 nights for 2 guests in low season.
        # Let's make it deterministic so it compares with competitors.
        # Competitors baseline is base_competitor_price.
        # We will make our price slightly under, over, or perfectly aligned with the market to demonstrate alerts.
        # E.g., on summer weekends we price way too low (alerts should trigger), or on winter weekdays too high.
        day_of_year = (
            datetime.datetime.strptime(checkin_date, "%Y-%m-%d").timetuple().tm_yday
        )

        if day_of_year % 3 == 0:
            # Underpriced - Bottom 10% alert test
            my_price = base_competitor_price * 0.82
        elif day_of_year % 3 == 1:
            # Overpriced - Top 50% alert test
            my_price = base_competitor_price * 1.30
        else:
            # Healthy
            my_price = base_competitor_price * 0.98

        return round(my_price, 2)
