"""
Routers Package - FastAPI Route Handlers

This package contains all the API routers that wrap utility functions.
Each router file corresponds to one compliance section (SI-1, SI-3, SI-6, etc.)
"""

# Make routers easily importable
from . import si1_router
from . import si3_router
from . import si4_router
from . import si6_router

__all__ = ["si1_router", "si3_router", "si4_router", "si6_router"]
