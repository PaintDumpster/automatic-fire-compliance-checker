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
from app.api.models import SI3CheckRequest, SI3CheckResponse, SI3MaxRouteRequest, SI3MaxRouteResponse, ErrorResponse
import logging
import os

# Import the actual compliance functions from utils/
# These are the ONLY connections to your utility file
from tools.SI_3_Evacuation_of_occupants import (
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


@router.post("/check-max-route", response_model=SI3MaxRouteResponse, responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
def check_si3_max_route_endpoint(request: SI3MaxRouteRequest):
    """
    Check maximum evacuation routes using advanced grid-based pathfinding (SI-3).
    
    **What this does:**
    This is an advanced evacuation route checker that uses Dijkstra pathfinding on a grid
    to calculate actual walking distances from any point in a space to exit doors.
    
    **What this checks:**
    - Maximum evacuation route distance per space
    - Single exit vs multiple exit limits
    - Compliance with CTE DB-SI SI3.3 route length requirements
    - Optional 25% increase for buildings with automatic fire extinction
    
    **Required input:**
    - `ifc_path`: Full path to IFC file
    - `typology`: Building typology (e.g., "Residencial Vivienda", "Administrativo")
    - `has_auto_extinction`: Whether building has automatic fire suppression (default: false)
    - `rules_json_path`: Path to regulation_rules.json (optional)
    
    **Returns:**
    - Maximum route distance found (worst case)
    - Compliance status per space
    - Number of compliant/non-compliant/blocked spaces
    - Detailed results for each space
    
    **Example request:**
    ```json
    {
        "ifc_path": "/path/to/building.ifc",
        "typology": "Residencial Vivienda",
        "has_auto_extinction": false,
        "rules_json_path": "/path/to/regulation_rules.json"
    }
    ```
    
    **Research Context:**
    This advanced checker calculates real evacuation paths using spatial analysis.
    For FO-SEZ projects, it provides precise route validation accounting for actual
    building geometry, door positions, and space connectivity.
    
    **Note:** This is computationally intensive and may take longer than the basic SI-3 check.
    """
    try:
        # Import the max route functions - only import when needed
        from tools.SI_3_Evacuation_of_occupants_max_route import (
            build_space_door_maps_enhanced,
            get_shape_mesh,
            footprint_from_space_mesh,
            rasterize_polygon,
            is_exit_door,
            build_door_cells,
            build_door_graph,
            add_level_bridge_edges,
            grid_multisource_dijkstra,
            compliance_check_evacuation,
            load_regulation_rules,
            world_to_cell,
            snap_cell_to_walkable
        )
        import ifcopenshell
        import numpy as np
        
        # Validate file exists
        if not os.path.exists(request.ifc_path):
            raise HTTPException(
                status_code=400,
                detail=f"IFC file not found: {request.ifc_path}"
            )
        
        logger.info(f"Starting SI-3 max route check for: {request.ifc_path}")
        
        # Constants (from the original file)
        GRID_RES = 0.20
        ALLOW_DIAGONALS = True
        SNAP_MAX_RADIUS_CELLS = 10
        
        # Load IFC model
        model = ifcopenshell.open(request.ifc_path)
        spaces = model.by_type("IfcSpace")
        doors = model.by_type("IfcDoor")
        
        if not spaces:
            raise HTTPException(status_code=400, detail="No spaces found in IFC file")
        
        logger.info(f"Found {len(spaces)} spaces and {len(doors)} doors")
        
        # Build space-door relationships
        door_to_spaces, space_to_doors = build_space_door_maps_enhanced(model)
        
        # Create grids for each space
        space_polys = {}
        space_grids = {}
        for sp in spaces:
            sid = sp.GlobalId
            try:
                verts, faces = get_shape_mesh(sp)
                poly = footprint_from_space_mesh(verts, faces)
                space_polys[sid] = poly
                if poly is not None:
                    grid, origin, res = rasterize_polygon(poly, GRID_RES)
                    space_grids[sid] = (grid, origin, res)
            except Exception as e:
                logger.warning(f"Could not process space {sp.Name} ({sid}): {e}")
                space_polys[sid] = None
        
        logger.info(f"Created grids for {len(space_grids)}/{len(spaces)} spaces")
        
        # Identify exit doors
        exit_door_ids = {d.GlobalId for d in doors if is_exit_door(d)}
        logger.info(f"Detected {len(exit_door_ids)} exit doors")
        
        if len(exit_door_ids) == 0:
            raise HTTPException(status_code=400, detail="No exit doors found in IFC file")
        
        # Build door cells and graph
        portal_cells, door_any_cell, door_xyz, door_level = build_door_cells(
            model, space_polys, space_grids, space_to_doors
        )
        door_graph = build_door_graph(space_grids, portal_cells, space_to_doors)
        
        # Add vertical connectivity (stairs)
        door_graph = add_level_bridge_edges(
            model, door_graph, doors, door_xyz, door_level, space_to_doors
        )
        
        # Calculate distances from doors to exits using Dijkstra
        from collections import defaultdict
        import heapq
        
        dist = {}
        for did in exit_door_ids:
            dist[did] = 0.0
        
        # Dijkstra on door graph
        pq = [(0.0, did) for did in exit_door_ids]
        heapq.heapify(pq)
        visited = set()
        
        while pq:
            d, u = heapq.heappop(pq)
            if u in visited:
                continue
            visited.add(u)
            dist[u] = d
            for (v, edge_cost) in door_graph.get(u, []):
                if v not in visited:
                    heapq.heappush(pq, (d + edge_cost, v))
        
        # For each space, calculate max distance to nearest exit
        per_space_data = []
        warn_count = 0
        
        for sp in spaces:
            sid = sp.GlobalId
            sp_name = sp.Name or f"Space #{sp.id()}"
            
            if sid not in space_grids:
                warn_count += 1
                continue
            
            grid, origin, res = space_grids[sid]
            sp_doors = space_to_doors.get(sid, [])
            
            # Get doors that have a path to exit
            seeded = [(did, dist.get(did, float('inf'))) for did in sp_doors if did in dist]
            
            if not seeded:
                warn_count += 1
                continue
            
            # Multi-source Dijkstra from doors
            seeds = []
            for did, base_cost in seeded:
                if did not in door_any_cell:
                    continue
                cy, cx = door_any_cell[did]
                seeds.append((cy, cx, base_cost))
            
            if not seeds:
                warn_count += 1
                continue
            
            cost_map = grid_multisource_dijkstra(grid, res, seeds, ALLOW_DIAGONALS)
            
            # Find maximum distance in this space
            max_dist = 0.0
            for iy in range(cost_map.shape[0]):
                for ix in range(cost_map.shape[1]):
                    c = cost_map[iy, ix]
                    if c < float('inf'):
                        max_dist = max(max_dist, c)
            
            per_space_data.append((max_dist, sp_name, sid))
        
        # Sort by worst distance
        per_space_data.sort(reverse=True, key=lambda x: x[0])
        
        if not per_space_data:
            raise HTTPException(
                status_code=400,
                detail="Could not calculate evacuation routes for any spaces"
            )
        
        max_route = per_space_data[0][0]
        logger.info(f"Maximum evacuation route: {max_route:.2f} m")
        
        # Run compliance check
        if request.rules_json_path and os.path.exists(request.rules_json_path):
            all_rules = load_regulation_rules(request.rules_json_path)
        else:
            # Try default location
            repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            default_rules = os.path.join(repo_root, "data_push", "regulation_rules.json")
            if os.path.exists(default_rules):
                all_rules = load_regulation_rules(default_rules)
            else:
                raise HTTPException(
                    status_code=400,
                    detail="regulation_rules.json not found. Please provide rules_json_path."
                )
        
        spaces_by_id = {sp.GlobalId: sp for sp in spaces}
        
        results = compliance_check_evacuation(
            per_space_data=per_space_data,
            spaces_by_id=spaces_by_id,
            n_exit_doors=len(exit_door_ids),
            all_rules=all_rules,
            typology=request.typology or "",
            has_auto_extinction=request.has_auto_extinction
        )
        
        # Calculate statistics
        n_compliant = sum(1 for r in results if r["check_status"] == "pass")
        n_non_compliant = sum(1 for r in results if r["check_status"] == "fail")
        n_blocked = sum(1 for r in results if r["check_status"] == "blocked")
        
        if n_non_compliant > 0:
            status = "non_compliant"
        elif n_blocked > 0:
            status = "partial"
        else:
            status = "compliant"
        
        logger.info(f"SI-3 max route check complete: {n_compliant}/{len(results)} spaces compliant")
        
        return SI3MaxRouteResponse(
            status=status,
            max_route_distance_m=round(max_route, 2),
            total_spaces=len(results),
            compliant_spaces=n_compliant,
            non_compliant_spaces=n_non_compliant,
            blocked_spaces=n_blocked,
            results=results,
            message=f"Max route calculation complete: {n_compliant} compliant, {n_non_compliant} non-compliant, {n_blocked} blocked (max distance: {max_route:.2f}m)"
        )
        
    except HTTPException:
        raise
    except ImportError as e:
        logger.error(f"Import error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to import max route functions: {str(e)}"
        )
    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        raise HTTPException(status_code=400, detail=f"File error: {str(e)}")
    except Exception as e:
        logger.error(f"SI-3 max route check failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to check max evacuation routes: {str(e)}"
        )
