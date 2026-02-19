"""
SI-3 Router - Evacuation API Wrapper

PURPOSE:
This file wraps the SI-3 utility functions and exposes them as REST API endpoints.
It DOES NOT modify the original utility file.

WHAT IT DOES:
1. Imports functions from utils/SI_3_Evacuation_of_occupants.py
2. Creates FastAPI endpoints that call those functions
3. Handles errors and returns formatted JSON responses

FLOW:
1. Client sends POST request to /api/si3/check
2. This router validates the request data
3. Calls utility functions: detect typology → get rules → calculate occupancy → evaluate compliance
4. Returns the result as JSON

RESEARCH CONNECTION:
This endpoint validates evacuation requirements for FO-SEZ buildings.
Input: IFC model + building parameters
Output: Occupancy, exit requirements, compliance status
"""

from fastapi import APIRouter, HTTPException
from app.api.models import SI3CheckRequest, SI3CheckResponse, ErrorResponse
import logging

# Import the actual compliance functions from utils/
# These are the ONLY connections to your utility file
from utils.SI_3_Evacuation_of_occupants import (
    detectar_tipologia,
    obtener_reglas,
    calcular_ocupacion,
    evaluar_cumplimiento
)

# Create router
router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/check", response_model=SI3CheckResponse, responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
def check_si3_endpoint(request: SI3CheckRequest):
    """
    Check evacuation compliance (SI-3).
    
    **What this checks:**
    - Maximum occupancy with single exit
    - Evacuation height limits
    - Exit width requirements
    - Stair protection requirements
    
    **Required input:**
    - `ifc_path`: Full path to IFC file
    - `building_typology`: Optional override (auto-detected if not provided)
    - `language`: Language for detection (es, en, fr, auto)
    
    **Returns:**
    - Detected building typology
    - Total occupancy calculation
    - Evacuation height (ascending/descending)
    - Compliance status for each check
    - List of compliance issues (if any)
    
    **Example request:**
    ```json
    {
        "ifc_path": "/home/user/projects/building.ifc",
        "building_typology": null,
        "language": "auto"
    }
    ```
    
    **Workflow:**
    1. Detect building typology from IFC (or use override)
    2. Load applicable CTE rules for that typology
    3. Calculate occupancy based on space areas and density
    4. Evaluate compliance against rules
    5. Return detailed results
    """
    try:
        logger.info(f"SI-3 check requested for: {request.ifc_path}")
        
        # Step 1: Detect or use provided typology
        if request.building_typology:
            tipologia = request.building_typology
            logger.info(f"Using provided typology: {tipologia}")
        else:
            logger.info("Auto-detecting typology...")
            tipologia_result = detectar_tipologia(request.ifc_path, language=request.language)
            tipologia = tipologia_result.get("tipologia_detectada", "Residencial Vivienda")
            logger.info(f"Detected typology: {tipologia}")
        
        # Step 2: Get rules for this typology
        reglas = obtener_reglas(tipologia, regulation_id="CTE_DBSI_SI3")
        
        # Step 3: Calculate occupancy
        logger.info("Calculating occupancy...")
        ocupacion = calcular_ocupacion(
            ifc_path=request.ifc_path,
            tipologia=tipologia,
            reglas=reglas,
            language=request.language,
            regulation_id="CTE_DBSI_SI3"
        )
        
        # Step 4: Evaluate compliance
        logger.info("Evaluating compliance...")
        verificaciones = evaluar_cumplimiento(ocupacion, reglas)
        
        # Step 5: Analyze results
        total_checks = len(verificaciones)
        passing_checks = sum(1 for v in verificaciones if "CUMPLE" in v.get("resultado", ""))
        failing_checks = total_checks - passing_checks
        
        compliance_issues = [
            {
                "rule": v.get("regla", ""),
                "building_value": v.get("valor_edificio", ""),
                "limit": v.get("limite", ""),
                "result": v.get("resultado", "")
            }
            for v in verificaciones
            if "NO CUMPLE" in v.get("resultado", "")
        ]
        
        # Determine overall status
        status = "compliant" if failing_checks == 0 else "non_compliant"
        
        # Extract exit analysis from verificaciones
        exit_analysis = {
            "total_checks": total_checks,
            "passing_checks": passing_checks,
            "failing_checks": failing_checks,
            "checks_detail": verificaciones
        }
        
        # Build response
        response = SI3CheckResponse(
            status=status,
            ifc_path=request.ifc_path,
            detected_typology=tipologia,
            total_spaces=len(ocupacion.get("espacios_detalle", [])),
            total_occupants=ocupacion.get("ocupacion_total", 0),
            evacuation_summary={
                "descending_height_m": ocupacion.get("altura_evacuacion_descendente_m", 0),
                "ascending_height_m": ocupacion.get("altura_evacuacion_ascendente_m", 0),
                "occupancy_per_floor": ocupacion.get("ocupacion_por_planta", {}),
                "total_area_m2": ocupacion.get("superficie_total", 0)
            },
            exit_analysis=exit_analysis,
            compliance_issues=compliance_issues,
            message=f"Checked {total_checks} rules. {passing_checks} passed, {failing_checks} failed."
        )
        
        logger.info(f"SI-3 check completed: {response.status}")
        return response
        
    except FileNotFoundError as e:
        logger.error(f"IFC file not found: {request.ifc_path}")
        raise HTTPException(status_code=400, detail=f"IFC file not found: {request.ifc_path}")
        
    except Exception as e:
        logger.error(f"Error in SI-3 check: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error during SI-3 check: {str(e)}")


@router.get("/info")
def si3_info():
    """
    Get information about SI-3 compliance checks.
    
    Returns metadata about what this endpoint does.
    """
    return {
        "section": "SI-3",
        "title": "Evacuation of Occupants",
        "description": "Checks evacuation requirements based on occupancy and building height",
        "regulations": "Spanish Technical Building Code (CTE) - Basic Document on Fire Safety (DB-SI)",
        "what_it_checks": [
            "Maximum occupancy with single exit",
            "Evacuation height limits (ascending/descending)",
            "Exit width requirements based on occupancy",
            "Stair protection requirements",
            "Dead-end corridor lengths"
        ],
        "workflow": [
            "1. Detect building typology from IFC metadata",
            "2. Load CTE rules for that typology",
            "3. Calculate occupancy from space areas and density",
            "4. Evaluate compliance against regulations",
            "5. Return detailed results"
        ],
        "required_parameters": {
            "ifc_path": "Path to IFC file",
            "building_typology": "Optional override (auto-detected if null)",
            "language": "Language code for detection (es, en, fr, auto)"
        },
        "output": {
            "status": "compliant | non_compliant",
            "total_occupants": "Calculated total occupants",
            "compliance_issues": "List of failing checks"
        }
    }
