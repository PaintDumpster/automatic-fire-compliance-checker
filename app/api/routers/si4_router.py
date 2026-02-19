"""
SI-4 Router - Fire Protection Installation API Wrapper

PURPOSE:
This file wraps the SI-4 utility functions and exposes them as REST API endpoints.
It DOES NOT modify the original utility file.

WHAT IT DOES:
1. Imports functions from utils/SI_4_installation_of_protection.py
2. Creates FastAPI endpoints that call those functions
3. Handles errors and returns formatted JSON responses

FLOW:
1. Client sends POST request to /api/si4/check
2. This router validates the request data
3. Calls the utility function from utils/
4. Returns the result as JSON

RESEARCH CONNECTION:
This endpoint validates fire protection installations for FO-SEZ buildings.
Input: IFC model + building configuration
Output: Compliance status for fire detection, extinguishing systems, etc.
"""

from fastapi import APIRouter, HTTPException
from app.api.models import SI4CheckRequest, SI4CheckResponse, ErrorResponse
import logging

# Import the actual compliance function from utils/
# This is the ONLY connection to your utility file
from tools.SI_4_installation_of_protection import check_si4_administrativo

# Create router
router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/check", response_model=SI4CheckResponse, responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
def check_si4_endpoint(request: SI4CheckRequest):
    """
    Check fire protection installation compliance (SI-4).
    
    **What this checks:**
    - Fire detection systems
    - Fire extinguishing systems (hydrants, sprinklers, etc.)
    - Manual alarm systems
    - Emergency lighting
    - Building use compliance
    
    **Required input:**
    - `ifc_path`: Full path to IFC file
    - `config`: Configuration dictionary with:
        - `building_use_target`: Expected building use (e.g., "administrative")
        - `rules`: List of rule configurations
        - `actions_by_rule`: Remediation actions per rule (optional)
    
    **Returns:**
    - Compliance status (PASS/FAIL)
    - List of failing rules
    - Non-compliant elements
    - Missing data warnings
    
    **Example request:**
    ```json
    {
        "ifc_path": "/home/user/projects/building.ifc",
        "config": {
            "building_use_target": "administrative",
            "rules": [
                {
                    "id": "SI4-01",
                    "check_key": "fire_detection",
                    "requirement": "Fire detection system required",
                    "applies": "always",
                    "required_count": 1
                }
            ]
        }
    }
    ```
    """
    try:
        logger.info(f"SI-4 check requested for: {request.ifc_path}")
        
        # Call the utility function (imported from utils/)
        result = check_si4_administrativo(
            ifc_path=request.ifc_path,
            config=request.config
        )
        
        # Check for errors
        if "error" in result:
            logger.error(f"SI-4 check error: {result['error']}")
            raise HTTPException(status_code=400, detail=result["error"])
        
        # Extract results
        rules = result.get("rules", [])
        fail_count = sum(1 for r in rules if r.get("status") == "FAIL")
        pass_count = sum(1 for r in rules if r.get("status") == "PASS")
        manual_count = sum(1 for r in rules if r.get("status") == "MANUAL_REVIEW")
        
        # Get non-compliant details
        non_compliant = [
            {
                "rule_id": r.get("rule_id"),
                "requirement": r.get("requirement"),
                "check_key": r.get("check_key"),
                "required_count": r.get("required_count"),
                "found_count": r.get("found_count"),
                "status": r.get("status"),
                "note": r.get("note", "")
            }
            for r in rules
            if r.get("status") in ["FAIL", "MANUAL_REVIEW"]
        ]
        
        # Transform into response model
        response = SI4CheckResponse(
            status=result.get("overall_status", "UNKNOWN"),
            file_name=result.get("file_name", ""),
            building_use=result.get("building_use", ""),
            complies=result.get("complies", False),
            total_rules_checked=len(rules),
            passing_rules=pass_count,
            failing_rules=fail_count,
            manual_review_required=manual_count,
            non_compliant_details=non_compliant,
            missing_data=result.get("missing_data", []),
            message=f"Checked {len(rules)} rules. {pass_count} passed, {fail_count} failed, {manual_count} require manual review."
        )
        
        logger.info(f"SI-4 check completed: {response.status}")
        return response
        
    except FileNotFoundError as e:
        logger.error(f"IFC file not found: {request.ifc_path}")
        raise HTTPException(status_code=400, detail=f"IFC file not found: {request.ifc_path}")
        
    except Exception as e:
        logger.error(f"Error in SI-4 check: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error during SI-4 check: {str(e)}")


@router.get("/info")
def si4_info():
    """
    Get information about SI-4 compliance checks.
    
    Returns metadata about what this endpoint does.
    """
    return {
        "section": "SI-4",
        "title": "Fire Protection Installations",
        "description": "Checks required fire protection systems based on building use and characteristics",
        "regulations": "Spanish Technical Building Code (CTE) - Basic Document on Fire Safety (DB-SI)",
        "what_it_checks": [
            "Fire detection systems",
            "Fire extinguishing systems (hydrants, sprinklers)",
            "Manual alarm systems",
            "Emergency lighting",
            "Building use compliance (administrative, residential, etc.)"
        ],
        "required_parameters": {
            "ifc_path": "Path to IFC file",
            "config": {
                "building_use_target": "Expected building use",
                "rules": "List of rule configurations to check"
            }
        },
        "output": {
            "status": "PASS | FAIL | PASS_WITH_WARNINGS",
            "complies": "Boolean - overall compliance",
            "non_compliant_details": "List of failing/manual review rules"
        }
    }
