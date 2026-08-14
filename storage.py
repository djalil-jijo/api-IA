import json
import sqlite3
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "inventory_optimization.db")


class StorageManager:
    """Thread-safe SQLite storage handler for supply chain optimization runs."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initializes the database schema and indexes."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS optimization_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    remaining_warehouse_stock REAL NOT NULL,
                    total_shipped REAL NOT NULL,
                    recommended_purchase_quantity REAL NOT NULL
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_product_id_timestamp 
                ON optimization_runs(product_id, timestamp DESC)
            """)
            conn.commit()

    def get_last_run(self, product_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves the most recent previous run for a given product_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, response_json, remaining_warehouse_stock, total_shipped, recommended_purchase_quantity
                FROM optimization_runs
                WHERE product_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (product_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "timestamp": row["timestamp"],
                    "response": json.loads(row["response_json"]),
                    "remaining_warehouse_stock": row["remaining_warehouse_stock"],
                    "total_shipped": row["total_shipped"],
                    "recommended_purchase_quantity": row["recommended_purchase_quantity"],
                }
            return None

    def save_run(
        self,
        product_id: str,
        timestamp: str,
        request_data: Dict[str, Any],
        response_data: Dict[str, Any],
        remaining_warehouse_stock: float,
        total_shipped: float,
        recommended_purchase_quantity: float
    ) -> None:
        """Saves an optimization run payload into SQLite storage."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO optimization_runs (
                    product_id, timestamp, request_json, response_json,
                    remaining_warehouse_stock, total_shipped, recommended_purchase_quantity
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                product_id,
                timestamp,
                json.dumps(request_data, ensure_ascii=False),
                json.dumps(response_data, ensure_ascii=False),
                remaining_warehouse_stock,
                total_shipped,
                recommended_purchase_quantity
            ))
            conn.commit()

    def cleanup_old_records(self, retention_days: int = 30, max_runs_per_product: int = 50) -> int:
        """
        Deletes optimization runs older than retention_days OR exceeding max_runs_per_product per product_id.
        Returns total count of deleted records.
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cutoff_iso = cutoff_date.isoformat()
        purged_count = 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. Delete records older than retention_days
            cursor.execute("""
                DELETE FROM optimization_runs
                WHERE timestamp < ?
            """, (cutoff_iso,))
            purged_count += cursor.rowcount

            # 2. Delete runs exceeding max_runs_per_product per product_id
            cursor.execute("""
                DELETE FROM optimization_runs
                WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY product_id ORDER BY timestamp DESC
                        ) as row_num
                        FROM optimization_runs
                    ) WHERE row_num <= ?
                )
            """, (max_runs_per_product,))
            purged_count += cursor.rowcount

            conn.commit()
            return purged_count

