"""
SI-6 Router - Structural Fire Resistance API Wrapper

PURPOSE:
This file wraps the SI-6 utility functions and exposes them as REST API endpoints.
It DOES NOT modify the original utility file.

WHAT IT DOES:
1. Imports functions from utils/si_6_fire_resistance_of_the_structure.py
2. Creates FastAPI endpoints that call those functions
3. Handles errors and returns formatted JSON responses

FLOW:
1. Client sends POST request to /api/si6/check
2. This router validates the request data
3. Calls the utility function from utils/
4. Returns the result as JSON

RESEARCH CONNECTION:
This endpoint validates structural fire resistance for FO-SEZ buildings.
Input: IFC model + building parameters
Output: Compliance status for beams, columns, slabs
"""

from fastapi import APIRouter, HTTPException
from app.api.models import SI6CheckRequest, SI6CheckResponse, ErrorResponse
import logging

# Import the actual compliance function from utils/
# This is the ONLY connection to your utility file
from tools.si_6_fire_resistance_of_the_structure import check_si6_compliance

# Create router
router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/check", response_model=SI6CheckResponse, responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
def check_si6_endpoint(request: SI6CheckRequest):
    """
    Check structural fire resistance compliance (SI-6 Table 3.1).
    
    **What this checks:**
    - Beams (IfcBeam)
    - Columns (IfcColumn)
    - Slabs (IfcSlab)
    - Other structural members (IfcMember)
    
    **Required input:**
    - `ifc_path`: Full path to IFC file
    - `building_use`: Typology (residential, office, etc.)
    - `evacuation_height_m`: Height in meters
    - `is_basement`: True if checking basement
    
    **Returns:**
    - Compliance status
    - List of non-compliant elements
    - Required vs actual fire resistance
    
    **Example request:**
    ```json
    {
        "ifc_path": "/home/user/projects/building.ifc",
        "building_use": "residential",
        "evacuation_height_m": 18.5,
        "is_basement": false
    }
    ```
    """
    try:
        logger.info(f"SI-6 check requested for: {request.ifc_path}")
        
        # Call the utility function (imported from utils/)
        # We are NOT modifying the original file - just calling it
        result = check_si6_compliance(
            ifc_path=request.ifc_path,
            building_use=request.building_use,
            evacuation_height_m=request.evacuation_height_m,
            is_basement=request.is_basement
        )
        
        # Transform the result into our response model
        response = SI6CheckResponse(
            status=result.get("status", "unknown"),
            building_use=result.get("building_use", request.building_use),
            evacuation_height_m=result.get("evacuation_height_m", request.evacuation_height_m),
            required_fire_resistance_minutes=result.get("required_R", 0),
            total_elements_checked=result.get("total_elements", 0),
            compliant_elements=result.get("compliant_count", 0),
            non_compliant_elements=result.get("non_compliant_count", 0),
            non_compliant_details=result.get("non_compliant", []),
            message=result.get("message")
        )
        
        logger.info(f"SI-6 check completed: {response.status}")
        return response
        
    except FileNotFoundError as e:
        logger.error(f"IFC file not found: {request.ifc_path}")
        raise HTTPException(status_code=400, detail=f"IFC file not found: {request.ifc_path}")
        
    except Exception as e:
        logger.error(f"Error in SI-6 check: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error during SI-6 check: {str(e)}")


@router.get("/info")
def si6_info():
    """
    Get information about SI-6 compliance checks.
    
    Returns metadata about what this endpoint does.
    """
    return {
        "section": "SI-6",
        "title": "Structural Fire Resistance",
        "description": "Checks structural elements (beams, columns, slabs) against CTE DB-SI Table 3.1",
        "regulations": "Spanish Technical Building Code (CTE) - Basic Document on Fire Safety (DB-SI)",
        "elements_checked": ["IfcBeam", "IfcColumn", "IfcSlab", "IfcMember"],
        "required_parameters": {
            "ifc_path": "Path to IFC file",
            "building_use": "Building typology",
            "evacuation_height_m": "Height in meters",
            "is_basement": "Boolean flag"
        },
        "output": {
            "status": "compliant | non_compliant",
            "non_compliant_elements": "List of elements failing requirements"
        }
    }
