from pathlib import Path
from collections import defaultdict
from typing import Dict, Any, Callable, Optional, List
import re
import json
import argparse
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


def _collect_space_boundary_relationships(ifc_file) -> List[Any]:
    rels: Dict[int, Any] = {}
    for rel_type in ("IfcRelSpaceBoundary", "IfcRelSpaceBoundary1stLevel", "IfcRelSpaceBoundary2ndLevel"):
        try:
            for rel in ifc_file.by_type(rel_type) or []:
                try:
                    rels[rel.id()] = rel
                except Exception:
                    pass
        except Exception:
            continue
    return list(rels.values())


def build_door_space_adjacency(ifc_file) -> Dict[str, List[str]]:
    """
    Return door -> adjacent spaces map from IfcRelSpaceBoundary* relationships.
    If only one adjacent space is found for a door, list has length 1.
    """
    door_to_spaces = defaultdict(set)

    for rel in _collect_space_boundary_relationships(ifc_file):
        space = safe_attr(rel, "RelatingSpace")
        elem = safe_attr(rel, "RelatedBuildingElement")
        if not space or not elem:
            continue
        if not space.is_a("IfcSpace") or not elem.is_a("IfcDoor"):
            continue

        door_guid = safe_attr(elem, "GlobalId") or f"id:{elem.id()}"
        space_guid = safe_attr(space, "GlobalId") or f"id:{space.id()}"
        door_to_spaces[str(door_guid)].add(str(space_guid))

    return {door_guid: sorted(list(space_guids)) for door_guid, space_guids in door_to_spaces.items()}


def _normalize_fire_rating_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _is_valid_fire_rating_value(value: Any) -> bool:
    norm = _normalize_fire_rating_value(value)
    invalid = {"", "-", "n/a", "na", "null", "none"}
    if norm in invalid:
        return False
    if norm in {"fire rating", "firerating"}:
        return False
    return True


def _extract_door_fire_rating_info(door) -> Dict[str, Any]:
    """
    Extract fire rating with provenance and validity.
    Priority: Pset_DoorCommon.FireRating first, then fallbacks.
    """
    if door is None:
        return {
            "property_exists": False,
            "raw_value": None,
            "normalized_value": "",
            "is_valid": False,
            "source": None,
        }

    # 1) Preferred source: door pset Pset_DoorCommon.FireRating
    try:
        psets = get_psets(door)
        pset_dc = psets.get("Pset_DoorCommon", {})
        if isinstance(pset_dc, dict) and "FireRating" in pset_dc:
            raw = pset_dc.get("FireRating")
            return {
                "property_exists": True,
                "raw_value": None if raw is None else str(raw),
                "normalized_value": _normalize_fire_rating_value(raw),
                "is_valid": _is_valid_fire_rating_value(raw),
                "source": "Pset_DoorCommon.FireRating",
            }
    except Exception:
        pass

    # 2) Door direct attribute
    try:
        attr_val = safe_attr(door, "FireRating")
        if attr_val is not None:
            return {
                "property_exists": True,
                "raw_value": str(attr_val),
                "normalized_value": _normalize_fire_rating_value(attr_val),
                "is_valid": _is_valid_fire_rating_value(attr_val),
                "source": "IfcDoor.FireRating",
            }
    except Exception:
        pass

    # 3) Other door pset fallbacks
    try:
        psets = get_psets(door)
        for pset_name, key in (
            ("Pset_DoorWindowGlazingType", "FireRating"),
            ("Pset_DoorCommon", "FireResistanceRating"),
            ("Pset_DoorCommon", "Rating"),
        ):
            pset = psets.get(pset_name, {})
            if isinstance(pset, dict) and key in pset:
                raw = pset.get(key)
                return {
                    "property_exists": True,
                    "raw_value": None if raw is None else str(raw),
                    "normalized_value": _normalize_fire_rating_value(raw),
                    "is_valid": _is_valid_fire_rating_value(raw),
                    "source": f"{pset_name}.{key}",
                }
    except Exception:
        pass

    # 4) Type-based fallbacks
    try:
        for rel in safe_attr(door, "IsDefinedBy", []) or []:
            if not rel.is_a("IfcRelDefinesByType"):
                continue
            dtype = safe_attr(rel, "RelatingType")
            if not dtype:
                continue

            type_attr = safe_attr(dtype, "FireRating")
            if type_attr is not None:
                return {
                    "property_exists": True,
                    "raw_value": str(type_attr),
                    "normalized_value": _normalize_fire_rating_value(type_attr),
                    "is_valid": _is_valid_fire_rating_value(type_attr),
                    "source": "IfcDoorType.FireRating",
                }

            psets_t = get_psets(dtype)
            pset_dc_t = psets_t.get("Pset_DoorCommon", {})
            for key in ("FireRating", "FireResistanceRating", "Rating"):
                if isinstance(pset_dc_t, dict) and key in pset_dc_t:
                    raw = pset_dc_t.get(key)
                    return {
                        "property_exists": True,
                        "raw_value": None if raw is None else str(raw),
                        "normalized_value": _normalize_fire_rating_value(raw),
                        "is_valid": _is_valid_fire_rating_value(raw),
                        "source": f"IfcDoorType.Pset_DoorCommon.{key}",
                    }
    except Exception:
        pass

    return {
        "property_exists": False,
        "raw_value": None,
        "normalized_value": "",
        "is_valid": False,
        "source": None,
    }


def _door_by_guid(ifc_file) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for door in ifc_file.by_type("IfcDoor") or []:
        guid = safe_attr(door, "GlobalId") or f"id:{door.id()}"
        out[str(guid)] = door
    return out


def check_special_risk_boundary_doors(ifc_file, special_risk_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluate doors on special risk room boundaries.
    """
    risk_items = (special_risk_result.get("details") or {}).get("items") or []
    risk_guids = {str(item.get("guid")) for item in risk_items if item.get("guid")}

    adjacency = build_door_space_adjacency(ifc_file)
    if not adjacency:
        return {
            "result": "INCOMPLETE",
            "details": {
                "detected_risk_rooms_count": len(risk_guids),
                "boundary_doors_count": 0,
                "doors": [],
                "messages": ["No IfcRelSpaceBoundary / cannot map doors to spaces"],
            },
        }

    by_guid = _door_by_guid(ifc_file)
    doors: List[Dict[str, Any]] = []
    missing_property_count = 0
    unusable_value_count = 0
    valid_rating_count = 0

    for door_guid, adjacent_spaces in adjacency.items():
        risk_spaces = [sg for sg in adjacent_spaces if sg in risk_guids]
        if not risk_spaces:
            continue

        connects_risk_room_guid = risk_spaces[0]
        other_spaces = [sg for sg in adjacent_spaces if sg != connects_risk_room_guid]
        connects_to_space_guid = other_spaces[0] if other_spaces else None

        rating_info = _extract_door_fire_rating_info(by_guid.get(door_guid)) if door_guid in by_guid else {
            "property_exists": False,
            "raw_value": None,
            "normalized_value": "",
            "is_valid": False,
            "source": None,
        }
        if not rating_info.get("property_exists"):
            missing_property_count += 1
        elif not rating_info.get("is_valid"):
            unusable_value_count += 1
        else:
            valid_rating_count += 1

        doors.append({
            "door_guid": door_guid,
            "adjacent_spaces": list(adjacent_spaces),
            "connects_risk_room_guid": connects_risk_room_guid,
            "connects_to_space_guid": connects_to_space_guid,
            "fire_rating": rating_info.get("raw_value"),
            "fire_rating_source": rating_info.get("source"),
            "fire_rating_normalized": rating_info.get("normalized_value"),
            "fire_rating_property_exists": bool(rating_info.get("property_exists")),
            "fire_rating_is_valid": bool(rating_info.get("is_valid")),
        })

    if not doors:
        return {
            "result": "PASS",
            "details": {
                "detected_risk_rooms_count": len(risk_guids),
                "boundary_doors_count": 0,
                "doors": [],
                "messages": ["No door found adjacent to detected special risk rooms."],
            },
        }

    messages: List[str] = []
    if missing_property_count > 0:
        messages.append(f"{missing_property_count} boundary door(s) are missing FireRating property.")
    if unusable_value_count > 0:
        messages.append(f"{unusable_value_count} boundary door(s) have FireRating field but unusable value.")

    if valid_rating_count == 0:
        result = "INCOMPLETE"
        messages.append("No boundary door has a usable FireRating value.")
    elif missing_property_count > 0 or unusable_value_count > 0:
        result = "FAIL"
    else:
        result = "PASS"

    return {
        "result": result,
        "details": {
            "detected_risk_rooms_count": len(risk_guids),
            "boundary_doors_count": len(doors),
            "valid_rating_count": valid_rating_count,
            "missing_property_count": missing_property_count,
            "unusable_value_count": unusable_value_count,
            "doors": doors,
            "messages": messages,
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
    special_risk_boundary_doors_result = check_special_risk_boundary_doors(ifc, special_risk_result)

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
        "si1_special_risk_boundary_doors": special_risk_boundary_doors_result,
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


def to_row(
    id: str,
    check_result_id: str,
    element_id: Optional[str] = None,
    element_type: Optional[str] = None,
    element_name: Optional[str] = None,
    element_name_long: Optional[str] = None,
    check_status: str = "log",
    actual_value: Optional[str] = None,
    required_value: Optional[str] = None,
    comment: Optional[str] = None,
    log: Optional[str] = None,
) -> Dict[str, Any]:
    allowed = {"pass", "fail", "warning", "blocked", "log"}
    status = check_status if check_status in allowed else "log"
    return {
        "id": str(id),
        "check_result_id": str(check_result_id),
        "element_id": str(element_id) if element_id is not None else None,
        "element_type": str(element_type) if element_type is not None else None,
        "element_name": str(element_name) if element_name is not None else None,
        "element_name_long": str(element_name_long) if element_name_long is not None else None,
        "check_status": status,
        "actual_value": str(actual_value) if actual_value is not None else None,
        "required_value": str(required_value) if required_value is not None else None,
        "comment": str(comment) if comment is not None else None,
        "log": str(log) if log is not None else None,
    }


def _make_event_id(check_result_id: str, element_id: Optional[str], element_name: Optional[str], index: int) -> str:
    anchor = element_id if element_id else (element_name if element_name else "NONE")
    return f"{check_result_id}:{anchor}:{index}"


def _build_events_sector_size(file_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    checks = ((file_result.get("si1_sector_size") or {}).get("details") or {}).get("sector_checks") or []
    detail_messages = ((file_result.get("si1_sector_size") or {}).get("details") or {}).get("messages") or []
    has_sector_labels = bool((file_result.get("data_quality") or {}).get("has_sector_labels"))

    for c in checks:
        sector_id = c.get("sector")
        if c.get("status") == "PASS":
            status = "pass"
        elif c.get("status") == "FAIL":
            status = "fail"
        else:
            status = "blocked"

        area = c.get("area_total_m2")
        limit = c.get("limit_m2")
        comment = c.get("reason") or "Sector size evaluated against configured limit."
        log_msg = " | ".join(str(m) for m in detail_messages) if detail_messages else None
        element_name = str(sector_id) if sector_id is not None else None

        rows.append(
            to_row(
                id=_make_event_id("SI1_SECTOR_SIZE", None, element_name, len(rows)),
                check_result_id="SI1_SECTOR_SIZE",
                element_id=None,
                element_type="IfcZone" if has_sector_labels else "FireSector",
                element_name=element_name,
                element_name_long=None,
                check_status=status,
                actual_value=str(area) if area is not None else None,
                required_value=str(limit) if limit is not None else None,
                comment=comment,
                log=log_msg,
            )
        )
    return rows


def _build_events_special_risk_rooms(file_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    items = ((file_result.get("si1_special_risk_rooms") or {}).get("details") or {}).get("items") or []
    for it in items:
        guid = it.get("guid")
        dtype = it.get("detected_type")
        kws = it.get("matched_keywords") or []
        kws_text = ", ".join(str(k) for k in kws) if kws else "none"
        zones = ", ".join(str(z) for z in (it.get("zones") or [])) if it.get("zones") else "none"

        rows.append(
            to_row(
                id=_make_event_id("SI1_SPECIAL_RISK_ROOM", guid, it.get("name"), len(rows)),
                check_result_id="SI1_SPECIAL_RISK_ROOM",
                element_id=guid,
                element_type="IfcSpace",
                element_name=it.get("name"),
                element_name_long=it.get("long_name"),
                check_status="warning",
                actual_value=f"type={dtype}; keywords=[{kws_text}]",
                required_value="N/A",
                comment="Special risk room detected; manual DB-SI requirement confirmation is needed.",
                log=f"storey={it.get('storey')}; zones={zones}",
            )
        )
    return rows


def _build_events_boundary_doors(file_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    srbd = file_result.get("si1_special_risk_boundary_doors") or {}
    details = srbd.get("details") or {}
    doors = details.get("doors") or []
    messages = details.get("messages") or []

    if srbd.get("result") == "INCOMPLETE" and not doors:
        rows.append(
            to_row(
                id=_make_event_id("SI1_BOUNDARY_DOOR_RATING", None, "SUMMARY", len(rows)),
                check_result_id="SI1_BOUNDARY_DOOR_RATING",
                element_id=None,
                element_type=None,
                element_name=None,
                element_name_long=None,
                check_status="blocked",
                actual_value="doors_checked=0, with_rating=0, missing_rating=0",
                required_value="FireRating required for risk-room boundary door",
                comment="No IfcRelSpaceBoundary, cannot map doors to spaces.",
                log=" | ".join(str(m) for m in messages) if messages else None,
            )
        )
        return rows

    with_rating = 0
    missing_rating = 0
    for d in doors:
        guid = d.get("door_guid")
        rating = d.get("fire_rating")
        source = d.get("fire_rating_source")
        norm = d.get("fire_rating_normalized")
        prop_exists = bool(d.get("fire_rating_property_exists"))
        is_valid = bool(d.get("fire_rating_is_valid"))

        if is_valid:
            with_rating += 1
            status = "pass"
            actual = str(rating) if rating is not None else ""
            comment = "Boundary door has FireRating."
        else:
            missing_rating += 1
            status = "blocked"
            actual = None if rating is None else str(rating)
            if prop_exists:
                comment = "Boundary door has FireRating field but no usable value."
            else:
                comment = "Boundary door is missing FireRating property."

        rows.append(
            to_row(
                id=_make_event_id("SI1_BOUNDARY_DOOR_RATING", guid, None, len(rows)),
                check_result_id="SI1_BOUNDARY_DOOR_RATING",
                element_id=guid,
                element_type="IfcDoor",
                element_name=None,
                element_name_long=None,
                check_status=status,
                actual_value=actual,
                required_value="FireRating required for risk-room boundary door",
                comment=comment,
                log=(
                    f"adjacent_spaces={d.get('adjacent_spaces')}; "
                    f"risk_space={d.get('connects_risk_room_guid')}; "
                    f"target_space={d.get('connects_to_space_guid')}; "
                    f"source={source}; raw={rating}; normalized={norm}"
                ),
            )
        )

    total = len(doors)
    summary_status = "warning" if total == 0 else ("blocked" if missing_rating > 0 else "pass")
    rows.append(
        to_row(
            id=_make_event_id("SI1_BOUNDARY_DOOR_RATING", None, "SUMMARY", len(rows)),
            check_result_id="SI1_BOUNDARY_DOOR_RATING",
            element_id=None,
            element_type=None,
            element_name=None,
            element_name_long=None,
            check_status=summary_status,
            actual_value=f"doors_checked={total}, with_rating={with_rating}, missing_rating={missing_rating}",
            required_value="All risk-room boundary doors must have FireRating",
            comment="Summary of FireRating presence for doors on special risk room boundaries.",
            log=" | ".join(str(m) for m in messages) if messages else None,
        )
    )
    return rows


def build_events(out: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for result in out.get("results", []):
        if result.get("error"):
            rows.append(
                to_row(
                    id=_make_event_id("SI1_LOG", None, str(result.get("file") or "unknown"), len(rows)),
                    check_result_id="SI1_LOG",
                    element_id=None,
                    element_type=None,
                    element_name=str(result.get("file") or "unknown"),
                    element_name_long=None,
                    check_status="blocked",
                    actual_value=None,
                    required_value=None,
                    comment="File scan failed.",
                    log=str(result.get("error")),
                )
            )
            continue

        rows.extend(_build_events_sector_size(result))
        rows.extend(_build_events_special_risk_rooms(result))
        rows.extend(_build_events_boundary_doors(result))
    return rows


def split_events(events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    ok = [e for e in events if e.get("check_status") in {"pass", "warning", "log"}]
    problems = [e for e in events if e.get("check_status") in {"fail", "blocked"}]
    return {"ok": ok, "problems": problems}


def _renumber_event_ids(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, e in enumerate(events):
        check_result_id = str(e.get("check_result_id") or "UNKNOWN")
        anchor = e.get("element_id") or e.get("element_name") or "NONE"
        row = dict(e)
        row["id"] = f"{check_result_id}:{anchor}:{i}"
        out.append(row)
    return out


def run_self_test_boundary_rating(duplex_ifc_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Self-test for SI1 boundary-door FireRating validation on Duplex IFC.
    Confirms unusable FireRating values do not get PASS.
    """
    ifc_path = duplex_ifc_path or r"C:\Users\gorkem\Documents\GitHub\automatic-fire-compliance-checker\00_data\ifc_models\01_Duplex_Apartment.ifc"
    try:
        result = scan_one_ifc(ifc_path)
    except Exception as e:
        return {
            "ok": False,
            "ifc_path": ifc_path,
            "error": str(e),
        }

    events = _renumber_event_ids(build_events({"results": [result]}))
    door_events = [e for e in events if e.get("check_result_id") == "SI1_BOUNDARY_DOOR_RATING" and e.get("element_type") == "IfcDoor"]
    invalid_tokens = {"", "-", "n/a", "na", "null", "none", "fire rating", "firerating"}

    bad_pass = []
    for e in door_events:
        av = e.get("actual_value")
        av_norm = (str(av).strip().lower() if av is not None else "")
        is_invalid = av_norm in invalid_tokens
        if is_invalid and e.get("check_status") == "pass":
            bad_pass.append(e.get("id"))

    return {
        "ok": len(bad_pass) == 0,
        "ifc_path": ifc_path,
        "doors_checked": len(door_events),
        "invalid_value_pass_count": len(bad_pass),
        "invalid_value_pass_event_ids": bad_pass,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CTE DB-SI SI1 interior propagation checker")
    parser.add_argument(
        "--folder",
        type=str,
        default=r"C:\Users\gorkem\Documents\GitHub\automatic-fire-compliance-checker\00_data\ifc_models",
        help="Folder path containing IFC files",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        default=True,
        help="Scan folder recursively (default: true)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Print additional structured terminal report rows",
    )
    parser.add_argument(
        "--events",
        action="store_true",
        help="Print only events JSON array to stdout",
    )
    parser.add_argument(
        "--events_split",
        action="store_true",
        help="With --events, print JSON object wrapper: {\"ok\": [...], \"problems\": [...]}",
    )
    parser.add_argument(
        "--full_report",
        action="store_true",
        help="Print original full nested report JSON",
    )
    parser.add_argument(
        "--self_test_boundary_rating",
        action="store_true",
        help="Run Duplex self-test to verify unusable FireRating never yields PASS",
    )
    args = parser.parse_args()

    if args.self_test_boundary_rating:
        print(json.dumps(run_self_test_boundary_rating(), indent=2, ensure_ascii=False))
        raise SystemExit(0)

    out = scan_folder(args.folder, recursive=args.recursive)
    if args.full_report:
        print(json.dumps(out, indent=2))
        if args.pretty:
            print(json.dumps(_renumber_event_ids(build_events(out)), indent=2, ensure_ascii=False))
    else:
        events = _renumber_event_ids(build_events(out))
        if args.events_split:
            print(json.dumps(split_events(events), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(events, indent=2, ensure_ascii=False))
