"""
CTE DB-SI SI1 Fire Compartmentation Compliance Checker

This module implements checks for Spanish Building Code (CTE) DB-SI Section 1:
Interior Fire Propagation - Fire compartmentation and sector sizing.

Compliant with IFCore platform contracts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from collections import defaultdict

import ifcopenshell


# ============================================================
# CONFIG LOADING
# ============================================================

_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}

# Default config path - teams should override this or pass as parameter
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "data_push" / "rulesdb_si_si1_rules.json.json"


def load_rules_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load SI1 rules configuration from JSON file."""
    path = config_path or str(DEFAULT_CONFIG_PATH)
    p = str(Path(path))
    
    if p in _CONFIG_CACHE:
        return _CONFIG_CACHE[p]
    
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        _CONFIG_CACHE[p] = data
        return data
    except Exception:
        return {}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def safe_attr(el: Any, name: str, default: Any = None) -> Any:
    """Safely get attribute from IFC element."""
    try:
        return getattr(el, name)
    except Exception:
        return default


def norm(s: Optional[str]) -> str:
    """Normalize string for comparison."""
    return (s or "").strip().lower()


def get_psets(element: Any) -> Dict[str, Any]:
    """Extract property sets from IFC element."""
    try:
        from ifcopenshell.util.element import get_psets
        return get_psets(element) or {}
    except Exception:
        return {}


def build_storey_map(model: ifcopenshell.file) -> Dict[int, str]:
    """Build mapping of element IDs to storey names."""
    m: Dict[int, str] = {}

    # Containment relationships
    for rel in model.by_type("IfcRelContainedInSpatialStructure") or []:
        container = safe_attr(rel, "RelatingStructure", None)
        if container and container.is_a("IfcBuildingStorey"):
            sname = safe_attr(container, "Name", None) or "Unknown"
            for el in safe_attr(rel, "RelatedElements", []) or []:
                try:
                    m[el.id()] = str(sname)
                except Exception:
                    pass

    # Aggregation relationships
    for rel in model.by_type("IfcRelAggregates") or []:
        parent = safe_attr(rel, "RelatingObject", None)
        if parent and parent.is_a("IfcBuildingStorey"):
            sname = safe_attr(parent, "Name", None) or "Unknown"
            for child in safe_attr(rel, "RelatedObjects", []) or []:
                try:
                    m[child.id()] = str(sname)
                except Exception:
                    pass

    return m


def get_space_zones(space: Any) -> List[str]:
    """Extract zone assignments for a space."""
    zones: List[str] = []
    try:
        for rel in getattr(space, "HasAssignments", []) or []:
            if rel.is_a("IfcRelAssignsToGroup"):
                grp = getattr(rel, "RelatingGroup", None)
                if grp and grp.is_a("IfcZone"):
                    zname = safe_attr(grp, "Name", None) or safe_attr(grp, "LongName", None) or "UnnamedZone"
                    zones.append(str(zname))
    except Exception:
        pass
    return zones


def get_space_area_m2(space: Any) -> Optional[float]:
    """Extract area from space in square meters."""
    # Try IfcElementQuantity first
    try:
        for rel in getattr(space, "IsDefinedBy", []) or []:
            if not rel.is_a("IfcRelDefinesByProperties"):
                continue
            qset = getattr(rel, "RelatingPropertyDefinition", None)
            if qset and qset.is_a("IfcElementQuantity"):
                for q in getattr(qset, "Quantities", []) or []:
                    if q.is_a("IfcQuantityArea"):
                        val = getattr(q, "AreaValue", None)
                        if val is not None:
                            return float(val)
    except Exception:
        pass

    # Try common property sets
    p = get_psets(space)
    for pset_name in ("Qto_SpaceBaseQuantities", "Pset_SpaceCommon", "BaseQuantities"):
        d = p.get(pset_name, {})
        if isinstance(d, dict):
            for k in ("NetFloorArea", "GrossFloorArea", "Area", "GrossArea", "NetArea"):
                if k in d and d[k] is not None:
                    try:
                        return float(d[k])
                    except Exception:
                        pass

    return None


def get_space_volume_m3(space: Any) -> Optional[float]:
    """Extract volume from space in cubic meters."""
    # Try IfcElementQuantity first
    try:
        for rel in getattr(space, "IsDefinedBy", []) or []:
            if not rel.is_a("IfcRelDefinesByProperties"):
                continue
            qset = getattr(rel, "RelatingPropertyDefinition", None)
            if qset and qset.is_a("IfcElementQuantity"):
                for q in getattr(qset, "Quantities", []) or []:
                    if q.is_a("IfcQuantityVolume"):
                        val = getattr(q, "VolumeValue", None)
                        if val is not None:
                            return float(val)
    except Exception:
        pass

    # Try common property sets
    p = get_psets(space)
    for pset_name in ("Qto_SpaceBaseQuantities", "Pset_SpaceCommon", "BaseQuantities"):
        d = p.get(pset_name, {})
        if isinstance(d, dict):
            for k in ("NetVolume", "GrossVolume", "Volume"):
                if k in d and d[k] is not None:
                    try:
                        return float(d[k])
                    except Exception:
                        pass

    return None


def detect_sector_for_space(space: Any, rules: Dict[str, Any]) -> str:
    """Determine fire sector ID for a space."""
    # Priority 1: IfcZone membership
    try:
        for rel in getattr(space, "HasAssignments", []) or []:
            if rel.is_a("IfcRelAssignsToGroup"):
                grp = getattr(rel, "RelatingGroup", None)
                if grp and grp.is_a("IfcZone"):
                    zname = safe_attr(grp, "Name", None) or safe_attr(grp, "LongName", None)
                    if zname and any(kw in norm(zname) for kw in ["sector", "compartment", "fire"]):
                        return str(zname)
    except Exception:
        pass

    # Priority 2: Property sets
    try:
        psets = get_psets(space)
        for pset_name, props in psets.items():
            if isinstance(props, dict):
                for k in ["FireCompartment", "Sector", "SI_Sector", "FireSector"]:
                    if k in props and props[k]:
                        return str(props[k])
    except Exception:
        pass

    # Priority 3: Regex on name/type
    try:
        texts = [
            safe_attr(space, "Name") or "",
            safe_attr(space, "LongName") or "",
            safe_attr(space, "ObjectType") or "",
        ]
        combined = " ".join(t for t in texts if t)
        match = re.search(r"sector[_\s]*(\d+|[A-Z])", combined, re.IGNORECASE)
        if match:
            return f"SECTOR_{match.group(1)}"
    except Exception:
        pass

    # Fallback: single sector
    return "SECTOR_1"


def build_door_space_adjacency(model: ifcopenshell.file) -> Dict[str, List[str]]:
    """Build map of door GUIDs to adjacent space GUIDs from space boundaries."""
    door_to_spaces = defaultdict(set)

    for rel_type in ("IfcRelSpaceBoundary", "IfcRelSpaceBoundary1stLevel", "IfcRelSpaceBoundary2ndLevel"):
        try:
            for rel in model.by_type(rel_type) or []:
                space = safe_attr(rel, "RelatingSpace")
                elem = safe_attr(rel, "RelatedBuildingElement")
                if not space or not elem:
                    continue
                if not space.is_a("IfcSpace") or not elem.is_a("IfcDoor"):
                    continue

                door_guid = safe_attr(elem, "GlobalId") or f"id:{elem.id()}"
                space_guid = safe_attr(space, "GlobalId") or f"id:{space.id()}"
                door_to_spaces[str(door_guid)].add(str(space_guid))
        except Exception:
            continue

    return {door_guid: sorted(list(space_guids)) for door_guid, space_guids in door_to_spaces.items()}


def get_door_fire_rating(door: Any) -> Optional[str]:
    """Extract fire rating from door element."""
    # Try Pset_DoorCommon.FireRating
    try:
        psets = get_psets(door)
        pset_dc = psets.get("Pset_DoorCommon", {})
        if isinstance(pset_dc, dict) and "FireRating" in pset_dc:
            val = pset_dc["FireRating"]
            if val and str(val).strip().lower() not in {"", "-", "n/a", "na", "null", "none"}:
                return str(val)
    except Exception:
        pass

    # Try direct attribute
    try:
        attr_val = safe_attr(door, "FireRating")
        if attr_val and str(attr_val).strip().lower() not in {"", "-", "n/a", "na", "null", "none"}:
            return str(attr_val)
    except Exception:
        pass

    return None


# ============================================================
# CHECK FUNCTIONS (IFCore Platform Contract)
# ============================================================

def check_sector_area_limits(
    model: ifcopenshell.file,
    building_use: str = "residencial_vivienda",
    has_sprinklers: bool = False,
    config_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check fire sector area limits per CTE DB-SI SI1.
    
    Args:
        model: IFC model (ifcopenshell.file object)
        building_use: Building use classification (default: "residencial_vivienda")
        has_sprinklers: Whether building has automatic sprinkler system
        config_path: Optional path to rules config JSON
    
    Returns:
        List of check result dicts, one per sector, following IFCore schema.
    """
    results = []
    rules = load_rules_config(config_path)
    
    # Get sector limits from config
    sector_limits = rules.get("sector_limits_m2", {}).get(building_use, {})
    if not sector_limits:
        return [{
            "element_id": None,
            "element_type": "IfcBuilding",
            "element_name": "Building",
            "element_name_long": "Building",
            "check_status": "blocked",
            "actual_value": None,
            "required_value": None,
            "comment": f"No sector limits configured for building_use='{building_use}'",
            "log": "Check rules configuration file"
        }]
    
    base_limit = float(sector_limits.get("base_limit_m2", 2500.0))
    multiplier = float(sector_limits.get("sprinkler_multiplier", 2.0)) if has_sprinklers else 1.0
    effective_limit = base_limit * multiplier
    
    # Collect spaces and group by sector
    spaces = model.by_type("IfcSpace") or []
    storey_map = build_storey_map(model)
    
    sector_stats = defaultdict(lambda: {
        "area_total": 0.0,
        "space_count": 0,
        "missing_area_count": 0,
        "storeys": set()
    })
    
    for space in spaces:
        sector_id = detect_sector_for_space(space, rules)
        area = get_space_area_m2(space)
        storey = storey_map.get(space.id(), "Unknown")
        
        stats = sector_stats[sector_id]
        stats["space_count"] += 1
        stats["storeys"].add(storey)
        
        if area is None:
            stats["missing_area_count"] += 1
        else:
            stats["area_total"] += area
    
    # Generate check results per sector
    for sector_id, stats in sector_stats.items():
        area_total = stats["area_total"]
        missing = stats["missing_area_count"]
        
        if missing > 0:
            check_status = "blocked"
            comment = f"Cannot verify: {missing} space(s) missing area data"
        elif area_total > effective_limit:
            check_status = "fail"
            overage = area_total - effective_limit
            comment = f"Sector exceeds limit by {overage:.1f} m²"
        else:
            check_status = "pass"
            comment = None
        
        storeys_str = ", ".join(sorted(stats["storeys"]))
        
        results.append({
            "element_id": sector_id,
            "element_type": "IfcZone",
            "element_name": sector_id,
            "element_name_long": f"{sector_id} ({storeys_str})",
            "check_status": check_status,
            "actual_value": f"{area_total:.1f} m²" if missing == 0 else f"~{area_total:.1f} m² (incomplete)",
            "required_value": f"≤ {effective_limit:.1f} m²",
            "comment": comment,
            "log": f"Spaces: {stats['space_count']}, Missing area: {missing}, Sprinklers: {has_sprinklers}"
        })
    
    return results


def check_special_risk_rooms(
    model: ifcopenshell.file,
    config_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Detect and classify special risk rooms per CTE DB-SI Table 2.1.
    
    Special risk rooms (locales de riesgo especial) include storage rooms,
    workshops, archives, waste storage, etc. that exceed certain size thresholds.
    
    Args:
        model: IFC model (ifcopenshell.file object)
        config_path: Optional path to rules config JSON
    
    Returns:
        List of check result dicts, one per detected risk room.
    """
    results = []
    rules = load_rules_config(config_path)
    
    risk_config = rules.get("special_risk_rooms", {})
    types_config = risk_config.get("types", {})
    
    if not types_config:
        return [{
            "element_id": None,
            "element_type": "IfcBuilding",
            "element_name": "Building",
            "element_name_long": "Building",
            "check_status": "blocked",
            "actual_value": None,
            "required_value": None,
            "comment": "No special risk room configuration found",
            "log": "Check rules configuration file"
        }]
    
    spaces = model.by_type("IfcSpace") or []
    storey_map = build_storey_map(model)
    
    for space in spaces:
        # Collect searchable text
        name = safe_attr(space, "Name") or "Unnamed"
        long_name = safe_attr(space, "LongName") or ""
        object_type = safe_attr(space, "ObjectType") or ""
        zones = get_space_zones(space)
        
        text_parts = [norm(name), norm(long_name), norm(object_type)]
        text_parts.extend(norm(z) for z in zones)
        search_text = " | ".join(p for p in text_parts if p)
        
        # Match against risk room types
        detected_type = None
        matched_keywords = []
        
        for risk_type, type_config in types_config.items():
            keywords = type_config.get("keywords", [])
            matches = [kw for kw in keywords if norm(kw) in search_text]
            if matches:
                detected_type = risk_type
                matched_keywords = matches
                break
        
        if not detected_type:
            continue
        
        # Get metrics
        area = get_space_area_m2(space)
        volume = get_space_volume_m3(space)
        
        type_config = types_config[detected_type]
        metric = type_config.get("metric", "area_m2")
        thresholds = type_config.get("thresholds", {})
        
        # Determine value to check
        if metric == "area_m2":
            value = area
            value_str = f"{area:.1f} m²" if area else None
        elif metric == "volume_m3":
            value = volume
            value_str = f"{volume:.1f} m³" if volume else None
        else:
            value = None
            value_str = None
        
        # Classify risk level
        if value is None:
            check_status = "blocked"
            risk_level = "UNKNOWN"
            comment = f"Cannot classify: {metric} data missing"
        else:
            risk_level = None
            for level in ["high", "medium", "low"]:
                threshold = thresholds.get(level, {})
                gt = threshold.get("gt")
                lte = threshold.get("lte")
                
                passes = True
                if gt is not None and not (value > gt):
                    passes = False
                if lte is not None and not (value <= lte):
                    passes = False
                
                if passes:
                    risk_level = level.upper()
                    break
            
            if risk_level is None:
                check_status = "pass"
                risk_level = "NONE"
                comment = f"Below risk thresholds"
            elif risk_level == "HIGH":
                check_status = "fail"
                comment = f"HIGH risk: requires special fire protection measures"
            else:
                check_status = "warning"
                comment = f"{risk_level} risk: additional requirements may apply"
        
        storey = storey_map.get(space.id(), "Unknown")
        guid = safe_attr(space, "GlobalId") or f"id:{space.id()}"
        
        results.append({
            "element_id": guid,
            "element_type": "IfcSpace",
            "element_name": name,
            "element_name_long": f"{name} ({storey})",
            "check_status": check_status,
            "actual_value": value_str,
            "required_value": f"{detected_type.replace('_', ' ').title()}",
            "comment": comment,
            "log": f"Type: {detected_type}, Risk: {risk_level}, Keywords: {', '.join(matched_keywords)}"
        })
    
    # If no risk rooms detected, return a summary pass
    if not results:
        results.append({
            "element_id": None,
            "element_type": "IfcBuilding",
            "element_name": "Building",
            "element_name_long": "Building - Special Risk Rooms",
            "check_status": "pass",
            "actual_value": "0 detected",
            "required_value": "N/A",
            "comment": "No special risk rooms detected",
            "log": None
        })
    
    return results


def check_risk_room_door_ratings(
    model: ifcopenshell.file,
    config_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check fire ratings on doors adjacent to special risk rooms.
    
    Doors separating special risk rooms from other spaces must have
    appropriate fire ratings per CTE DB-SI requirements.
    
    Args:
        model: IFC model (ifcopenshell.file object)
        config_path: Optional path to rules config JSON
    
    Returns:
        List of check result dicts, one per risk room boundary door.
    """
    results = []
    rules = load_rules_config(config_path)
    
    # First, identify risk rooms (reuse logic)
    risk_results = check_special_risk_rooms(model, config_path)
    risk_room_guids = {
        r["element_id"] for r in risk_results 
        if r["element_type"] == "IfcSpace" and r["check_status"] in {"fail", "warning"}
    }
    
    if not risk_room_guids:
        return [{
            "element_id": None,
            "element_type": "IfcBuilding",
            "element_name": "Building",
            "element_name_long": "Building - Risk Room Doors",
            "check_status": "pass",
            "actual_value": "No risk rooms detected",
            "required_value": "N/A",
            "comment": "No special risk rooms requiring door checks",
            "log": None
        }]
    
    # Build door-to-space adjacency
    adjacency = build_door_space_adjacency(model)
    
    if not adjacency:
        return [{
            "element_id": None,
            "element_type": "IfcBuilding",
            "element_name": "Building",
            "element_name_long": "Building - Risk Room Doors",
            "check_status": "blocked",
            "actual_value": None,
            "required_value": None,
            "comment": "Cannot map doors to spaces (no IfcRelSpaceBoundary data)",
            "log": f"Risk rooms detected: {len(risk_room_guids)}"
        }]
    
    doors = model.by_type("IfcDoor") or []
    door_by_guid = {safe_attr(d, "GlobalId"): d for d in doors}
    
    for door_guid, adjacent_space_guids in adjacency.items():
        # Check if door is adjacent to any risk room
        if not any(sg in risk_room_guids for sg in adjacent_space_guids):
            continue
        
        door = door_by_guid.get(door_guid)
        if not door:
            continue
        
        door_name = safe_attr(door, "Name") or "Unnamed Door"
        fire_rating = get_door_fire_rating(door)
        
        if fire_rating:
            check_status = "pass"
            actual = fire_rating
            comment = None
        else:
            check_status = "fail"
            actual = "Not specified"
            comment = "Fire rating required for risk room boundary door"
        
        results.append({
            "element_id": door_guid,
            "element_type": "IfcDoor",
            "element_name": door_name,
            "element_name_long": f"{door_name} (Risk Room Boundary)",
            "check_status": check_status,
            "actual_value": actual,
            "required_value": "Fire-rated door (e.g., EI 30, EI 60)",
            "comment": comment,
            "log": f"Adjacent spaces: {len(adjacent_space_guids)}"
        })
    
    # If no doors found on boundaries, report it
    if not results:
        results.append({
            "element_id": None,
            "element_type": "IfcBuilding",
            "element_name": "Building",
            "element_name_long": "Building - Risk Room Doors",
            "check_status": "blocked",
            "actual_value": f"{len(risk_room_guids)} risk room(s)",
            "required_value": None,
            "comment": "No doors found on risk room boundaries",
            "log": "Check space boundary modeling"
        })
    
    return results


# ============================================================
# LOCAL TESTING
# ============================================================

if __name__ == "__main__":
    """
    Local testing example.
    Usage: python checker_si1_fire_compartmentation.py path/to/model.ifc
    """
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python checker_si1_fire_compartmentation.py <ifc_file>")
        sys.exit(1)
    
    ifc_path = sys.argv[1]
    model = ifcopenshell.open(ifc_path)
    
    print("=" * 80)
    print("CTE DB-SI SI1 Fire Compartmentation Checks")
    print("=" * 80)
    
    # Check 1: Sector Area Limits
    print("\n1. Sector Area Limits")
    print("-" * 80)
    sector_results = check_sector_area_limits(model, building_use="residencial_vivienda", has_sprinklers=False)
    for r in sector_results:
        status = r["check_status"].upper()
        print(f"[{status}] {r['element_name']}: {r['actual_value']} (limit: {r['required_value']})")
        if r["comment"]:
            print(f"       → {r['comment']}")
    
    # Check 2: Special Risk Rooms
    print("\n2. Special Risk Rooms")
    print("-" * 80)
    risk_results = check_special_risk_rooms(model)
    for r in risk_results:
        status = r["check_status"].upper()
        print(f"[{status}] {r['element_name']}: {r['actual_value']}")
        if r["comment"]:
            print(f"       → {r['comment']}")
    
    # Check 3: Risk Room Door Ratings
    print("\n3. Risk Room Door Fire Ratings")
    print("-" * 80)
    door_results = check_risk_room_door_ratings(model)
    for r in door_results:
        status = r["check_status"].upper()
        print(f"[{status}] {r['element_name']}: {r['actual_value']}")
        if r["comment"]:
            print(f"       → {r['comment']}")
    
    print("\n" + "=" * 80)
    print(f"Total checks: {len(sector_results) + len(risk_results) + len(door_results)}")
    print("=" * 80)
