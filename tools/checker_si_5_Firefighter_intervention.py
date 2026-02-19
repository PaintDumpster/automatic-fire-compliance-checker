# imported libraries

import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.util.placement
import numpy as np
import json
import uuid
import os

# function

def validate_firefighter_access(ifc_window, floor_elevation, floor_evacuation_height):
    """
    Analyzes an IfcWindow for compliance with DB SI 5 Section 2 (Firefighter Access).
    
    Parameters:
    - ifc_window: The IfcWindow entity.
    - floor_elevation: Absolute Z of the finished floor (in meters).
    - floor_evacuation_height: Height of the floor relative to ground (for the 9m rule).
    """
    try:
        # Prepare per-check result structure
        checks = {
            "dimensions": {"status": None, "actual": None, "required": "Width >=0.8m and Height >=1.2m"},
            "sill_height": {"status": None, "actual": None, "required": "Sill between -0.05m and 1.20m from floor"},
            "security_obstruction": {"status": None, "actual": None, "required": "No security bars if evacuation height > 9m"}
        }

        # 1. Dimensions Check (DB SI 5-2.1.b)
        w = getattr(ifc_window, "OverallWidth", 0) / 1000.0
        h = getattr(ifc_window, "OverallHeight", 0) / 1000.0
        checks["dimensions"]["actual"] = f"{w:.2f}m x {h:.2f}m"
        if w >= 0.80 and h >= 1.20:
            checks["dimensions"]["status"] = True
        else:
            checks["dimensions"]["status"] = False

        # 2. Sill Height Check (DB SI 5-2.1.a)
        def _placement_chain_z(entity):
            opl = getattr(entity, 'ObjectPlacement', None)
            if not opl:
                return None
            z_sum = 0.0
            cur = opl
            found = False
            while cur is not None:
                rp = getattr(cur, 'RelativePlacement', None)
                if rp is not None:
                    loc = getattr(rp, 'Location', None)
                    if loc is not None:
                        coords = getattr(loc, 'Coordinates', None)
                        if coords and len(coords) >= 3:
                            z_sum += float(coords[2])
                            found = True
                cur = getattr(cur, 'PlacementRelTo', None)
            return (z_sum if found else None)

        placement = None
        try:
            placement = ifcopenshell.util.placement.get_local_placement(ifc_window)
        except Exception:
            placement = None

        if placement is None:
            try:
                placement = ifcopenshell.util.placement.get_cartesiantransformationoperator3d(ifc_window)
            except Exception:
                placement = None

        if placement is None:
            z_mm = _placement_chain_z(ifc_window)
            if z_mm is None:
                checks["sill_height"]["status"] = None
                checks["sill_height"]["actual"] = "unknown: placement not found"
            else:
                z_insertion = z_mm / 1000.0
                sill_z = z_insertion - (h / 2)
                relative_sill_height = sill_z - floor_elevation
                checks["sill_height"]["actual"] = f"sill {relative_sill_height:.2f}m from floor"
                checks["sill_height"]["status"] = (-0.05 <= relative_sill_height <= 1.20)
        else:
            z_insertion = placement[2, 3] / 1000.0
            sill_z = z_insertion - (h / 2)
            relative_sill_height = sill_z - floor_elevation
            checks["sill_height"]["actual"] = f"sill {relative_sill_height:.2f}m from floor"
            checks["sill_height"]["status"] = (-0.05 <= relative_sill_height <= 1.20)

        # 3. Security Obstruction Check (DB SI 5-2.1.c)
        psets = ifcopenshell.util.element.get_psets(ifc_window)
        has_bars = psets.get("Pset_WindowCommon", {}).get("SecurityBars", False)
        checks["security_obstruction"]["actual"] = f"security bars: {bool(has_bars)}"
        if has_bars and floor_evacuation_height > 9.0:
            checks["security_obstruction"]["status"] = False
        else:
            checks["security_obstruction"]["status"] = True

        # Overall
        # If any check explicitly False -> overall False. If any check is None (unknown) -> overall None
        statuses = [checks[k]["status"] for k in checks]
        if any(s is False for s in statuses):
            overall = False
        elif any(s is None for s in statuses):
            overall = None
        else:
            overall = True

        return overall, checks

    except Exception as e:
        return False, {"error": str(e)}


def check_firefighter_access(model, floor_evacuation_height=None):
    """
    IFCore contract wrapper for firefighter access checks.

    Signature: first arg `model` (ifcopenshell.file)
    Returns: list[dict] matching element_results schema
    """
    results = []
    for window in model.by_type("IfcWindow"):
        container = ifcopenshell.util.element.get_container(window)
        floor_z = (getattr(container, "Elevation", 0.0) / 1000.0) if container else 0.0
        evac_h = floor_evacuation_height if floor_evacuation_height is not None else floor_z

        overall, checks = validate_firefighter_access(
            ifc_window=window,
            floor_elevation=floor_z,
            floor_evacuation_height=evac_h,
        )

        # Map overall status to contract values
        if overall is True:
            check_status = "pass"
        elif overall is False:
            check_status = "fail"
        else:
            check_status = "blocked"

        # Build comment from failing checks
        comment = None
        if isinstance(checks, dict):
            reasons = []
            for k, v in checks.items():
                if v.get("status") is False:
                    reasons.append(f"{k}: {v.get('actual')}")
            if reasons:
                comment = "; ".join(reasons)

        results.append({
            "element_id": window.GlobalId,
            "element_type": "IfcWindow",
            "element_name": window.Name or f"Window {window.id()}",
            "element_name_long": (window.Name if getattr(window, 'Name', None) else None),
            "check_status": check_status,
            "actual_value": ("Compliant" if overall is True else (comment or None)),
            "required_value": "Width>=0.8m; Height>=1.2m; sill between -0.05m and 1.20m; no security bars if evac>9m",
            "comment": comment,
            "log": None,
        })

    return results

# testing space

if __name__ == "__main__":
    # prefer the sample IFC in the repository data folder
    repo_root = os.path.dirname(os.path.dirname(__file__))
    file_path = os.path.join(repo_root, "00_data", "ifc_models", "Ifc4_SampleHouse.ifc")
    
    if not os.path.exists(file_path):
        print(json.dumps({"error": "File not found"}, indent=4))
    else:
        # Load the IFC model
        model = ifcopenshell.open(file_path)
        windows = model.by_type("IfcWindow")
        
        results = []
        output_checks = []
        project_compliant = True

        for window in windows:
            # Find the BuildingStorey container to get the floor elevation
            container = ifcopenshell.util.element.get_container(window)
            # Elevation is in mm in this model, convert to meters
            floor_z = (getattr(container, "Elevation", 0.0) / 1000.0) if container else 0.0
            
            # Run the validation logic
            is_ok, message = validate_firefighter_access(
                ifc_window=window,
                floor_elevation=floor_z,
                floor_evacuation_height=floor_z # Height relative to 0.0 ground
            )
            
            if not is_ok:
                project_compliant = False

            # Record the result for this window (always)
            results.append({
                "Guid": window.GlobalId,
                "Name": window.Name,
                "Storey": container.Name if container else "Unknown",
                "IsCompliant": is_ok,
                "Details": message
            })

            # Build output entry conforming to requested schema (always)
            # `message` is now a dict of checks when validate_firefighter_access returns structured info
            overall = is_ok
            checks = message if isinstance(message, dict) else {}

            output_checks.append({
                "id": uuid.uuid4().hex,
                "check_result_id": uuid.uuid4().hex,
                "element_id": window.GlobalId or None,
                "element_type": window.is_a() if hasattr(window, 'is_a') else "IfcWindow",
                "element_name": window.Name if getattr(window, 'Name', None) else None,
                "element_name_long": (window.Name if getattr(window, 'Name', None) else None),
                "check_status": ("pass" if overall else "fail" if overall is False else "unknown"),
                "actual_value": ("Compliant" if overall else "Non-compliant" if overall is False else "Unknown"),
                "required_value": "Window dims >=0.8x1.2m; sill between -0.05m and 1.20m; no security bars if evac>9m",
                "comment": None,
                "log": None,
                "checks": checks,
                "overall_compliant": overall
            })

        # Final JSON Output: include a concise summary plus the checks array
        total = len(output_checks)
        compliant = sum(1 for c in output_checks if c.get('check_status') == 'pass')
        final_output = {
            "summary_text": f"{compliant} out of {total} compliant",
            "total_checked": total,
            "total_compliant": compliant,
            "results": output_checks
        }

        print(json.dumps(final_output, indent=4))