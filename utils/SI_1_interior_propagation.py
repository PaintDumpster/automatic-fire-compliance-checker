"""imported libraries
import pandas
import networkx
import pydantic
import tqdm""" 



#Check all the files 
#1.Detect all spaces (rooms)
#1.Fire compartment identification (Sectores de incendio)
   #A. How compartments are defined
   #B. Sector metrics
   #C.Sector size compliance

#2.Special Risk Rooms (Locales de riesgo especial)
   #check name, objectType or Zones 
   #compute area and volume 
   #check with the requirements of the code

#3.Openings (Aberturas) 
   # Doors between sectors must have specific fire resistance (EI, REI)
   # Service penetrations must maintain fire resistance.
   # for doors in: Special risk room - other space cehck:
      # if the door is EI, REI or EI2h, then it is compliant
      # if the door is not compliant, check if it has a fire damper (cortafuegos) and if it is compliant with the code.

#4.Interior surface reaction-to-fire (finishes)
   #  walls/ceilings/floors in certain areas require Euroclass reaction-to-fire ratings
   # Read material classifications if present. If not present: data completeness warning only


"""
IFC Fire Safety Scanner Module (SI 1) - BASE SCANNER

Purpose:
- Provide robust, reusable extraction of IFC data needed for SI1 checks.
- This module DOES NOT implement full SI1 regulation logic yet.
- It extracts:
  - Spaces (+ area, volume, storey, zones)
  - Doors (+ fire rating if available)
  - Walls, storeys counts
  - Data quality metrics
  - Errors safely (never crashes the app)

Outputs are JSON-serializable dicts.
"""


from typing import Dict, List, Any, Optional, Tuple, Iterable
from pathlib import Path

import ifcopenshell


# ============================================================
# SECTION A — SAFE HELPERS
# ============================================================

def _safe_get_attribute(element: ifcopenshell.entity_instance, attr_name: str) -> Optional[Any]:
    """Safely retrieve an attribute from an IFC element."""
    try:
        if hasattr(element, attr_name):
            return getattr(element, attr_name)
    except Exception:
        pass
    return None


def get_pset_value(
    element: ifcopenshell.entity_instance,
    pset_name: str,
    prop_name: str
) -> Optional[Any]:
    """
    Safely retrieve a property value from an element's property set.
    Uses ifcopenshell.util.element.get_psets when available.
    """
    try:
        from ifcopenshell.util.element import get_psets  # type: ignore
        psets = get_psets(element) or {}
        pset = psets.get(pset_name, None)
        if isinstance(pset, dict):
            return pset.get(prop_name, None)
    except Exception:
        pass
    return None


def _to_float(x: Any) -> Optional[float]:
    """Best-effort float conversion."""
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


# ============================================================
# SECTION B — STOREY LOOKUP (FAST)
# ============================================================

def build_element_storey_map(ifc_file: ifcopenshell.file) -> Dict[int, str]:
    """
    Build a fast map { element_id_int : storey_name } using IfcRelContainedInSpatialStructure.
    Avoids scanning all relations per element (performance fix for big IFCs).
    """
    mapping: Dict[int, str] = {}
    try:
        rels = ifc_file.by_type("IfcRelContainedInSpatialStructure") or []
        for rel in rels:
            container = getattr(rel, "RelatingStructure", None)
            if not container or not container.is_a("IfcBuildingStorey"):
                continue
            storey_name = getattr(container, "Name", None) or "Unknown"
            related = getattr(rel, "RelatedElements", None) or []
            for el in related:
                try:
                    mapping[el.id()] = storey_name
                except Exception:
                    continue
    except Exception:
        pass
    return mapping


def get_storey_name(element: ifcopenshell.entity_instance, storey_map: Dict[int, str]) -> Optional[str]:
    """Get storey name from cached map."""
    try:
        return storey_map.get(element.id(), None)
    except Exception:
        return None


# ============================================================
# SECTION C — QUANTITIES (AREA/VOLUME) THAT ACTUALLY WORK
# ============================================================

def get_element_quantities(element: ifcopenshell.entity_instance) -> Dict[str, float]:
    """
    Extract quantities (IfcElementQuantity) from IsDefinedBy relations.

    Returns dict keyed by quantity Name lowercased:
      - IfcQuantityArea  -> AreaValue
      - IfcQuantityVolume -> VolumeValue
    """
    out: Dict[str, float] = {}
    try:
        for rel in getattr(element, "IsDefinedBy", []) or []:
            if not rel.is_a("IfcRelDefinesByProperties"):
                continue
            prop_def = getattr(rel, "RelatingPropertyDefinition", None)
            if not prop_def or not prop_def.is_a("IfcElementQuantity"):
                continue
            for q in getattr(prop_def, "Quantities", []) or []:
                qname = (getattr(q, "Name", "") or "").strip().lower()
                if not qname:
                    continue
                if q.is_a("IfcQuantityArea"):
                    val = _to_float(getattr(q, "AreaValue", None))
                    if val is not None:
                        out[qname] = val
                elif q.is_a("IfcQuantityVolume"):
                    val = _to_float(getattr(q, "VolumeValue", None))
                    if val is not None:
                        out[qname] = val
    except Exception:
        pass
    return out


def get_space_area_m2(space: ifcopenshell.entity_instance) -> Optional[float]:
    qs = get_element_quantities(space)
    # Common IFC names (varies by exporter)
    candidates = (
        "netfloorarea", "grossfloorarea", "area",
        "net area", "gross area", "floorarea", "floor area"
    )
    for k in candidates:
        if k in qs:
            return qs[k]
    return None


def get_space_volume_m3(space: ifcopenshell.entity_instance) -> Optional[float]:
    qs = get_element_quantities(space)
    candidates = ("netvolume", "grossvolume", "volume", "spacevolume", "space volume")
    for k in candidates:
        if k in qs:
            return qs[k]
    return None


# ============================================================
# SECTION D — ZONES (IfcZone membership)
# ============================================================

def get_space_zones(space: ifcopenshell.entity_instance) -> List[str]:
    """
    Get zone names for a space via IfcRelAssignsToGroup where group is IfcZone.
    """
    zones: List[str] = []
    try:
        for rel in getattr(space, "HasAssignments", []) or []:
            if not rel.is_a("IfcRelAssignsToGroup"):
                continue
            grp = getattr(rel, "RelatingGroup", None)
            if grp and grp.is_a("IfcZone"):
                zname = getattr(grp, "Name", None) or getattr(grp, "LongName", None) or "UnnamedZone"
                zones.append(str(zname))
    except Exception:
        pass
    return zones

# ============================================================
# SECTION E — DOOR FIRE RATING (INSTANCE + TYPE, ROBUST)
# ============================================================

def _extract_fire_rating_from_pset(element) -> Optional[str]:
    """
    Extract FireRating from an IfcPropertySet manually,
    ensuring we read NominalValue correctly.
    """
    try:
        for rel in getattr(element, "IsDefinedBy", []) or []:
            if not rel.is_a("IfcRelDefinesByProperties"):
                continue

            pset = getattr(rel, "RelatingPropertyDefinition", None)
            if not pset or not pset.is_a("IfcPropertySet"):
                continue

            if pset.Name != "Pset_DoorCommon":
                continue

            for prop in getattr(pset, "HasProperties", []) or []:
                if prop.Name == "FireRating":
                    if hasattr(prop, "NominalValue") and prop.NominalValue:
                        value = prop.NominalValue.wrappedValue
                        # Filter useless placeholder values
                        if value and str(value).strip().lower() not in ["fire rating", "none", ""]:
                            return str(value).strip()
    except Exception:
        pass

    return None


def get_door_fire_rating(door) -> Optional[str]:
    """
    Retrieve fire rating from:
    1. Door instance property set
    2. Door type property set (IfcRelDefinesByType)

    Returns:
        Fire rating string (e.g. "EI60", "REI90") or None
    """

    # 1️⃣ Check instance property set
    fr = _extract_fire_rating_from_pset(door)
    if fr:
        return fr

    # 2️⃣ Check type property set
    try:
        for rel in getattr(door, "IsDefinedBy", []) or []:
            if rel.is_a("IfcRelDefinesByType"):
                door_type = getattr(rel, "RelatingType", None)
                if door_type:
                    fr_type = _extract_fire_rating_from_pset(door_type)
                    if fr_type:
                        return fr_type
    except Exception:
        pass

    return None


# ============================================================
# SECTION F — MAIN SCAN API
# ============================================================

def scan_ifc_basic(ifc_path: str, preview_limit: int = 20) -> Dict[str, Any]:
    """
    Scan an IFC file and extract basic fire-safety relevant information.

    Returns JSON-serializable dict:
    - file_name
    - counts: IfcSpace, IfcDoor, IfcWall, IfcBuildingStorey
    - spaces: first N spaces with guid, name, long_name, object_type, storey_name, area_m2, volume_m3, zones
    - doors: first N doors with guid, name, predefined_type, fire_rating
    - data_quality flags
    - error (optional)
    """
    file_name = Path(ifc_path).name if ifc_path else "unknown"

    # Open IFC safely
    try:
        ifc_file = ifcopenshell.open(ifc_path)
    except Exception as e:
        return {
            "file_name": file_name,
            "error": f"Failed to open IFC file: {e}",
            "spaces": [],
            "doors": [],
            "counts": {"IfcSpace": 0, "IfcDoor": 0, "IfcWall": 0, "IfcBuildingStorey": 0},
            "data_quality": {
                "has_spaces": False,
                "has_storeys": False,
                "has_space_areas": False,
                "has_space_volumes": False,
                "has_fire_ratings_doors": False,
                "has_zones": False,
            },
        }

    try:
        spaces = ifc_file.by_type("IfcSpace") or []
        doors = ifc_file.by_type("IfcDoor") or []
        walls = ifc_file.by_type("IfcWall") or []
        storeys = ifc_file.by_type("IfcBuildingStorey") or []

        storey_map = build_element_storey_map(ifc_file)

        # ---- Spaces preview ----
        space_list: List[Dict[str, Any]] = []
        has_area = False
        has_volume = False
        has_zone = False

        for space in spaces[:preview_limit]:
            area = get_space_area_m2(space)
            vol = get_space_volume_m3(space)
            zones = get_space_zones(space)

            if area is not None:
                has_area = True
            if vol is not None:
                has_volume = True
            if zones:
                has_zone = True

            space_list.append({
                "guid": _safe_get_attribute(space, "GlobalId"),
                "name": _safe_get_attribute(space, "Name") or "Unnamed",
                "long_name": _safe_get_attribute(space, "LongName"),
                "object_type": _safe_get_attribute(space, "ObjectType"),
                "storey_name": get_storey_name(space, storey_map),
                "area_m2": area,
                "volume_m3": vol,
                "zones": zones
            })

        # ---- Doors preview ----
        door_list: List[Dict[str, Any]] = []
        fire_rating_count = 0

        for door in doors[:preview_limit]:
            fire_rating = get_door_fire_rating(door)
            if fire_rating is not None:
                fire_rating_count += 1

            door_list.append({
                "guid": _safe_get_attribute(door, "GlobalId"),
                "name": _safe_get_attribute(door, "Name") or "Unnamed",
                "predefined_type": _safe_get_attribute(door, "PredefinedType") or "Unknown",
                "fire_rating": fire_rating
            })

        return {
            "file_name": file_name,
            "spaces": space_list,
            "doors": door_list,
            "counts": {
                "IfcSpace": len(spaces),
                "IfcDoor": len(doors),
                "IfcWall": len(walls),
                "IfcBuildingStorey": len(storeys),
            },
            "data_quality": {
                "has_spaces": len(spaces) > 0,
                "has_storeys": len(storeys) > 0,
                "has_space_areas": has_area,
                "has_space_volumes": has_volume,
                "has_fire_ratings_doors": fire_rating_count > 0,
                "has_zones": has_zone,
            },
        }

    except Exception as e:
        return {
            "file_name": file_name,
            "error": f"Error scanning IFC file: {e}",
            "spaces": [],
            "doors": [],
            "counts": {"IfcSpace": 0, "IfcDoor": 0, "IfcWall": 0, "IfcBuildingStorey": 0},
            "data_quality": {
                "has_spaces": False,
                "has_storeys": False,
                "has_space_areas": False,
                "has_space_volumes": False,
                "has_fire_ratings_doors": False,
                "has_zones": False,
            },
        }


# ============================================================
# SECTION G — BATCH SCAN (CHECK ALL FILES)
# ============================================================

def scan_ifc_folder(folder_path: str, recursive: bool = True, preview_limit: int = 20) -> Dict[str, Any]:
    """
    Scan all IFC files in a folder.
    """
    folder = Path(folder_path)
    pattern = "**/*.ifc" if recursive else "*.ifc"
    files = sorted(folder.glob(pattern))

    results: List[Dict[str, Any]] = []
    pass_count = 0
    fail_count = 0

    for f in files:
        r = scan_ifc_basic(str(f), preview_limit=preview_limit)
        results.append(r)
        if "error" in r:
            fail_count += 1
        else:
            pass_count += 1

    return {
        "batch": True,
        "folder": str(folder),
        "files_checked": len(files),
        "results": results,
        "stats": {"PASS": pass_count, "FAIL": fail_count}
    }


if __name__ == "__main__":
    # ✅ Hardcode folder here (use raw string OR forward slashes)
    folder_path = r"C:\Users\gorkem\Documents\GitHub\automatic-fire-compliance-checker\00_data\ifc_models"

    batch = scan_ifc_folder(folder_path, recursive=True, preview_limit=5)

    import json
    print(json.dumps(batch, indent=2))
