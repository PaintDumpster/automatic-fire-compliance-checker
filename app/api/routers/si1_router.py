"""
SI-1 Router - Interior Propagation API Wrapper

PURPOSE:
This file wraps the SI-1 utility functions and exposes them as REST API endpoints.
It DOES NOT modify the original utility file.

WHAT IT DOES:
1. Imports functions from utils/sub_si1_checker.py
2. Creates FastAPI endpoints that call those functions
3. Handles errors and returns formatted JSON responses

FLOW:
1. Client sends POST request to /api/si1/scan or /api/si1/check
2. This router validates the request data
3. Calls the utility function from utils/
4. Returns the result as JSON

RESEARCH CONNECTION:
This endpoint checks fire compartmentation (sectors) for FO-SEZ buildings.
It ensures fire sectors don't exceed maximum area limits based on building use.
"""

from fastapi import APIRouter, HTTPException
from app.api.models import (
    SI1ScanRequest, 
    SI1ScanResponse,
    SI1ComplianceRequest,
    SI1ComplianceResponse,
    ErrorResponse
)
import logging

# Import the actual compliance functions from utils/
# These are the ONLY connections to your utility files
from tools.checker_sub_si1_checker import scan_ifc_basic, check_sector_size_compliance, build_sectors
from tools.checker_SI_1_interior_propagation import load_rules_config
import ifcopenshell

# Create router
router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/scan", response_model=SI1ScanResponse, responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
def scan_ifc_endpoint(request: SI1ScanRequest):
    """
    Scan IFC file for fire safety data (spaces, doors, zones).
    
    **What this does:**
    - Extracts all IfcSpace elements
    - Extracts all IfcDoor elements
    - Reads fire zones and compartments
    - Calculates areas and volumes
    
    **Required input:**
    - `ifc_path`: Full path to IFC file
    - `preview_limit`: Max items to return (default: 200)
    
    **Returns:**
    - List of spaces with areas
    - List of doors with fire ratings
    - Data quality assessment
    
    **Example request:**
    ```json
    {
        "ifc_path": "/home/user/projects/building.ifc",
        "preview_limit": 200
    }
    ```
    """
    try:
        logger.info(f"SI-1 scan requested for: {request.ifc_path}")
        
        # Call the utility function (imported from utils/)
        scan_result = scan_ifc_basic(
            ifc_path=request.ifc_path,
            preview_limit=request.preview_limit
        )
        
        # Transform the result into our response model
        response = SI1ScanResponse(
            ifc_path=scan_result.get("ifc_path", request.ifc_path),
            total_spaces=scan_result.get("total_spaces", 0),
            total_doors=scan_result.get("total_doors", 0),
            spaces_preview=scan_result.get("spaces_preview", []),
            doors_preview=scan_result.get("doors_preview", []),
            data_quality=scan_result.get("data_quality", {}),
            message=scan_result.get("message")
        )
        
        logger.info(f"SI-1 scan completed: {response.total_spaces} spaces, {response.total_doors} doors")
        return response
        
    except FileNotFoundError as e:
        logger.error(f"IFC file not found: {request.ifc_path}")
        raise HTTPException(status_code=400, detail=f"IFC file not found: {request.ifc_path}")
        
    except Exception as e:
        logger.error(f"Error in SI-1 scan: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error during SI-1 scan: {str(e)}")


@router.post("/check", response_model=SI1ComplianceResponse, responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
def check_sectors_endpoint(request: SI1ComplianceRequest):
    """
    Check fire sector size compliance (SI-1).
    
    **What this checks:**
    - Maximum allowed area per fire sector
    - Fire separation between sectors
    - Special risk rooms
    
    **Required input:**
    - `ifc_path`: Full path to IFC file
    - `building_use`: Typology (residential, office, etc.)
    - `sprinklers`: Does building have sprinklers? (boolean)
    - `config_path`: Optional path to rules JSON (uses default if not provided)
    
    **Returns:**
    - Compliance status per sector
    - List of non-compliant sectors
    - Actual vs allowed areas
    
    **Example request:**
    ```json
    {
        "ifc_path": "/home/user/projects/building.ifc",
        "building_use": "residential",
        "sprinklers": false
    }
    ```
    """
    try:
        logger.info(f"SI-1 compliance check requested for: {request.ifc_path}")
        
        # Load configuration
        if request.config_path:
            rules = load_rules_config(request.config_path)
        else:
            # Use default config path
            import os
            default_config = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "data_push",
                "rulesdb_si_si1_rules.json.json"
            )
            rules = load_rules_config(default_config) if os.path.exists(default_config) else {}
        
        # Override with request parameters
        if not rules.get("project_defaults"):
            rules["project_defaults"] = {}
        rules["project_defaults"]["building_use"] = request.building_use
        rules["project_defaults"]["sprinklers"] = request.sprinklers
        
        # Scan IFC
        scan_result = scan_ifc_basic(request.ifc_path, preview_limit=1000)
        
        # Open IFC file for sector building
        ifc_file = ifcopenshell.open(request.ifc_path)
        
        # Build sectors
        sectors, used_fallback = build_sectors(ifc_file, scan_result, rules)
        
        # Check compliance
        compliance_result = check_sector_size_compliance(
            sectors=sectors,
            rules=rules,
            building_use=request.building_use,
            sprinklers=request.sprinklers,
            used_fallback=used_fallback
        )
        
        # Extract non-compliant sectors
        non_compliant = []
        for sector_id, sector_data in sectors.items():
            if sector_data.get("compliant") == False:
                non_compliant.append({
                    "sector_id": sector_id,
                    "actual_area_m2": sector_data.get("total_area_m2", 0),
                    "allowed_area_m2": sector_data.get("allowed_area_m2", 0),
                    "reason": sector_data.get("reason", "Area exceeds limit")
                })
        
        # Transform the result into our response model
        response = SI1ComplianceResponse(
            status=compliance_result.get("status", "unknown"),
            sectors=sectors,
            compliance_summary=compliance_result,
            non_compliant_sectors=non_compliant,
            message=compliance_result.get("message")
        )
        
        logger.info(f"SI-1 compliance check completed: {response.status}")
        return response
        
    except FileNotFoundError as e:
        logger.error(f"IFC file not found: {request.ifc_path}")
        raise HTTPException(status_code=400, detail=f"IFC file not found: {request.ifc_path}")
        
    except Exception as e:
        logger.error(f"Error in SI-1 compliance check: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error during SI-1 compliance check: {str(e)}")


@router.get("/info")
def si1_info():
    """
    Get information about SI-1 compliance checks.
    
    Returns metadata about what this endpoint does.
    """
    return {
        "section": "SI-1",
        "title": "Interior Propagation",
        "description": "Checks fire compartmentation and sector size limits",
        "regulations": "Spanish Technical Building Code (CTE) - Basic Document on Fire Safety (DB-SI)",
        "what_it_checks": [
            "Maximum fire sector area based on building use",
            "Fire door ratings and locations",
            "Special risk rooms",
            "Fire separation between sectors"
        ],
        "required_parameters": {
            "ifc_path": "Path to IFC file",
            "building_use": "Building typology (residential, office, etc.)",
            "sprinklers": "Boolean - does building have sprinklers?"
        },
        "output": {
            "status": "compliant | non_compliant",
            "sectors": "Dictionary of fire sectors with areas",
            "non_compliant_sectors": "List of sectors exceeding limits"
        }
    }
