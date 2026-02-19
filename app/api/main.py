"""
FastAPI Application - Fire Compliance Checker

PURPOSE:
This is the main entry point for the FastAPI web service.
It wraps all the compliance checking utilities without modifying them.

WHAT IT DOES:
1. Creates a FastAPI application instance
2. Registers all routers (SI-1, SI-3, SI-6, etc.)
3. Sets up CORS for web browser access
4. Provides health check endpoint

HOW TO RUN:
    uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000

RESEARCH CONNECTION:
This API exposes all fire compliance checks for FO-SEZ spatial planning.
External tools (QGIS, web dashboards, etc.) can call these endpoints
to validate building designs against Spanish fire safety regulations.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routers import si1_router, si3_router, si4_router, si5_router, si6_router

# Create FastAPI application
app = FastAPI(
    title="Fire Compliance Checker API",
    description="Automated fire safety compliance checks for IFC building models (Spanish CTE DB-SI)",
    version="1.0.0",
    docs_url="/docs",  # Swagger UI at /docs
    redoc_url="/redoc"  # ReDoc at /redoc
)

# Enable CORS (Cross-Origin Resource Sharing)
# This allows web browsers to call your API from different domains
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change to specific domains in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check endpoint
@app.get("/")
def root():
    """
    Root endpoint - confirms API is running.
    """
    return {
        "message": "Fire Compliance Checker API is running",
        "status": "healthy",
        "endpoints": {
            "docs": "/docs",
            "si1": "/api/si1",
            "si3": "/api/si3",
            "si4": "/api/si4",
            "si5": "/api/si5",
            "si6": "/api/si6"
        }
    }


@app.get("/health")
def health_check():
    """
    Health check endpoint for monitoring tools.
    """
    return {"status": "healthy"}


# Register routers - each router handles one compliance section
app.include_router(si1_router.router, prefix="/api/si1", tags=["SI-1 Interior Propagation"])
app.include_router(si3_router.router, prefix="/api/si3", tags=["SI-3 Evacuation"])
app.include_router(si4_router.router, prefix="/api/si4", tags=["SI-4 Protection Installations"])
app.include_router(si5_router.router, prefix="/api/si5", tags=["SI-5 Firefighter Intervention"])
app.include_router(si6_router.router, prefix="/api/si6", tags=["SI-6 Structural Resistance"])
