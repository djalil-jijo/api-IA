import pytest
import os
import json
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from main import app, storage_manager
from storage import StorageManager


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """Use a temporary database file for isolated testing."""
    test_db_path = str(tmp_path / "test_inventory.db")
    storage_manager.db_path = test_db_path
    storage_manager._init_db()
    yield
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except OSError:
            pass


client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "supply-chain-optimizer"


def test_optimize_first_run():
    payload = {
        "target_day": "Mardi",
        "truck_capacity_units": 500.0,
        "target_days_to_cover": 7.0,
        "retention_days": 30,
        "central_warehouse": {
            "product_id": "PROD-001",
            "product_name": "Lait UHT 1L",
            "current_stock": 1000.0,
            "lead_time_days": 3.0,
            "safety_stock": 200.0
        },
        "sectors": [
            {
                "sector_id": 1,
                "sector_name": "Alger Centre",
                "visiting_days": "Dimanche, Mardi",
                "current_stock": 50.0,
                "avg_daily_consumption": 20.0
            },
            {
                "sector_id": 2,
                "sector_name": "Oran Ville",
                "visiting_days": "Lundi, Jeudi",
                "current_stock": 100.0,
                "avg_daily_consumption": 30.0
            },
            {
                "sector_id": 3,
                "sector_name": "Constantine Est",
                "visiting_days": "Mardi",
                "current_stock": 10.0,
                "avg_daily_consumption": 15.0
            }
        ]
    }

    response = client.post("/api/v1/supply-chain/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["dispatch_day"] == "Mardi"
    assert data["product_info"]["product_id"] == "PROD-001"

    # Sector 1 (Alger Centre): needed = 20*7 - 50 = 90
    # Sector 2 (Oran Ville): Not visited on Mardi (Lundi, Jeudi) -> Excluded
    # Sector 3 (Constantine Est): needed = 15*7 - 10 = 95
    # Total needed = 90 + 95 = 185
    # Truck capacity = 500 -> no capacity constraint, shipped = needed
    truck_plan = data["truck_dispatch_plan"]
    assert truck_plan["total_needed_units"] == 185.0
    assert truck_plan["total_shipped_units"] == 185.0
    assert len(truck_plan["sectors_allocated"]) == 2

    # Procurement Plan
    # Network total consumption = 20 + 30 + 15 = 65
    # Lead time = 3, safety = 200 -> ROP = (65 * 3) + 200 = 395
    # Remaining warehouse stock = 1000 - 185 = 815
    # 815 > 395 -> purchase_required = False, purchase_qty = 0
    proc = data["central_procurement_plan"]
    assert proc["initial_warehouse_stock"] == 1000.0
    assert proc["remaining_warehouse_stock"] == 815.0
    assert proc["total_network_daily_consumption"] == 65.0
    assert proc["reorder_point_rop"] == 395.0
    assert proc["purchase_required"] is False
    assert proc["recommended_purchase_quantity"] == 0.0

    # Historical comparison first run
    hist = data["historical_comparison"]
    assert hist["has_previous_run"] is False
    assert hist["previous_run_timestamp"] is None
    assert "First optimization run" in hist["notes"]


def test_truck_capacity_constraint():
    payload = {
        "target_day": "Mardi",
        "truck_capacity_units": 100.0,  # Capacity bottleneck!
        "target_days_to_cover": 7.0,
        "central_warehouse": {
            "product_id": "PROD-002",
            "product_name": "Jus 1L",
            "current_stock": 500.0,
            "lead_time_days": 2.0,
            "safety_stock": 50.0
        },
        "sectors": [
            {
                "sector_id": 1,
                "sector_name": "Sector A",
                "visiting_days": "Mardi",
                "current_stock": 0.0,
                "avg_daily_consumption": 20.0  # needed = 140
            },
            {
                "sector_id": 2,
                "sector_name": "Sector B",
                "visiting_days": "Mardi",
                "current_stock": 0.0,
                "avg_daily_consumption": 10.0  # needed = 70
            }
        ]
    }

    response = client.post("/api/v1/supply-chain/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()

    # Total needed = 140 + 70 = 210 > truck capacity (100)
    # Proportional allocation:
    # Sector A: (140 / 210) * 100 = 66.67
    # Sector B: (70 / 210) * 100 = 33.33
    truck_plan = data["truck_dispatch_plan"]
    assert truck_plan["total_needed_units"] == 210.0
    assert truck_plan["total_shipped_units"] == 100.0
    assert truck_plan["capacity_utilization_pct"] == 100.0

    alloc = truck_plan["sectors_allocated"]
    assert alloc[0]["shipped_units"] == 66.67
    assert alloc[1]["shipped_units"] == 33.33


def test_reorder_point_and_purchase():
    payload = {
        "target_day": "Mardi",
        "truck_capacity_units": 500.0,
        "target_days_to_cover": 7.0,
        "central_warehouse": {
            "product_id": "PROD-003",
            "product_name": "Huile 5L",
            "current_stock": 170.0,  # 170 stock, 100 safety stock -> 70 available to ship
            "lead_time_days": 5.0,
            "safety_stock": 100.0
        },
        "sectors": [
            {
                "sector_id": 1,
                "sector_name": "Sector A",
                "visiting_days": "Mardi",
                "current_stock": 0.0,
                "avg_daily_consumption": 10.0  # needed = 70
            }
        ]
    }

    response = client.post("/api/v1/supply-chain/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()

    proc = data["central_procurement_plan"]
    # Total shipped = 70
    # Remaining warehouse stock = 170 - 70 = 100 (exactly at safety stock)
    # Total network consumption = 10
    # ROP = (10 * 5) + 100 = 150
    # Remaining stock 100 < ROP 150 -> purchase_required = True
    # Recommended purchase = 150 - 100 = 50
    assert proc["remaining_warehouse_stock"] == 100.0
    assert proc["reorder_point_rop"] == 150.0
    assert proc["purchase_required"] is True
    assert proc["recommended_purchase_quantity"] == 50.0


def test_historical_comparison_sequential_runs():
    payload = {
        "target_day": "Mardi",
        "truck_capacity_units": 500.0,
        "target_days_to_cover": 7.0,
        "central_warehouse": {
            "product_id": "PROD-HIST",
            "product_name": "Eau Minerale 1.5L",
            "current_stock": 1000.0,
            "lead_time_days": 2.0,
            "safety_stock": 50.0
        },
        "sectors": [
            {
                "sector_id": 1,
                "sector_name": "Sector A",
                "visiting_days": "Mardi",
                "current_stock": 50.0,
                "avg_daily_consumption": 10.0  # needed = 20
            }
        ]
    }

    # First run
    res1 = client.post("/api/v1/supply-chain/optimize", json=payload)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["historical_comparison"]["has_previous_run"] is False

    # Second run with lower warehouse stock
    payload["central_warehouse"]["current_stock"] = 800.0
    res2 = client.post("/api/v1/supply-chain/optimize", json=payload)
    assert res2.status_code == 200
    data2 = res2.json()

    hist = data2["historical_comparison"]
    assert hist["has_previous_run"] is True
    assert hist["previous_run_timestamp"] is not None
    # Warehouse stock changed from 980.0 to 780.0 -> change = -200.0
    assert hist["warehouse_stock_change"] == -200.0


def test_data_retention_cleanup():
    # Insert a fake run manually with an old timestamp (40 days ago)
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    storage_manager.save_run(
        product_id="PROD-OLD",
        timestamp=old_timestamp,
        request_data={},
        response_data={},
        remaining_warehouse_stock=100.0,
        total_shipped=50.0,
        recommended_purchase_quantity=0.0
    )

    payload = {
        "target_day": "Mardi",
        "truck_capacity_units": 500.0,
        "retention_days": 30,
        "central_warehouse": {
            "product_id": "PROD-NEW",
            "product_name": "Farine 1kg",
            "current_stock": 500.0,
            "lead_time_days": 2.0,
            "safety_stock": 50.0
        },
        "sectors": []
    }

    response = client.post("/api/v1/supply-chain/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()

    # Old record (40 days old) should be purged
    assert data["storage_stats"]["records_purged"] >= 1


def test_warehouse_stock_limitation():
    """Test that dispatch is capped by available central warehouse stock above safety stock (stsec)."""
    payload = {
        "target_day": "Mardi",
        "truck_capacity_units": 1000.0,  # Truck can carry 1000
        "target_days_to_cover": 7.0,
        "central_warehouse": {
            "product_id": "PROD-LOW-WH",
            "product_name": "Sucre 1kg",
            "current_stock": 100.0,  # Warehouse has 100 units
            "lead_time_days": 2.0,
            "safety_stock": 50.0   # Safety stock is 50 -> Available to ship is only 100 - 50 = 50 units!
        },
        "sectors": [
            {
                "sector_id": 1,
                "sector_name": "Sector A",
                "visiting_days": "Mardi",
                "current_stock": 0.0,
                "avg_daily_consumption": 100.0  # Needs 700 units
            }
        ]
    }

    response = client.post("/api/v1/supply-chain/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()

    # Total shipped must not exceed warehouse stock minus safety stock (50.0)
    truck_plan = data["truck_dispatch_plan"]
    assert truck_plan["total_shipped_units"] == 50.0
    # Remaining warehouse stock must be equal to safety stock (50.0), not 0.0
    assert data["central_procurement_plan"]["remaining_warehouse_stock"] == 50.0


def test_warehouse_stock_already_below_safety_stock():
    """Test that if central warehouse is already at or below safety stock, 0 units are dispatched."""
    payload = {
        "target_day": "Mardi",
        "truck_capacity_units": 500.0,
        "target_days_to_cover": 7.0,
        "central_warehouse": {
            "product_id": "PROD-BELOW-SEC",
            "product_name": "Farine 1kg",
            "current_stock": 30.0,  # Current stock is 30, but safety stock is 50
            "lead_time_days": 2.0,
            "safety_stock": 50.0
        },
        "sectors": [
            {
                "sector_id": 1,
                "sector_name": "Sector A",
                "visiting_days": "Mardi",
                "current_stock": 0.0,
                "avg_daily_consumption": 10.0
            }
        ]
    }

    response = client.post("/api/v1/supply-chain/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()

    # 0 units shipped to preserve remaining stock
    assert data["truck_dispatch_plan"]["total_shipped_units"] == 0.0
    assert data["central_procurement_plan"]["remaining_warehouse_stock"] == 30.0
    assert data["central_procurement_plan"]["purchase_required"] is True

