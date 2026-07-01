import inspect

from fastapi.testclient import TestClient

from app import database
from app.main import app

client = TestClient(app)
API_KEY = "capstone-key-2026"


def test_api_key_header_missing():
    """Verify that endpoints reject requests without X-API-Key."""
    response = client.get("/api/pricing?guest_count=2")
    assert response.status_code == 401
    assert response.json()["status"] == "error"
    assert "Unauthorized" in response.json()["message"]


def test_api_key_header_invalid():
    """Verify that endpoints reject invalid X-API-Key values."""
    response = client.get(
        "/api/pricing?guest_count=2", headers={"X-API-Key": "invalid-key"}
    )
    assert response.status_code == 401


def test_date_validation_past():
    """Verify that check-in dates in the past are rejected with a 400."""
    headers = {"X-API-Key": API_KEY}
    response = client.post(
        "/api/scan",
        headers=headers,
        json={
            "location": "Greencastle",
            "checkin_date": "2020-01-01",  # Past date
            "guest_count": 4,
        },
    )
    assert response.status_code == 400
    assert response.json()["status"] == "error"
    assert "cannot be in the past" in response.json()["message"]


def test_date_validation_horizon():
    """Verify that check-in dates beyond 90 days are rejected with a 400."""
    headers = {"X-API-Key": API_KEY}
    response = client.post(
        "/api/scan",
        headers=headers,
        json={
            "location": "Greencastle",
            "checkin_date": "2030-01-01",  # Beyond 90 days
            "guest_count": 4,
        },
    )
    assert response.status_code == 400
    assert response.json()["status"] == "error"
    assert "Time horizon is limited" in response.json()["message"]


def test_date_validation_invalid_format():
    """Verify that malformed check-in date formats are rejected with a 400."""
    headers = {"X-API-Key": API_KEY}
    response = client.post(
        "/api/scan",
        headers=headers,
        json={
            "location": "Greencastle",
            "checkin_date": "07-15-2026",  # Malformed date
            "guest_count": 4,
        },
    )
    assert response.status_code == 400
    assert response.json()["status"] == "error"
    assert "Invalid date format" in response.json()["message"]


def test_guest_validation_invalid():
    """Verify that odd guest capacities are rejected with a 400."""
    headers = {"X-API-Key": API_KEY}
    response = client.post(
        "/api/scan",
        headers=headers,
        json={
            "location": "Greencastle",
            "checkin_date": "2026-07-15",
            "guest_count": 5,  # Disallowed count (only 2, 4, 6 allowed)
        },
    )
    assert response.status_code == 400
    assert response.json()["status"] == "error"
    assert "Only 2, 4, or 6 guests are allowed" in response.json()["message"]


def test_sql_injection_defense():
    """Verifies that save_scan_results source code enforces SQL query parameterization."""
    source = inspect.getsource(database.save_scan_results)

    # Assert that no cursor.execute calls use string formatting or concatenation
    for line in source.splitlines():
        if "cursor.execute" in line or ".execute(" in line:
            assert 'f"' not in line
            assert "f'" not in line
            assert "%" not in line
            assert ".format(" not in line


def test_not_found_live_uses_seed_fallback():
    """Verify that when a live scrape does not find our listing, a seasonal seed price is
    used as fallback (my_listing_found=0, my_price is not None) and the recommendation
    explains the property was likely booked or unavailable."""
    from unittest.mock import patch
    from app.agent import run_market_scan_tool

    # Mock collector.collect_market_data to return a live result without our own listing
    mock_market_data = {
        "data_source": "live",
        "listings": [
            {"name": "Competitor 1", "price": 200.0, "is_my_listing": 0, "data_source": "live"},
            {"name": "Competitor 2", "price": 220.0, "is_my_listing": 0, "data_source": "live"},
        ]
    }

    with patch("app.agent.collector.collect_market_data", return_value=mock_market_data):
        result = run_market_scan_tool(
            location="Greencastle",
            checkin_date="2026-07-20",
            checkout_date="2026-07-22",
            guest_count=2,
            force_fallback=False
        )

        # Apartment not found → seed fallback used, price is a real number
        assert result["my_listing_found"] == 0
        assert result["my_price"] is not None, "Seasonal seed price should be used when listing not found"
        assert isinstance(result["my_price"], float)
        # Status is calculated from the seed price vs competitor percentiles
        assert result["status"] in ("Too Low", "Healthy", "Too High")
        # Recommendation should explain listing was not found / likely booked
        assert "not found" in result["recommendation"].lower() or "booked" in result["recommendation"].lower()


