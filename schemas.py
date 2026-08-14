from typing import List, Optional
from pydantic import BaseModel, Field


class SectorInput(BaseModel):
    sector_id: int = Field(..., description="Unique identifier for the sector")
    sector_name: str = Field(..., description="Human-readable name of the sector")
    visiting_days: str = Field(..., description="Comma-separated visiting days (e.g., 'Dimanche, Mardi')")
    current_stock: float = Field(..., ge=0.0, description="Current available stock in the sector")
    avg_daily_consumption: float = Field(..., ge=0.0, description="Average daily consumption rate for the sector")


class CentralWarehouseInput(BaseModel):
    product_id: str = Field(..., description="Unique product identifier (SKU/ID)")
    product_name: str = Field(..., description="Name of the product")
    current_stock: float = Field(..., ge=0.0, description="Current stock available at the central warehouse")
    lead_time_days: float = Field(..., ge=0.0, description="Replenishment lead time in days")
    safety_stock: float = Field(..., ge=0.0, description="Buffer/safety stock quantity required")


class OptimizationRequest(BaseModel):
    target_day: Optional[str] = Field(
        default="",
        description="Target dispatch day (e.g. 'Mardi', 'Dimanche'). If empty, defaults to current server day."
    )
    truck_capacity_units: float = Field(..., gt=0.0, description="Maximum units the delivery truck can carry")
    target_days_to_cover: float = Field(default=7.0, gt=0.0, description="Target coverage period in days")
    retention_days: int = Field(default=30, ge=1, description="Data retention limit in days")
    central_warehouse: CentralWarehouseInput = Field(..., description="Central warehouse inventory details")
    sectors: List[SectorInput] = Field(..., description="List of distribution sectors")


class SectorAllocation(BaseModel):
    sector_id: int
    sector_name: str
    needed_units: float
    shipped_units: float
    stock_after_dispatch: float


class TruckDispatchPlan(BaseModel):
    truck_capacity_units: float
    total_needed_units: float
    total_shipped_units: float
    capacity_utilization_pct: float
    sectors_allocated: List[SectorAllocation]


class CentralProcurementPlan(BaseModel):
    initial_warehouse_stock: float
    remaining_warehouse_stock: float
    total_network_daily_consumption: float
    reorder_point_rop: float
    purchase_required: bool
    recommended_purchase_quantity: float


class ProductInfo(BaseModel):
    product_id: str
    product_name: str


class HistoricalComparison(BaseModel):
    has_previous_run: bool
    previous_run_timestamp: Optional[str] = None
    warehouse_stock_change: float = 0.0
    total_shipped_change: float = 0.0
    purchase_qty_change: float = 0.0
    notes: str = ""


class StorageStats(BaseModel):
    records_purged: int = 0


class OptimizationResponse(BaseModel):
    dispatch_day: str
    product_info: ProductInfo
    truck_dispatch_plan: TruckDispatchPlan
    central_procurement_plan: CentralProcurementPlan
    historical_comparison: HistoricalComparison
    storage_stats: StorageStats
