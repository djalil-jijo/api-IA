from datetime import datetime
from typing import Optional, Dict, Any, List
from schemas import (
    OptimizationRequest,
    OptimizationResponse,
    ProductInfo,
    SectorAllocation,
    TruckDispatchPlan,
    CentralProcurementPlan,
    HistoricalComparison,
    StorageStats,
)

FRENCH_DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

DAY_TRANSLATIONS = {
    "monday": "Lundi", "mon": "Lundi", "lundi": "Lundi",
    "tuesday": "Mardi", "tue": "Mardi", "mardi": "Mardi",
    "wednesday": "Mercredi", "wed": "Mercredi", "mercredi": "Mercredi",
    "thursday": "Jeudi", "thu": "Jeudi", "jeudi": "Jeudi",
    "friday": "Vendredi", "fri": "Vendredi", "vendredi": "Vendredi",
    "saturday": "Samedi", "sat": "Samedi", "samedi": "Samedi",
    "sunday": "Dimanche", "sun": "Dimanche", "dimanche": "Dimanche",
}


def resolve_dispatch_day(target_day: Optional[str]) -> str:
    """Returns the cleaned target_day or the current server weekday in French if empty."""
    if target_day and target_day.strip():
        cleaned = target_day.strip()
        lower_cleaned = cleaned.lower()
        if lower_cleaned in DAY_TRANSLATIONS:
            return DAY_TRANSLATIONS[lower_cleaned]
        return cleaned.capitalize()
    
    # Server weekday index (0 = Monday, 6 = Sunday)
    weekday_idx = datetime.now().weekday()
    return FRENCH_DAYS[weekday_idx]


def is_sector_visited(visiting_days_str: str, target_day: str) -> bool:
    """Case-insensitive check if target_day matches visiting_days string."""
    if not visiting_days_str or not target_day:
        return False
    target = target_day.strip().lower()
    days_list = [d.strip().lower() for d in visiting_days_str.split(",")]
    
    for day in days_list:
        if target == day:
            return True
        # Check standard translations (e.g., 'mardi' in 'dimanche, mardi')
        translated_target = DAY_TRANSLATIONS.get(target, target).lower()
        translated_day = DAY_TRANSLATIONS.get(day, day).lower()
        if translated_target == translated_day:
            return True
        if translated_target in translated_day or translated_day in translated_target:
            return True
    return False


def run_optimization(
    request: OptimizationRequest,
    last_run: Optional[Dict[str, Any]],
    records_purged: int
) -> OptimizationResponse:
    """Executes the core supply chain inventory optimization algorithm."""
    
    # 1. Resolve dispatch day
    dispatch_day = resolve_dispatch_day(request.target_day)

    # 2. Filter eligible sectors based on scheduled visiting days
    eligible_sectors = [
        s for s in request.sectors
        if is_sector_visited(s.visiting_days, dispatch_day)
    ]

    # 3. Calculate sector inventory requirements
    sectors_alloc_data = []
    total_needed_units = 0.0

    for s in eligible_sectors:
        needed = max(0.0, (s.avg_daily_consumption * request.target_days_to_cover) - s.current_stock)
        sectors_alloc_data.append({
            "sector": s,
            "needed": needed
        })
        total_needed_units += needed

    # 4. Proportional Truck & Warehouse Allocation
    cw = request.central_warehouse
    initial_stock = cw.current_stock
    truck_capacity = request.truck_capacity_units

    # Capacity limit is restricted by truck capacity and available warehouse stock above safety stock (stsec)
    available_warehouse_stock = max(0.0, initial_stock - cw.safety_stock)
    effective_capacity = min(truck_capacity, available_warehouse_stock)
    is_constrained = total_needed_units > effective_capacity and total_needed_units > 0.0
    
    total_shipped_units = 0.0
    allocated_sectors_list: List[SectorAllocation] = []

    for item in sectors_alloc_data:
        s = item["sector"]
        needed = item["needed"]
        
        if is_constrained:
            shipped = (needed / total_needed_units) * effective_capacity
        else:
            shipped = needed

        shipped = round(shipped, 2)
        stock_after = round(s.current_stock + shipped, 2)
        needed_rounded = round(needed, 2)

        total_shipped_units += shipped
        allocated_sectors_list.append(
            SectorAllocation(
                sector_id=s.sector_id,
                sector_name=s.sector_name,
                needed_units=needed_rounded,
                shipped_units=shipped,
                stock_after_dispatch=stock_after
            )
        )

    total_shipped_units = round(total_shipped_units, 2)
    total_needed_units = round(total_needed_units, 2)
    
    utilization_pct = 0.0
    if truck_capacity > 0:
        utilization_pct = round((total_shipped_units / truck_capacity) * 100.0, 2)

    truck_plan = TruckDispatchPlan(
        truck_capacity_units=round(truck_capacity, 2),
        total_needed_units=total_needed_units,
        total_shipped_units=total_shipped_units,
        capacity_utilization_pct=utilization_pct,
        sectors_allocated=allocated_sectors_list
    )

    # 5. Central Warehouse Procurement Analysis
    remaining_stock = round(initial_stock - total_shipped_units, 2)

    # Total network consumption across all sectors in request
    total_network_daily_consumption = round(
        sum(s.avg_daily_consumption for s in request.sectors), 2
    )

    reorder_point_rop = round(
        (total_network_daily_consumption * cw.lead_time_days) + cw.safety_stock, 2
    )

    purchase_required = remaining_stock < reorder_point_rop
    recommended_purchase_quantity = 0.0
    if purchase_required:
        recommended_purchase_quantity = round(reorder_point_rop - remaining_stock, 2)

    procurement_plan = CentralProcurementPlan(
        initial_warehouse_stock=round(initial_stock, 2),
        remaining_warehouse_stock=remaining_stock,
        total_network_daily_consumption=total_network_daily_consumption,
        reorder_point_rop=reorder_point_rop,
        purchase_required=purchase_required,
        recommended_purchase_quantity=recommended_purchase_quantity
    )

    # 6. Historical Comparison Analysis
    if last_run:
        prev_rem_stock = last_run.get("remaining_warehouse_stock", 0.0)
        prev_shipped = last_run.get("total_shipped", 0.0)
        prev_purchase = last_run.get("recommended_purchase_quantity", 0.0)

        stock_change = round(remaining_stock - prev_rem_stock, 2)
        shipped_change = round(total_shipped_units - prev_shipped, 2)
        purchase_change = round(recommended_purchase_quantity - prev_purchase, 2)

        notes_parts = []
        if stock_change > 0:
            notes_parts.append(f"Warehouse stock increased by {stock_change:.2f} units.")
        elif stock_change < 0:
            notes_parts.append(f"Warehouse stock decreased by {abs(stock_change):.2f} units.")
        else:
            notes_parts.append("Warehouse stock level unchanged.")

        if shipped_change > 0:
            notes_parts.append(f"Total dispatch volume increased by {shipped_change:.2f} units.")
        elif shipped_change < 0:
            notes_parts.append(f"Total dispatch volume decreased by {abs(shipped_change):.2f} units.")

        if purchase_change > 0:
            notes_parts.append(f"Recommended purchase quantity increased by {purchase_change:.2f} units.")
        elif purchase_change < 0:
            notes_parts.append(f"Recommended purchase quantity decreased by {abs(purchase_change):.2f} units.")

        notes_str = " ".join(notes_parts)

        historical = HistoricalComparison(
            has_previous_run=True,
            previous_run_timestamp=last_run.get("timestamp"),
            warehouse_stock_change=stock_change,
            total_shipped_change=shipped_change,
            purchase_qty_change=purchase_change,
            notes=notes_str
        )
    else:
        historical = HistoricalComparison(
            has_previous_run=False,
            previous_run_timestamp=None,
            warehouse_stock_change=0.0,
            total_shipped_change=0.0,
            purchase_qty_change=0.0,
            notes="First optimization run recorded for this product."
        )

    response = OptimizationResponse(
        dispatch_day=dispatch_day,
        product_info=ProductInfo(
            product_id=cw.product_id,
            product_name=cw.product_name
        ),
        truck_dispatch_plan=truck_plan,
        central_procurement_plan=procurement_plan,
        historical_comparison=historical,
        storage_stats=StorageStats(records_purged=records_purged)
    )

    return response
