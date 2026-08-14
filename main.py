"""
Local entrypoint wrapper.
Re-exports FastAPI app from api.main for local uvicorn execution (e.g. uvicorn main:app --reload).
"""
from api.main import app, storage_manager

__all__ = ["app", "storage_manager"]
