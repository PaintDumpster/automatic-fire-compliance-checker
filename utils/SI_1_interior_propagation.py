from pathlib import Path
from collections import defaultdict
from typing import Dict, Any, Callable, Optional, List
import re
import json
import ifcopenshell


DEFAULT_RULES_CONFIG_PATH = Path(
    r"C:\Users\gorkem\Documents\GitHub\automatic-fire-compliance-checker\00_data\config\rulesdb_si_si1_rules.json"
)


def load_rules_config(path: str) -> Dict[str, Any]:
    """
    Load SI1 rules configuration from a JSON file.

    The JSON is expected to contain at least:
      - project_defaults: { building_use, sprinklers, ... }
      - sector_area_limits_m2: { <building_use>: { ... } or number }
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # Robust fallback: return empty dict; callers must handle INCOMPLETE
        return {}


# ---------- helpers ----------
def safe_attr(el, name, default=None):
    try:
        return getattr(el, name)
    except Exception:
        return default

def build_storey_map(ifc):
    m = {}

    # 1) RelContainedInSpatialStructure (often for elements)
    for rel in ifc.by_type("IfcRelContainedInSpatialStructure") or []:
        container = rel.RelatingStructure
        if container and container.is_a("IfcBuildingStorey"):
            sname = getattr(container, "Name", None) or "Unknown"
            for el in rel.RelatedElements or []:
                m[el.id()] = sname

    # 2) RelAggregates (often for spaces)
    for rel in ifc.by_type("IfcRelAggregates") or []:
        parent = rel.RelatingObject
        if parent and parent.is_a("IfcBuildingStorey"):
            sname = getattr(parent, "Name", None) or "Unknown"
            for child in rel.RelatedObjects or []:
                # some exporters aggregate spaces here
                m[child.id()] = sname

    return m

def get_psets(el):
    try:
        from ifcopenshell.util.element import get_psets
        return get_psets(el) or {}
    except Exception:
        return {}


def _area_from_geometry_bbox(space) -> Optional[float]:
    """
    Very rough geometric fallback: compute 2D bounding-box area of the space
    using ifcopenshell.geom. This is only used when no quantity/pset area is
    available and should be treated as an approximation.
    """
    try:
        import ifcopenshell.geom as geom  # type: ignore
    except Exception:
        return None

    try:
        settings = geom.settings()
        shape = geom.create_shape(settings, space)
        verts = shape.geometry.verts  # flat list [x1,y1,z1,x2,y2,z2,...]
        if not verts:
            return None

        xs = verts[0::3]
        ys = verts[1::3]
        if not xs or not ys:
            return None

        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        area = (maxx - minx) * (maxy - miny)
        if area <= 0:
            return None
        return float(area)
    except Exception:
        return None


def get_space_area(space):
    """
    Best-effort area extraction for a space.

    Returns:
        (area_m2: Optional[float], method: str, warning: Optional[str])

    method:
        'quantity'  - from IfcElementQuantity
        'pset'      - from common psets (Pset_SpaceCommon, BaseQuantities, ...)
        'geom'      - from geometry bounding box (approximate)
        'none'      - no area available
    """
    # 1) quantities
    try:
        for rel in space.IsDefinedBy or []:
            if not rel.is_a("IfcRelDefinesByProperties"):
                continue
            qset = rel.RelatingPropertyDefinition
            if qset and qset.is_a("IfcElementQuantity"):
                for q in qset.Quantities or []:
                    if q.is_a("IfcQuantityArea") and hasattr(q, "AreaValue"):
                        return float(q.AreaValue), "quantity", None
    except Exception:
        pass

    # 2) psets common
    p = get_psets(space)
    for pset_name in ("Pset_SpaceCommon", "BaseQuantities", "Qto_SpaceBaseQuantities"):
        d = p.get(pset_name, {})
        if not isinstance(d, dict):
            continue
        for key in ("NetFloorArea", "GrossFloorArea", "Area"):
            if key in d and d[key] is not None:
                try:
                    return float(d[key]), "pset", None
                except Exception:
                    pass

    # 3) fallback: geometry-based approximation (bounding box)
    geom_area = _area_from_geometry_bbox(space)
    if geom_area is not None:
        return geom_area, "geom", "Area approximated from geometry bounding box; verify against model."

    # 4) no area
    return None, "none", None

def get_space_zones(space):
    zones = []
    for rel in getattr(space, "HasAssignments", []) or []:
        if rel.is_a("IfcRelAssignsToGroup"):
            grp = rel.RelatingGroup
            if grp and grp.is_a("IfcZone"):
                zones.append(safe_attr(grp, "Name", "UnnamedZone") or "UnnamedZone")
    return zones


def _norm_text(s: Optional[str]) -> str:
    return (s or "").strip().lower()

DEFAULT_SECTOR = "SECTOR_1"


def get_sector_id(space):
    """
    Determine fire sector / compartment ID for a space.

    Priority:
      1) IfcZone membership
      2) Property set value on space (FireCompartment / Sector / SI_Sector...)
      3) Regex on Name / LongName / ObjectType
      4) Fallback single sector

    Returns:
      (sector_id: str, method: str)
    """
    # 1) IfcZone membership (strongest)
    try:
        for rel in getattr(space, "HasAssignments", []) or []:
            if not rel.is_a("IfcRelAssignsToGroup"):
                continue
            grp = rel.RelatingGroup
            if grp and grp.is_a("IfcZone"):
                zname = safe_attr(grp, "Name") or safe_attr(grp, "LongName")
                label = (zname or "").strip()
                if label:
                    return label, "zone:IfcZone"
    except Exception:
        pass

    # 2) Property set on space
    try:
        psets = get_psets(space)
        # Keys we consider as potential sector identifiers
        sector_keys = {
            "firecompartment",
            "fire_compartment",
            "firecompartmentid",
            "fire_sector",
            "firesector",
            "sector",
            "si_sector",
            "compartment",
        }
        for pset_name, props in psets.items():
            if not isinstance(props, dict):
                continue
            for key, value in props.items():
                if value is None:
                    continue
                key_l = str(key).lower()
                if key_l in sector_keys:
                    label = str(value).strip()
                    if label:
                        return label, f"pset:{pset_name}.{key}"
    except Exception:
        pass

    # 3) Regex on name / object type / long name
    try:
        texts = [
            safe_attr(space, "Name") or "",
            safe_attr(space, "LongName") or "",
            safe_attr(space, "ObjectType") or "",
        ]
        combined = " ".join(t for t in texts if t)
        if combined:
            patterns = [
                r"\b[Ss]ector\s*[A-Za-z0-9_-]+",   # "Sector 1", "sector A", "Sector-01"
                r"\bSC[-_ ]?[A-Za-z0-9]+",         # "SC-01", "SC_1"
                r"\bSEC[-_ ]?[A-Za-z0-9]+",        # "SEC-1"
            ]
            for pat in patterns:
                m = re.search(pat, combined)
                if m:
                    label = m.group(0).strip()
                    if label:
                        return label, f"name_regex:{pat}"
    except Exception:
        pass

    # 4) Fallback: single sector for whole building
    return DEFAULT_SECTOR, "fallback:single_sector"


# ============================================================
# Special Risk Rooms (Locales de riesgo especial)
# ============================================================

def check_special_risk_rooms(spaces: List[Dict[str, Any]], rules: Dict[str, Any]) -> Dict[str, Any]:
    """
    Detect and classify special risk rooms based on configuration.

    Configuration (in rules JSON):

    special_risk_rooms: {
      "keywords": {
        "<risk_type>": ["kw1", "kw2", ...] | { "keywords": [...] }
      },
      "thresholds": {
        "<risk_type>": {
          "area_m2": {
            "low": <float>,
            "medium": <float>,
            "high": <float>
          }
        }
      }
    }

    Returns:
        {
          "result": "PASS" | "FAIL" | "INCOMPLETE",
          "color": "green" | "red" | "yellow",
          "details": {
            "detected_count": int,
            "items": [...],
          },
        }
    """
    sr_cfg = rules.get("special_risk_rooms") or {}
    keywords_cfg = sr_cfg.get("keywords") or {}
    thresholds_cfg = sr_cfg.get("thresholds") or {}

    # If no config for special risk rooms, treat as INCOMPLETE
    if not keywords_cfg:
        return {
            "result": "INCOMPLETE",
            "color": "yellow",
            "details": {
                "detected_count": 0,
                "items": [],
                "messages": ["No special_risk_rooms.keywords configured."],
            },
        }

    items: List[Dict[str, Any]] = []
    any_missing_area_or_volume = False
    any_high_risk = False

    # Precompute normalized keyword set for debug purposes (long_name hits)
    all_keyword_norms: List[str] = []
    for kw_cfg in keywords_cfg.values():
        if isinstance(kw_cfg, dict):
            kw_list = kw_cfg.get("keywords", [])
        else:
            kw_list = kw_cfg
        for kw in kw_list or []:
            kw_norm = (str(kw) or "").strip().lower()
            if kw_norm and kw_norm not in all_keyword_norms:
                all_keyword_norms.append(kw_norm)

    debug_long_name_hits = 0

    # Debug: print basic keyword config information
    try:
        print(f"[SI1][special_risk] keyword groups: {len(keywords_cfg)}")
        kitchen_cfg = keywords_cfg.get("kitchen")
        if isinstance(kitchen_cfg, dict):
            kitchen_kw_list = kitchen_cfg.get("keywords", []) or []
        else:
            kitchen_kw_list = kitchen_cfg or []
        print(f"[SI1][special_risk] kitchen sample keywords: {kitchen_kw_list[:3]}")
    except Exception:
        pass

    for sp in spaces:
        # Collect searchable text
        name = sp.get("name") or ""
        long_name = sp.get("long_name") or ""
        object_type = sp.get("object_type") or ""
        zones = sp.get("zones") or []
        description = sp.get("description") or ""

        parts = [
            _norm_text(name),
            _norm_text(long_name),
            _norm_text(object_type),
        ]
        parts.extend(_norm_text(z) for z in zones if z)
        parts.append(_norm_text(description))
        text = " | ".join(p for p in parts if p)
        if not text:
            continue

        # Debug: long_name contains any risk keyword?
        ln_norm = _norm_text(long_name)
        if ln_norm and any(kw in ln_norm for kw in all_keyword_norms):
            debug_long_name_hits += 1

        # Debug: print searchable text for specific test spaces (e.g. A103, B103)
        try:
            if name in ("A103", "B103"):
                print(f"[SI1][special_risk] space {name} searchable text: {text}")
        except Exception:
            pass

        detected_type: Optional[str] = None
        matched_keywords: List[str] = []

        # Keyword matching, fully driven by config
        for risk_type, kw_cfg in keywords_cfg.items():
            if isinstance(kw_cfg, dict):
                kw_list = kw_cfg.get("keywords", [])
            else:
                kw_list = kw_cfg

            local_matches: List[str] = []
            for kw in kw_list or []:
                kw_norm = _norm_text(str(kw))
                if not kw_norm:
                    continue
                if kw_norm in text:
                    local_matches.append(str(kw))

            if local_matches:
                detected_type = risk_type
                matched_keywords = local_matches
                # First matching type wins; can be adjusted if needed
                break

        if not detected_type:
            continue

        area_m2 = sp.get("area_m2")
        volume_m3 = sp.get("volume_m3")
        storey = sp.get("storey")

        # Track missing metrics for INCOMPLETE decision
        if area_m2 is None or volume_m3 is None:
            any_missing_area_or_volume = True

        # Risk classification (LOW / MEDIUM / HIGH / UNKNOWN)
        risk_level = "UNKNOWN"
        area_value: Optional[float] = None
        try:
            if area_m2 is not None:
                area_value = float(area_m2)
        except Exception:
            area_value = None

        th_type = thresholds_cfg.get(detected_type, {})
        area_th = th_type.get("area_m2", {}) if isinstance(th_type, dict) else {}

        if area_value is not None and area_th:
            low_t = area_th.get("low")
            med_t = area_th.get("medium")
            high_t = area_th.get("high")

            try:
                if high_t is not None and area_value > float(high_t):
                    risk_level = "HIGH"
                elif med_t is not None and area_value > float(med_t):
                    risk_level = "MEDIUM"
                elif low_t is not None and area_value >= float(low_t):
                    risk_level = "LOW"
                else:
                    # thresholds exist but area is below lowest; treat as LOW by default
                    risk_level = "LOW"
            except Exception:
                risk_level = "UNKNOWN"
        elif area_value is not None and not area_th:
            # Area known but no thresholds configured for this type
            risk_level = "UNKNOWN"

        if risk_level == "HIGH":
            any_high_risk = True

        items.append({
            "guid": sp.get("guid"),
            "name": name,
            "long_name": long_name,
            "object_type": object_type,
            "storey": storey,
            "zones": zones,
            "detected_type": detected_type,
            "matched_keywords": matched_keywords,
            "area_m2": area_m2,
            "volume_m3": volume_m3,
            "risk_level": risk_level,
        })

    detected_count = len(items)

    # Determine result & color
    if detected_count == 0:
        result = "PASS"
        color = "green"
    else:
        if any_high_risk:
            result = "FAIL"
            color = "red"
        elif any_missing_area_or_volume:
            result = "INCOMPLETE"
            color = "yellow"
        else:
            result = "PASS"
            color = "green"

    return {
        "result": result,
        "color": color,
        "details": {
            "detected_count": detected_count,
            "items": items,
            "debug_long_name_keyword_hit_count": debug_long_name_hits,
        },
    }
# ---------- core ----------
def scan_one_ifc(ifc_path: str, exclude_space_predicate: Optional[Callable[[Dict[str, Any]], bool]] = None):
    ifc = ifcopenshell.open(ifc_path)

    storey_map = build_storey_map(ifc)
    spaces = ifc.by_type("IfcSpace") or []

    space_rows = []
    sector_map = {}
    sector_method = {}

    for sp in spaces:
        sid, method = get_sector_id(sp)
        sector_map[sp.id()] = sid
        sector_method[sp.id()] = method

        area, area_method, area_warning = get_space_area(sp)

        space_rows.append({
            "guid": safe_attr(sp, "GlobalId"),
            "name": safe_attr(sp, "Name") or "Unnamed",
            "long_name": safe_attr(sp, "LongName"),
            "object_type": safe_attr(sp, "ObjectType"),
            "description": safe_attr(sp, "Description"),
            "storey": storey_map.get(sp.id()),
            "area_m2": area,
            "area_method": area_method,
            "area_warning": area_warning,
            "sector": sid,
            "sector_method": method,
            "zones": get_space_zones(sp),
        })

    # sector metrics
    sector_stats = defaultdict(lambda: {
        "area_total": 0.0,
        "spaces": 0,
        "missing_area_spaces": 0,
        "excluded_spaces": 0,
        "by_storey": defaultdict(float),
    })

    for r in space_rows:
        s = r["sector"]
        stat = sector_stats[s]
        stat["spaces"] += 1

        excluded = False
        if exclude_space_predicate is not None:
            try:
                excluded = bool(exclude_space_predicate(r))
            except Exception:
                excluded = False
        r["excluded_from_sector_area"] = excluded

        if excluded:
            stat["excluded_spaces"] += 1
            continue

        a = r["area_m2"]
        if a is None:
            stat["missing_area_spaces"] += 1
        else:
            stat["area_total"] += a
            if r["storey"]:
                stat["by_storey"][r["storey"]] += a

    # ---------- Convert sector stats to normal dict ----------
    sector_stats_out = {}
    for s, v in sector_stats.items():
        sector_stats_out[s] = {
            "spaces": v["spaces"],
            "area_total": v["area_total"],
            "missing_area_spaces": v["missing_area_spaces"],
            "by_storey": dict(v["by_storey"])
        }

    # ---------- Sector Size Compliance + Special Risk Rooms ----------
    rules = load_rules_config(str(DEFAULT_RULES_CONFIG_PATH))
    sector_size_result = check_sector_size_compliance(sector_stats_out, rules)
    special_risk_result = check_special_risk_rooms(space_rows, rules)

    # ---------- Final Output ----------
    return {
        "file": Path(ifc_path).name,
        "path": ifc_path,

        "counts": {
            "IfcSpace": len(spaces),
            "IfcDoor": len(ifc.by_type("IfcDoor") or []),
            "IfcWall": len(ifc.by_type("IfcWall") or []),
            "IfcBuildingStorey": len(ifc.by_type("IfcBuildingStorey") or []),
        },

        "data_quality": {
            "has_spaces": len(spaces) > 0,
            "has_any_space_area": any(r["area_m2"] is not None for r in space_rows),

            # real sector labels only (not fallback)
            "has_sector_labels": any(
                r["sector_method"] != "fallback:single_sector"
                for r in space_rows
            ),

            "used_sector_fallback": any(
                r["sector_method"] == "fallback:single_sector"
                for r in space_rows
            ),
        },

        "spaces": space_rows,
        "spaces_preview": space_rows[:20],
        "sectors": sector_stats_out,

        #  SI1 compliance result
        "si1_sector_size": sector_size_result,
        "si1_special_risk_rooms": special_risk_result,
    }


def check_sector_size_compliance(sectors: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
    """
    Check each sector's total area against a DB-SI style limit loaded from config.

    The `rules` dict is expected to contain:
      - project_defaults: { building_use, sprinklers, ... }
      - sector_area_limits_m2: { <building_use>: number | { base_limit_m2, allow_double_if_sprinklers, ... } }

    Returns:
        {
            "result": "PASS" | "FAIL" | "INCOMPLETE",
            "color": "green" | "red" | "yellow",
            "details": {...}
        }
    """
    project_defaults = rules.get("project_defaults") or {}
    sector_limits_cfg = rules.get("sector_area_limits_m2") or {}

    building_use = project_defaults.get("building_use")
    sprinklers = bool(project_defaults.get("sprinklers", False))

    messages: list[str] = []

    if not building_use:
        messages.append("Missing project_defaults.building_use in rules configuration.")

    # Determine base limit and allow_double_if_sprinklers
    base_limit: Optional[float] = None
    allow_double = False

    if building_use and building_use in sector_limits_cfg:
        cfg_entry = sector_limits_cfg[building_use]
        if isinstance(cfg_entry, (int, float)):
            base_limit = float(cfg_entry)
            allow_double = bool(
                rules.get("allow_double_if_sprinklers")
                or project_defaults.get("allow_double_if_sprinklers")
            )
        elif isinstance(cfg_entry, dict):
            # Try multiple common keys to avoid hard-coding one schema
            for key in ("base_limit_m2", "limit_m2", "base", "limit"):
                if key in cfg_entry and cfg_entry[key] is not None:
                    try:
                        base_limit = float(cfg_entry[key])
                        break
                    except Exception:
                        continue
            allow_double = bool(
                cfg_entry.get("allow_double_if_sprinklers")
                or rules.get("allow_double_if_sprinklers")
                or project_defaults.get("allow_double_if_sprinklers")
            )
    else:
        if building_use:
            messages.append(
                f"No sector_area_limits_m2 entry for building_use='{building_use}'."
            )

    if base_limit is None:
        messages.append("No usable base sector area limit found in configuration.")

    effective_limit: Optional[float] = None
    if base_limit is not None:
        effective_limit = base_limit
        if sprinklers and allow_double:
            effective_limit = base_limit * 2.0

    per_sector = []
    any_fail = False
    any_incomplete = False

    for sector_id, sdata in sectors.items():
        area = sdata.get("area_total")
        if area is None or effective_limit is None:
            any_incomplete = True
            per_sector.append({
                "sector": sector_id,
                "area_total_m2": area,
                "limit_m2": effective_limit,
                "status": "INCOMPLETE",
                "reason": "Missing area_total or effective_limit_m2.",
            })
            continue

        is_ok = area <= effective_limit
        if not is_ok:
            any_fail = True

        per_sector.append({
            "sector": sector_id,
            "area_total_m2": area,
            "limit_m2": effective_limit,
            "status": "PASS" if is_ok else "FAIL",
        })

    # Determine overall result/color
    if any_fail:
        result = "FAIL"
        color = "red"
    elif any_incomplete or base_limit is None or building_use is None:
        result = "INCOMPLETE"
        color = "yellow"
    else:
        result = "PASS"
        color = "green"

    details: Dict[str, Any] = {
        "building_use": building_use,
        "sprinklers": sprinklers,
        "base_limit_m2": base_limit,
        "effective_limit_m2": effective_limit,
        "allow_double_if_sprinklers": allow_double,
        "sector_checks": per_sector,
    }
    if messages:
        details["messages"] = messages

    return {
        "result": result,
        "color": color,
        "details": details,
    }

def scan_folder(folder_path: str, recursive: bool = True):
    folder = Path(folder_path)
    pattern = "**/*.ifc" if recursive else "*.ifc"
    files = sorted(folder.glob(pattern))

    results = []
    for f in files:
        try:
            results.append(scan_one_ifc(str(f)))
        except Exception as e:
            results.append({"file": f.name, "path": str(f), "error": str(e)})

    return {"folder": str(folder), "files_checked": len(files), "results": results}

if __name__ == "__main__":
    import json
    folder_path = r"C:\Users\gorkem\Documents\GitHub\automatic-fire-compliance-checker\00_data\ifc_models"
    out = scan_folder(folder_path, recursive=True)
    print(json.dumps(out, indent=2))