"""Compatibility shim so `uvicorn app:app` keeps working."""
from judging.main import app

__all__ = ["app"]
