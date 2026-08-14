from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, Any

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

try:
    from schemas import OptimizationRequest, OptimizationResponse
    from storage import StorageManager
    from optimizer import run_optimization
except ImportError:
    from .schemas import OptimizationRequest, OptimizationResponse
    from .storage import StorageManager
    from .optimizer import run_optimization

# Instantiate database storage manager
storage_manager = StorageManager()



@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events manager for FastAPI startup/shutdown."""
    # Ensure SQLite tables and indexes exist
    storage_manager._init_db()
    yield


app = FastAPI(
    title="Intelligent Inventory & Supply Chain Optimization API",
    description="API for optimizing truck dispatch allocation, reorder point analysis, and state persistence.",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/health", tags=["Health"])
def health_check() -> Dict[str, Any]:
    """Healthcheck endpoint verifying service operational status."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "supply-chain-optimizer"
    }


@app.post(
    "/api/v1/supply-chain/optimize",
    response_model=OptimizationResponse,
    status_code=status.HTTP_200_OK,
    tags=["Optimization"],
    summary="Optimize inventory allocation and procurement",
    description="Calculates truck dispatch allocation for scheduled sectors, evaluates central warehouse ROP, compares against historical runs, and purges expired records."
)
def optimize_supply_chain(request: OptimizationRequest) -> OptimizationResponse:
    try:
        product_id = request.central_warehouse.product_id.strip()
        if not product_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="product_id cannot be empty."
            )

        # 1. Automatic Cleanup: purge records older than retention_days
        records_purged = storage_manager.cleanup_old_records(request.retention_days)

        # 2. Fetch last historical run for product_id BEFORE saving current run
        last_run = storage_manager.get_last_run(product_id)

        # 3. Perform supply chain optimization calculations
        response = run_optimization(
            request=request,
            last_run=last_run,
            records_purged=records_purged
        )

        # 4. Save current run payload and metrics into SQLite storage
        current_timestamp = datetime.now(timezone.utc).isoformat()
        
        request_dict = jsonable_encoder(request)
        response_dict = jsonable_encoder(response)

        storage_manager.save_run(
            product_id=product_id,
            timestamp=current_timestamp,
            request_data=request_dict,
            response_data=response_dict,
            remaining_warehouse_stock=response.central_procurement_plan.remaining_warehouse_stock,
            total_shipped=response.truck_dispatch_plan.total_shipped_units,
            recommended_purchase_quantity=response.central_procurement_plan.recommended_purchase_quantity
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred during supply chain optimization: {str(e)}"
        )
