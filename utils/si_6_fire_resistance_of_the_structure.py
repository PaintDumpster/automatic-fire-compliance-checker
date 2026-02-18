# ─────────────────────────────────────────────
# IMPORTED LIBRARIES
# ─────────────────────────────────────────────
import json # for loading SI 6 Table 3.1 data from external JSON file
import os # for file path handling
import ifcopenshell # for reading IFC files and extracting element properties
import logging # for debug logging throughout the module


# ─────────────────────────────────────────────
# FUNCTION
# ─────────────────────────────────────────────

# Load SI 6 Table 3.1 from external JSON file
# Compute path relative to this module for reliability when imported
_JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "data_push", "si6_table_3_1.json")
_JSON_PATH = os.path.abspath(_JSON_PATH)

with open(_JSON_PATH, "r", encoding="utf-8") as f:
    _SI6_DATA = json.load(f)

SI6_TABLE_3_1 = {
    key: value
    for key, value in _SI6_DATA.items()
    if not key.startswith("_")
}

# Logger for this module
logger = logging.getLogger(__name__)
logger.debug("Loaded SI6_TABLE_3_1 keys: %s", list(SI6_TABLE_3_1.keys()))


# Multilingual mapping for building-use detection (English + Spanish)
BUILDING_USE_MAP = {
    "residential": ["residential", "residencial", "vivienda", "single_family", "single-family", "unifamiliar"],
    "commercial": ["commercial", "comercial", "comercio", "retail"],
    "office": ["office", "oficina", "oficinas"],
    "public_assembly": ["public assembly", "ensamblaje público", "uso público", "publico", "auditorium", "auditorio"],
    "healthcare": ["healthcare", "sanitary", "sanitario", "salud", "hospital"],
    "educational": ["educational", "educativo", "educación", "escuela", "school"],
    "administrative": ["administrative", "administrativo"],
    "car_park_standalone": ["car park", "parking", "aparcamientos", "estacionamiento"],
    "car_park_below_other": ["car park below", "parking below", "aparcamientos bajo"],
    "single_family": ["single family", "vivienda unifamiliar", "single-family"]
}


FIRE_RATING_FIELD_VARIANTS = [
    "firerating",
    "fire_rating",
    "fire rating",
    "resistencia_fuego",
    "resistenciafuego",
    "resistencia_de_fuego",
    "resistencia",
]

import re


def _match_building_use(text):
    if not text:
        return None
    t = text.strip().lower()
    for key, variants in BUILDING_USE_MAP.items():
        for v in variants:
            if v in t:
                logger.debug("Matched building use '%s' -> '%s' (source: %s)", v, key, text)
                return key
    return None


def _normalize_prop_name(name):
    if not name:
        return ""
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _parse_minutes_from_value(val):
    try:
        s = str(val)
    except Exception:
        return None
    # Look for R90 style or just digits
    m = re.search(r"R\s*(\d{1,3})", s, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m2 = re.search(r"(\d{1,3})\s*(min|minutes)?", s, re.IGNORECASE)
    if m2:
        return int(m2.group(1))
    # Last resort: extract any digits
    digits = ''.join(filter(str.isdigit, s))
    if digits:
        logger.debug("Parsed minutes from value '%s' -> %s", s, digits)
        return int(digits)
    logger.debug("Could not parse minutes from value: %s", s)
    return None


def get_default_fire_rating(building_use):
    """Get the default fire rating (minutes) for a building use.
    
    This is used when an element has no FireRating property.
    Falls back to the minimum required rating for the given use.
    """
    try:
        defaults = _SI6_DATA.get("_defaults", {})
        key = _normalize_use_label(building_use)
        default = defaults.get(key)
        if default is not None:
            logger.debug("Using default fire rating for use '%s': %s minutes", building_use, default)
            return default
    except Exception as e:
        logger.debug("Failed to get default fire rating: %s", e)
    return None


def _detect_length_unit_scale(model):
    """Detect length unit used in the IFC `model` and return a multiplier to convert
    values to metres. Returns `None` if detection failed and the caller should
    fall back to heuristics.

    Examples:
        If unit is millimetre -> returns 0.001
        If unit is metre -> returns 1.0
    """
    try:
        projects = model.by_type('IfcProject')
        if not projects:
            return None
        proj = projects[0]
        units = getattr(proj, 'UnitsInContext', None)
        if not units:
            return None
        unit_list = getattr(units, 'Units', [])

        for u in unit_list:
            # SI units with possible prefix
            if u.is_a('IfcSIUnit'):
                if getattr(u, 'UnitType', None) and u.UnitType == 'LENGTHUNIT':
                    # Prefix may be None or an enum like 'MILLI'
                    prefix = getattr(u, 'Prefix', None)
                    name = getattr(u, 'Name', None)
                    # Map prefixes to scale factors
                    prefix_map = {
                        'MILLI': 1e-3,
                        'CENTI': 1e-2,
                        'DECI': 1e-1,
                        'NONE': 1.0,
                        None: 1.0,
                    }
                    # Some Ifc files set Name to METRE
                    scale = prefix_map.get(str(prefix).upper(), 1.0)
                    # If Name indicates metre explicitly, return scale
                    if name and str(name).upper().startswith('METRE'):
                        return scale

            # Conversion-based units (common for mm, inch etc.)
            if u.is_a('IfcConversionBasedUnit'):
                if getattr(u, 'UnitType', None) and u.UnitType == 'LENGTHUNIT':
                    uname = str(getattr(u, 'Name', '')).lower()
                    if 'millimet' in uname or 'millimetre' in uname or 'millimeter' in uname:
                        return 0.001
                    if 'centimet' in uname:
                        return 0.01
                    if 'inch' in uname:
                        return 0.0254
                    if 'foot' in uname:
                        return 0.3048
                    if 'metre' in uname or 'meter' in uname:
                        return 1.0

        # No explicit length unit found
        return None
    except Exception as e:
        logger.debug("Unit detection failed: %s", e)
        return None


def get_height_band(evacuation_height_m, is_basement=False):
    """Converts evacuation height in metres to a table lookup key."""
    if is_basement:
        return "basement"
    elif evacuation_height_m <= 15:
        return "h_le_15"
    elif evacuation_height_m <= 28:
        return "h_le_28"
    else:
        return "h_gt_28"


def _normalize_use_label(label):
    """Normalize user-provided building use labels to JSON keys.

    Examples:
        'Residential' -> 'residential'
        'public assembly' -> 'public_assembly'
    """
    if not label:
        return label
    return label.strip().lower().replace(" ", "_").replace("-", "_")


def get_required_R(building_use, evacuation_height_m, is_basement=False):
    """Returns the required fire resistance in minutes for a given use and height."""
    key = _normalize_use_label(building_use)
    use = SI6_TABLE_3_1.get(key)
    if not use:
        logger.warning("Unrecognised building use '%s' (normalized: '%s'). Valid options: %s", building_use, key, list(SI6_TABLE_3_1.keys()))
        return None
    if "all" in use:
        return use["all"]
    band = get_height_band(evacuation_height_m, is_basement)
    return use.get(band)


def get_fire_rating(element, building_use=None):
    """
    Reads the FireRating property from an IFC element.
    Returns an integer (minutes) or None if not found.
    e.g. 'R90' → 90
    
    If the element has no FireRating property and building_use is provided,
    falls back to the default rating for that building use.
    
    Only accepts properties with fire-rating-related names to avoid false positives
    from generic numeric properties.
    """
    for rel in getattr(element, 'IsDefinedBy', []):
        if rel.is_a("IfcRelDefinesByProperties"):
            pset = rel.RelatingPropertyDefinition
            pset_name = getattr(pset, 'Name', '?')
            if hasattr(pset, "HasProperties"):
                for prop in pset.HasProperties:
                    name = getattr(prop, 'Name', None)
                    nnorm = _normalize_prop_name(name)

                    # Only parse if the property name matches known variants (strict).
                    # This avoids false positives from unrelated numeric properties.
                    if any(v in nnorm for v in FIRE_RATING_FIELD_VARIANTS) or 'fire' in nnorm or 'fuego' in nnorm:
                        val = getattr(prop, 'NominalValue', None) or getattr(prop, 'Value', None)
                        # handle IfcLabel/IfcText wrapping
                        raw = None
                        if hasattr(val, 'wrappedValue'):
                            raw = val.wrappedValue
                        elif val is not None:
                            raw = val
                        minutes = _parse_minutes_from_value(raw)
                        if minutes is not None:
                            logger.debug("Found FireRating: property='%s' pset='%s' value=%s minutes", name, pset_name, minutes)
                            return minutes

    # No explicit FireRating found; try default for building use
    if building_use:
        default = get_default_fire_rating(building_use)
        if default is not None:
            logger.debug("No explicit FireRating for element %s; using default %s minutes for use '%s'", 
                        getattr(element, 'GlobalId', '(no id)'), default, building_use)
            return default

    logger.debug("No FireRating found for element %s", getattr(element, 'GlobalId', '(no id)'))
    return None


def extract_building_use_from_ifc(model):
    """
    Extracts building use from IFC model.
    Looks for IfcBuilding and checks for use classification or metadata.
    Returns a default value if not found.
    """
    buildings = model.by_type("IfcBuilding")
    if buildings:
        building = buildings[0]
        # Try multiple attributes for hints
        candidates = []
        for attr in ("Name", "Description", "ObjectType", "LongName"):
            if hasattr(building, attr) and getattr(building, attr):
                candidates.append(str(getattr(building, attr)))

        # Try classification references if present
        try:
            if hasattr(building, 'IsDefinedBy'):
                for rel in building.IsDefinedBy:
                    if rel.is_a('IfcRelDefinesByProperties'):
                        pset = rel.RelatingPropertyDefinition
                        if hasattr(pset, 'HasProperties'):
                            for prop in pset.HasProperties:
                                val = getattr(prop, 'NominalValue', None) or getattr(prop, 'Value', None)
                                if val is not None:
                                    candidates.append(str(getattr(val, 'wrappedValue', val)))
        except Exception:
            pass

        # Match against multilingual map
        for c in candidates:
            match = _match_building_use(c)
            if match:
                return match

    logger.info("No building use detected in IFC; defaulting to 'residential'")
    return "residential"  # Default fallback


def extract_evacuation_height_from_ifc(model):
    """
    Extracts evacuation height from IFC model.
    Calculates from the highest storey elevation.
    Returns height in metres.
    """
    storeys = model.by_type("IfcBuildingStorey")
    if not storeys:
        return 20.0  # Default fallback

    max_elevation = 0.0
    for storey in storeys:
        if hasattr(storey, "Elevation") and storey.Elevation:
            elevation = float(storey.Elevation)
            if elevation > max_elevation:
                max_elevation = elevation
    # If no elevation data, try to extract from geometry
    if max_elevation == 0.0:
        return 20.0

    # Try to detect project length units precisely from IfcUnitAssignment
    scale = _detect_length_unit_scale(model)
    if scale is not None:
        if scale != 1.0:
            logger.info("Converting elevations using detected length-unit scale: %s -> metres", scale)
        return max_elevation * scale

    # Fallback heuristic: many IFC files store elevations in millimetres.
    # If the extracted elevation looks very large (>1000), assume it is mm and convert to metres.
    if max_elevation > 1000:
        logger.info("Detected elevation %.3f - assuming millimetres, converting to metres (fallback)", max_elevation)
        return max_elevation / 1000.0

    # Otherwise assume elevations are already in metres
    return max_elevation


def check_si6_compliance(ifc_path, building_use, evacuation_height_m, is_basement=False):
    """
    Checks all primary structural elements in an IFC file against
    SI 6 fire resistance requirements.

    Parameters:
        ifc_path            (str)   : path to the .ifc file
        building_use        (str)   : e.g. 'residential', 'commercial'
        evacuation_height_m (float) : building evacuation height in metres
        is_basement         (bool)  : True if checking a basement floor

    Returns:
        dict with keys:
            required_R        — minimum R value required (int, minutes)
            compliant         — list of passing elements
            non_compliant     — list of failing elements
            no_data           — list of elements with no FireRating found
            overall_compliant — True only if no failures and no missing data
    """

    # Open the IFC file
    model = ifcopenshell.open(ifc_path)

    # Determine the required R rating from SI 6 Table 3.1
    required_R = get_required_R(building_use, evacuation_height_m, is_basement)
    if required_R is None:
        return {"error": f"Unrecognised building use: '{building_use}'. "
                         f"Valid options: {list(SI6_TABLE_3_1.keys())}"}

    # Primary structural element types per SI 6 Section 3
    element_types = [
        "IfcBeam",
        "IfcColumn",
        "IfcSlab",
        "IfcMember",
        "IfcWall",
        "IfcFooting",
        "IfcRoof",
        "IfcStair",
        "IfcStairFlight",
        "IfcRailing",
    ]

    # Results report
    results = {
        "required_R":        required_R,
        "building_use":      building_use,
        "evacuation_height": evacuation_height_m,
        "compliant":         [],
        "non_compliant":     [],
        "no_data":           []
    }

    # Loop through every structural element and check its fire rating
    for ifc_type in element_types:
        for element in model.by_type(ifc_type):
            name     = element.Name or element.GlobalId
            actual_R = get_fire_rating(element, building_use)

            entry = {
                "id":         element.GlobalId,
                "name":       name,
                "type":       ifc_type,
                "required_R": required_R,
                "actual_R":   actual_R
            }

            if actual_R is None:
                entry["issue"] = "No FireRating property found"
                results["no_data"].append(entry)

            elif actual_R >= required_R:
                results["compliant"].append(entry)

            else:
                entry["deficit"] = required_R - actual_R
                results["non_compliant"].append(entry)

    # Overall pass only if zero failures AND zero missing data
    results["overall_compliant"] = (
        len(results["non_compliant"]) == 0 and
        len(results["no_data"])       == 0
    )

    return results


# ─────────────────────────────────────────────
# TESTING SPACE
# ─────────────────────────────────────────────

if __name__ == "__main__":

    # Ensure logging is configured when executed as a script
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    # --- Test 1: Auto-detect building use and height from IFC ---
    ifc_path = "00_data/ifc_models/01_Duplex_Apartment.ifc"
    model = ifcopenshell.open(ifc_path)
    building_use = extract_building_use_from_ifc(model)
    evacuation_height_m = extract_evacuation_height_from_ifc(model)

    report = check_si6_compliance(
        ifc_path            = ifc_path,
        building_use        = building_use,
        evacuation_height_m = evacuation_height_m
    )

    print("=" * 50)
    print(f"TEST 1 — {building_use.replace('_', ' ').title()}, {evacuation_height_m:.1f}m")
    print("=" * 50)
    print(f"Required R rating  : R{report['required_R']}")
    print(f"Compliant elements : {len(report['compliant'])}")
    print(f"Non-compliant      : {len(report['non_compliant'])}")
    print(f"Missing data       : {len(report['no_data'])}")
    print(f"Overall compliant  : {report['overall_compliant']}")

    if report["non_compliant"]:
        print("\nFailing elements:")
        for el in report["non_compliant"]:
            print(f"  [{el['type']}] {el['name']} — "
                  f"has R{el['actual_R']}, needs R{el['required_R']} "
                  f"(deficit: {el['deficit']} min)")

    if report["no_data"]:
        print("\nElements with missing fire rating data:")
        for el in report["no_data"]:
            print(f"  [{el['type']}] {el['name']} — {el['issue']}")



