"""
Tests for restocking API endpoints.
"""
import pytest


class TestRestockingRecommendationsEndpoint:
    """Test suite for the restocking recommendations endpoint."""

    def test_zero_budget_returns_no_recommendations(self, client):
        """Test that a zero budget yields no recommendations."""
        response = client.get("/api/restocking/recommendations?budget=0")
        assert response.status_code == 200

        data = response.json()
        assert data["recommendations"] == []
        assert data["total_cost"] == 0
        assert data["remaining_budget"] == 0

    def test_recommendations_respect_budget_cap(self, client):
        """Test that total recommended cost never exceeds the given budget."""
        response = client.get("/api/restocking/recommendations?budget=3000")
        assert response.status_code == 200

        data = response.json()
        assert data["total_cost"] <= 3000
        assert abs(data["remaining_budget"] - (3000 - data["total_cost"])) < 0.01

        calculated_total = sum(r["line_cost"] for r in data["recommendations"])
        assert abs(calculated_total - data["total_cost"]) < 0.01

    def test_recommendations_only_include_positive_shortfall(self, client):
        """Test that only items with forecasted_demand > current_demand are recommended."""
        response = client.get("/api/restocking/recommendations?budget=1000000")
        assert response.status_code == 200

        data = response.json()
        skus = {r["item_sku"] for r in data["recommendations"]}

        # MTR-304 has decreasing demand (forecasted < current) - must never appear
        assert "MTR-304" not in skus

        for rec in data["recommendations"]:
            assert rec["shortfall_qty"] > 0
            assert rec["recommended_quantity"] > 0
            assert rec["recommended_quantity"] <= rec["shortfall_qty"]

    def test_larger_budget_recommends_more_or_equal_items(self, client):
        """Test that increasing the budget never decreases coverage."""
        small = client.get("/api/restocking/recommendations?budget=500").json()
        large = client.get("/api/restocking/recommendations?budget=50000").json()

        assert len(large["recommendations"]) >= len(small["recommendations"])
        assert large["total_cost"] >= small["total_cost"]

    def test_recommendations_sorted_by_shortfall_descending(self, client):
        """Test that recommendations are prioritized by largest forecasted shortfall first."""
        response = client.get("/api/restocking/recommendations?budget=50000")
        data = response.json()

        shortfalls = [r["shortfall_qty"] for r in data["recommendations"]]
        assert shortfalls == sorted(shortfalls, reverse=True)

    def test_recommendation_structure(self, client):
        """Test that each recommendation has the expected fields and types."""
        response = client.get("/api/restocking/recommendations?budget=5000")
        data = response.json()
        assert len(data["recommendations"]) > 0

        rec = data["recommendations"][0]
        for field in ["item_sku", "item_name", "unit_cost", "current_demand",
                      "forecasted_demand", "shortfall_qty", "recommended_quantity",
                      "line_cost", "trend"]:
            assert field in rec

        assert isinstance(rec["recommended_quantity"], int)
        assert isinstance(rec["unit_cost"], (int, float))
        assert rec["unit_cost"] > 0


class TestRestockingOrdersEndpoint:
    """Test suite for submitting restocking orders."""

    def test_submit_restocking_order_creates_submitted_order(self, client):
        """Test that submitting a restocking order creates an order with Submitted status."""
        payload = {
            "items": [
                {"item_sku": "WDG-001", "item_name": "Industrial Widget Type A", "quantity": 10, "unit_cost": 45.0}
            ]
        }
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 200

        order = response.json()
        assert order["status"] == "Submitted"
        assert order["customer"] == "Internal Restocking"
        assert order["items"][0]["sku"] == "WDG-001"
        assert order["items"][0]["quantity"] == 10
        assert abs(order["total_value"] - 450.0) < 0.01

    def test_submitted_order_has_positive_lead_time(self, client):
        """Test that the expected delivery date is after the order date."""
        payload = {
            "items": [
                {"item_sku": "FLT-405", "item_name": "Oil Filter Cartridge", "quantity": 5, "unit_cost": 6.25}
            ]
        }
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 200

        order = response.json()
        from datetime import datetime
        order_date = datetime.fromisoformat(order["order_date"])
        expected_delivery = datetime.fromisoformat(order["expected_delivery"])
        lead_time_days = (expected_delivery - order_date).days

        assert expected_delivery > order_date
        assert 3 <= lead_time_days <= 14

    def test_submitted_order_appears_in_orders_endpoint(self, client):
        """Test that a submitted restocking order shows up via GET /api/orders."""
        payload = {
            "items": [
                {"item_sku": "GSK-203", "item_name": "High-Temperature Gasket", "quantity": 20, "unit_cost": 8.75}
            ]
        }
        create_response = client.post("/api/restocking/orders", json=payload)
        new_order_id = create_response.json()["id"]

        response = client.get("/api/orders?status=submitted")
        assert response.status_code == 200

        data = response.json()
        assert any(order["id"] == new_order_id for order in data)
        for order in data:
            assert order["status"] == "Submitted"

    def test_submit_empty_items_returns_400(self, client):
        """Test that submitting an order with no items is rejected."""
        response = client.post("/api/restocking/orders", json={"items": []})
        assert response.status_code == 400

        data = response.json()
        assert "detail" in data
