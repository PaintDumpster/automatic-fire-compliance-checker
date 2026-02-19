"""
LEGACY FILE - NOT IFCORE PLATFORM COMPLIANT

This file is kept for reference only. It does not follow IFCore platform contracts:
- Functions take file paths instead of model objects
- Return format is nested dicts, not list[dict]
- Functions not named with check_* prefix

For platform-compliant version, use:
    tools/checker_si1_fire_compartmentation.py

See documentation:
    tools/SI1_FIRE_COMPARTMENTATION_CHECKER.md
"""

from __future__ import annotations

import json
import re
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

import ifcopenshell


# ============================================================
# CONFIG LOADING
# ============================================================

_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}

#  DEFAULT CONFIG PATH (your exact file)
DEFAULT_CONFIG_PATH = r"C:\Users\gorkem\Documents\GitHub\automatic-fire-compliance-checker\data_push\rulesdb_si_si1_rules.json.json"


def load_rules_config(config_path: str) -> Dict[str, Any]:
    p = str(Path(config_path))
    if p in _CONFIG_CACHE:
        return _CONFIG_CACHE[p]
    data = json.loads(Path(p).read_text(encoding="utf-8"))
    _CONFIG_CACHE[p] = data
    return data


# ============================================================
# SAFE HELPERS
# ============================================================

def safe_attr(el: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(el, name)
    except Exception:
        return default


def norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def collect_space_text(space_row: Dict[str, Any]) -> str:
    parts = [
        norm(space_row.get("name")),
        norm(space_row.get("long_name")),
        norm(space_row.get("object_type")),
    ]
    zones = space_row.get("zones") or []
    parts.extend([norm(z) for z in zones])
    return " | ".join([p for p in parts if p])


# ============================================================
# IFC EXTRACTION (SPACES / STOREYS / ZONES / AREAS)
# ============================================================

def build_storey_map(ifc_file: ifcopenshell.file) -> Dict[int, str]:
    m: Dict[int, str] = {}

    # 1) Containment (elements, sometimes spaces)
    for rel in ifc_file.by_type("IfcRelContainedInSpatialStructure") or []:
        container = safe_attr(rel, "RelatingStructure", None)
        if container and container.is_a("IfcBuildingStorey"):
            sname = safe_attr(container, "Name", None) or "Unknown"
            for el in safe_attr(rel, "RelatedElements", []) or []:
                try:
                    m[el.id()] = str(sname)
                except Exception:
                    pass

    # 2) Aggregation (very common for spaces)
    for rel in ifc_file.by_type("IfcRelAggregates") or []:
        parent = safe_attr(rel, "RelatingObject", None)
        if parent and parent.is_a("IfcBuildingStorey"):
            sname = safe_attr(parent, "Name", None) or "Unknown"
            for child in safe_attr(rel, "RelatedObjects", []) or []:
                try:
                    m[child.id()] = str(sname)
                except Exception:
                    pass

    return m


def get_psets(element: Any) -> Dict[str, Any]:
    try:
        from ifcopenshell.util.element import get_psets  # type: ignore
        return get_psets(element) or {}
    except Exception:
        return {}


def get_space_zones(space: Any) -> List[str]:
    zones: List[str] = []
    try:
        for rel in getattr(space, "HasAssignments", []) or []:
            if not rel.is_a("IfcRelAssignsToGroup"):
                continue
            grp = getattr(rel, "RelatingGroup", None)
            if grp and grp.is_a("IfcZone"):
                zname = safe_attr(grp, "Name", None) or safe_attr(grp, "LongName", None) or "UnnamedZone"
                zones.append(str(zname))
    except Exception:
        pass
    return zones


def get_space_area_m2(space: Any) -> Optional[float]:
    # 1) IfcElementQuantity via IsDefinedBy
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

    # 2) Common psets (exporter dependent)
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
    # 1) IfcElementQuantity via IsDefinedBy
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

    # 2) Common psets
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


# ============================================================
# SECTOR DETECTION (ZONE / PSET / REGEX / FALLBACK)
# ============================================================

def detect_sector_id_for_space(space_row: Dict[str, Any], space_entity: Any, rules: Dict[str, Any]) -> Tuple[str, str]:
    sd = rules.get("sector_detection", {}) or {}

    # 1) IfcZone name match
    zone_keywords = [norm(x) for x in (sd.get("zone_name_keywords") or [])]
    for z in space_row.get("zones") or []:
        zn = norm(z)
        if any(k in zn for k in zone_keywords):
            return str(z), "zone:name"

    # 2) Pset candidates
    pset_candidates = sd.get("space_pset_candidates") or []
    psets = get_psets(space_entity)
    for item in pset_candidates:
        pset_name = item.get("pset")
        prop = item.get("prop")
        if not pset_name or not prop:
            continue
        d = psets.get(pset_name, {})
        if isinstance(d, dict) and d.get(prop):
            return str(d.get(prop)).strip(), f"pset:{pset_name}.{prop}"

    # 3) Regex patterns against name/object_type/long_name/zones text
    text = collect_space_text(space_row)
    for pat in sd.get("name_regex_patterns") or []:
        try:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                return m.group(0), f"regex:{pat}"
        except re.error:
            continue

    # 4) Fallback
    fallback = sd.get("fallback_sector_id") or "SECTOR_1"
    return str(fallback), "fallback:single_sector"


# ============================================================
# SPECIAL RISK ROOMS
# ============================================================

def detect_risk_room_type(space_row: Dict[str, Any], rules: Dict[str, Any]) -> Tuple[Optional[str], float, List[str]]:
    rr = rules.get("special_risk_rooms", {}) or {}
    keywords = rr.get("keywords", {}) or {}

    text = collect_space_text(space_row)
    if not text:
        return None, 0.0, []

    best_type: Optional[str] = None
    best_score = 0.0
    best_matches: List[str] = []

    for room_type, kws in keywords.items():
        matches = []
        for kw in kws or []:
            if norm(kw) and norm(kw) in text:
                matches.append(kw)

        if matches:
            score = min(1.0, 0.3 + 0.2 * len(matches))
            if score > best_score:
                best_score = score
                best_type = room_type
                best_matches = matches

    return best_type, best_score, best_matches


def classify_risk_level(room_type: str, area_m2: Optional[float], volume_m3: Optional[float], rules: Dict[str, Any]) -> Dict[str, Any]:
    rr = rules.get("special_risk_rooms", {}) or {}
    table = rr.get("table_2_1_thresholds", {}) or {}

    t = table.get(room_type)
    if not t:
        return {"status": "INCOMPLETE", "reason": f"No thresholds configured for {room_type}"}

    metric = (t.get("metric") or "").upper()

    if metric == "S":
        value = area_m2
    elif metric == "V":
        value = volume_m3
    elif metric in ("P", "POWER"):
        value = None  # usually not in IFC (unless you model it explicitly)
    elif metric in ("NONE", ""):
        if t.get("always_special_risk") or t.get("always_special_risk", False):
            return {"status": "PASS", "risk_level": "ALWAYS", "metric": metric, "value": None}
        return {"status": "INCOMPLETE", "reason": "No metric and not marked always_special_risk"}
    else:
        value = None

    if value is None:
        return {"status": "INCOMPLETE", "reason": f"Missing metric {metric} value"}

    def ok(band: Dict[str, Any]) -> bool:
        gt = band.get("gt", None)
        gte = band.get("gte", None)
        lt = band.get("lt", None)
        lte = band.get("lte", None)
        v = float(value)
        if gt is not None and not (v > float(gt)):
            return False
        if gte is not None and not (v >= float(gte)):
            return False
        if lt is not None and not (v < float(lt)):
            return False
        if lte is not None and not (v <= float(lte)):
            return False
        return True

    # Check levels (low/medium/high) in order
    for level in ("low", "medium", "high"):
        band = t.get(level)
        if isinstance(band, dict) and ok(band):
            return {"status": "PASS", "risk_level": level.upper(), "metric": metric, "value": float(value)}

    return {"status": "INCOMPLETE", "reason": "Could not classify risk level"}


# ============================================================
# SCAN IFC (RAW FACTS)
# ============================================================

def scan_ifc_basic(ifc_path: str, preview_limit: int = 200) -> Dict[str, Any]:
    file_name = Path(ifc_path).name

    try:
        ifc = ifcopenshell.open(ifc_path)
    except Exception as e:
        return {"file_name": file_name, "error": f"Failed to open IFC: {e}", "spaces": [], "doors": [], "counts": {}, "data_quality": {}}

    spaces = ifc.by_type("IfcSpace") or []
    storeys = ifc.by_type("IfcBuildingStorey") or []
    doors = ifc.by_type("IfcDoor") or []
    walls = ifc.by_type("IfcWall") or []

    storey_map = build_storey_map(ifc)

    space_rows: List[Dict[str, Any]] = []
    has_area = False
    has_vol = False
    has_zone = False

    for sp in spaces[:preview_limit]:
        area = get_space_area_m2(sp)
        vol = get_space_volume_m3(sp)
        zones = get_space_zones(sp)

        has_area = has_area or (area is not None)
        has_vol = has_vol or (vol is not None)
        has_zone = has_zone or bool(zones)

        space_rows.append({
            "guid": safe_attr(sp, "GlobalId"),
            "name": safe_attr(sp, "Name") or "Unnamed",
            "long_name": safe_attr(sp, "LongName"),
            "object_type": safe_attr(sp, "ObjectType"),
            "storey_name": storey_map.get(sp.id(), None),
            "area_m2": area,
            "volume_m3": vol,
            "zones": zones
        })

    return {
        "file_name": file_name,
        "counts": {
            "IfcSpace": len(spaces),
            "IfcDoor": len(doors),
            "IfcWall": len(walls),
            "IfcBuildingStorey": len(storeys)
        },
        "data_quality": {
            "has_spaces": len(spaces) > 0,
            "has_storeys": len(storeys) > 0,
            "has_space_areas": has_area,
            "has_space_volumes": has_vol,
            "has_zones": has_zone
        },
        "spaces": space_rows
    }


# ============================================================
# SI1 CHECKS (Sectors + Sector Size + Risk Rooms)
# ============================================================

def check_sector_size_compliance(sectors: Dict[str, Any], rules: Dict[str, Any], building_use: str, sprinklers: bool, used_fallback: bool) -> Dict[str, Any]:
    limits = rules.get("sector_area_limits_m2", {}) or {}
    use_cfg = limits.get(building_use)

    if not use_cfg:
        return {
            "compliant": False,
            "color": "red",
            "details": {"reason": f"No sector area limits configured for building_use='{building_use}'."}
        }

    base = float(use_cfg.get("base_limit_m2", 0.0))
    allow_double = bool(use_cfg.get("allow_double_if_sprinklers", False))
    mult = float(use_cfg.get("sprinkler_multiplier", 2.0))

    limit = base * mult if (sprinklers and allow_double) else base

    checks = []
    any_fail = False
    any_incomplete = False

    if used_fallback:
        any_incomplete = True

    for sid, s in sectors.items():
        area = s.get("area_total_m2", None)
        miss = int(s.get("missing_area_spaces", 0))

        if area is None or miss > 0:
            any_incomplete = True
            checks.append({"sector": sid, "status": "INCOMPLETE", "missing_area_spaces": miss})
            continue

        ok = float(area) <= limit
        if not ok:
            any_fail = True
        checks.append({"sector": sid, "status": "PASS" if ok else "FAIL", "area_total_m2": float(area), "limit_m2": limit})

    if any_fail:
        result = "FAIL"
        color = "red"
    elif any_incomplete:
        result = "INCOMPLETE"
        color = "yellow"
    else:
        result = "PASS"
        color = "green"

    return {
        "result": result,
        "color": color,
        "details": {
            "building_use": building_use,
            "sprinklers": sprinklers,
            "limit_m2": limit,
            "used_sector_fallback": used_fallback,
            "checks": checks
        }
    }


def build_sectors(ifc: ifcopenshell.file, scan: Dict[str, Any], rules: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    spaces = ifc.by_type("IfcSpace") or []
    scan_spaces = scan.get("spaces", []) or []
    scan_by_guid = {str(s.get("guid")): s for s in scan_spaces if s.get("guid")}

    used_fallback = False
    stats = defaultdict(lambda: {"space_count": 0, "area_total_m2": 0.0, "missing_area_spaces": 0, "area_by_storey": defaultdict(float)})

    for sp in spaces:
        guid = safe_attr(sp, "GlobalId")
        if not guid:
            continue
        g = str(guid)
        row = scan_by_guid.get(g)
        if not row:
            continue

        sector_id, method = detect_sector_id_for_space(row, sp, rules)
        if method.startswith("fallback"):
            used_fallback = True

        row["sector"] = sector_id
        row["sector_method"] = method

        stats[sector_id]["space_count"] += 1
        area = row.get("area_m2")
        if area is None:
            stats[sector_id]["missing_area_spaces"] += 1
        else:
            stats[sector_id]["area_total_m2"] += float(area)
            st = row.get("storey_name")
            if st:
                stats[sector_id]["area_by_storey"][st] += float(area)

    sectors_out: Dict[str, Any] = {}
    for sid, v in stats.items():
        sectors_out[sid] = {
            "id": sid,
            "space_count": v["space_count"],
            "area_total_m2": v["area_total_m2"],
            "missing_area_spaces": v["missing_area_spaces"],
            "area_by_storey": dict(v["area_by_storey"])
        }

    return sectors_out, used_fallback


def check_special_risk_rooms(scan: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
    spaces = scan.get("spaces", []) or []
    items = []
    any_incomplete = False

    for sp in spaces:
        rt, conf, matches = detect_risk_room_type(sp, rules)
        if not rt:
            continue

        area = sp.get("area_m2")
        vol = sp.get("volume_m3")
        cls = classify_risk_level(rt, area, vol, rules)

        status = cls.get("status", "INCOMPLETE")
        if status != "PASS":
            any_incomplete = True

        items.append({
            "guid": sp.get("guid"),
            "name": sp.get("name"),
            "storey": sp.get("storey_name"),
            "detected_type": rt,
            "confidence": conf,
            "matched_keywords": matches,
            "area_m2": area,
            "volume_m3": vol,
            "classification": cls
        })

    if not items:
        return {"result": "PASS", "color": "green", "details": {"detected_count": 0, "items": []}}

    result = "INCOMPLETE" if any_incomplete else "PASS"
    color = "yellow" if any_incomplete else "green"
    return {"result": result, "color": color, "details": {"detected_count": len(items), "items": items}}


def run_si1_checks(ifc_path: str, config_path: str) -> Dict[str, Any]:
    rules = load_rules_config(config_path)

    defaults = rules.get("project_defaults", {}) or {}
    building_use = str(defaults.get("building_use", "Residencial Vivienda"))
    sprinklers = bool(defaults.get("sprinklers", False))

    scan = scan_ifc_basic(ifc_path)
    if "error" in scan:
        return {"file_name": scan.get("file_name"), "error": scan.get("error")}

    ifc = ifcopenshell.open(ifc_path)

    sectors, used_fallback = build_sectors(ifc, scan, rules)
    sector_size = check_sector_size_compliance(sectors, rules, building_use, sprinklers, used_fallback)
    risk_rooms = check_special_risk_rooms(scan, rules)

    overall_ok = (sector_size.get("result") == "PASS")
    return {
        "file_name": scan.get("file_name"),
        "status": bool(overall_ok),
        "color": "green" if overall_ok else "red",
        "counts": scan.get("counts"),
        "data_quality": scan.get("data_quality"),
        "sectors": sectors,
        "si1_sector_size": sector_size,
        "si1_special_risk_rooms": risk_rooms
    }


# ============================================================
# CLI
# ============================================================

def scan_ifc_folder(folder_path: str, config_path: str, recursive: bool = True) -> Dict[str, Any]:
    folder = Path(folder_path)
    pattern = "**/*.ifc" if recursive else "*.ifc"
    files = sorted(folder.glob(pattern))

    results = []
    for f in files:
        results.append(run_si1_checks(str(f), config_path))

    return {
        "folder": str(folder),
        "files_checked": len(files),
        "results": results
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CTE DB-SI SI1 IFC checker (config-driven).")
    parser.add_argument("--ifc", type=str, default=None, help="Path to a single IFC file.")
    parser.add_argument("--folder", type=str, default=None, help="Path to folder containing IFC files.")
    parser.add_argument("--config", type=str, default=DEFAULT_CONFIG_PATH, help="Path to JSON config.")
    parser.add_argument("--recursive", action="store_true", help="Scan folder recursively.")
    args = parser.parse_args()

    if not args.ifc and not args.folder:
        args.folder = r"C:\Users\gorkem\Documents\GitHub\automatic-fire-compliance-checker\00_data\ifc_models"


    if args.ifc:
        out = run_si1_checks(args.ifc, args.config)
    else:
        out = scan_ifc_folder(args.folder, args.config, recursive=args.recursive)

    print(json.dumps(out, indent=2, ensure_ascii=False))
