# ─────────────────────────────────────────────
# IMPORTED LIBRARIES
# ─────────────────────────────────────────────
import json # for loading SI 6 Table 3.1 data from external JSON file
import os # for file path handling
import ifcopenshell # for reading IFC files and extracting element properties
import logging # for debug logging throughout the module
import sys # for command line arguments
import uuid # for generating unique IDs
from pathlib import Path # for path handling
from typing import Dict, List, Any, Optional # for type hints
from datetime import datetime # for timestamps


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
    "fireratingvalue",
    "fire_rating_minutes",
]

import re # for parsing minutes from FireRating values

# Structural element types to extract
STRUCTURAL_ELEMENT_TYPES = [
    "IfcBeam",
    "IfcColumn",
    "IfcSlab",
    "IfcWall",
    "IfcFooting",
    "IfcRoof",
    "IfcStair",
    "IfcStairFlight",
    "IfcRailing",
    "IfcMember",
]


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


def get_default_fire_rating(building_use, element_type=None):
    """Get the default fire rating (minutes) for a building use and optional element type.
    
    This is used when an element has no FireRating property.
    Falls back from element-type-specific rating to generic building use rating.
    
    Priority:
        1. si6_table_3_1.json — element type specific (e.g., IfcColumn for residential)
        2. si6_table_3_1.json — generic rating for building use
        3. Legacy fallback — simple defaults for building use
    """
    key = _normalize_use_label(building_use)
    
    # Try element-type-specific rating from _default_fire_ratings_by_element_type
    if element_type:
        try:
            element_types_ratings = _SI6_DATA.get("_default_fire_ratings_by_element_type", {})
            use_ratings = element_types_ratings.get(key, {})
            if element_type in use_ratings:
                rating = use_ratings[element_type]
                logger.debug("Found element-type-specific fire rating for %s/%s: %s minutes", key, element_type, rating)
                return rating
        except Exception as e:
            logger.debug("Failed to get element-type-specific rating: %s", e)
    
    # Try generic rating for building use in _default_fire_ratings_by_element_type
    try:
        element_types_ratings = _SI6_DATA.get("_default_fire_ratings_by_element_type", {})
        use_ratings = element_types_ratings.get(key, {})
        if "generic" in use_ratings:
            rating = use_ratings["generic"]
            logger.debug("Found generic fire rating for %s: %s minutes", key, rating)
            return rating
    except Exception as e:
        logger.debug("Failed to get generic rating: %s", e)
    
    # Fallback to simple _defaults
    try:
        defaults = _SI6_DATA.get("_defaults", {})
        default = defaults.get(key)
        if default is not None:
            logger.debug("Using fallback default fire rating for use '%s': %s minutes", building_use, default)
            return default
    except Exception as e:
        logger.debug("Failed to get default fire rating: %s", e)
    
    logger.debug("No default fire rating found for use '%s'", building_use)
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


# ─────────────────────────────────────────────
# STRUCTURAL ELEMENTS EXTRACTION FUNCTIONS
# ─────────────────────────────────────────────

def extract_fire_rating_from_element(element) -> Optional[str]:
    """
    Extract fire rating value from element properties.
    
    Looks for fire rating in:
    1. Custom properties
    2. Type properties
    3. Material properties
    """
    try:
        # Check if element has properties
        if not hasattr(element, 'IsDefinedBy'):
            return None
        
        for rel in element.IsDefinedBy:
            # Check property sets
            if rel.is_a('IfcRelDefinesByProperties'):
                pset = rel.RelatingPropertyDefinition
                if hasattr(pset, 'HasProperties'):
                    for prop in pset.HasProperties:
                        prop_name = getattr(prop, 'Name', '').lower().replace(' ', '_').replace('-', '_')
                        
                        # Check if property name matches fire rating variants
                        for variant in FIRE_RATING_FIELD_VARIANTS:
                            if variant in prop_name:
                                val = getattr(prop, 'NominalValue', None) or getattr(prop, 'Value', None)
                                if val is not None:
                                    value_str = str(getattr(val, 'wrappedValue', val))
                                    logger.debug(f"Found fire rating: {value_str} in property: {prop_name}")
                                    return value_str
    except Exception as e:
        logger.debug(f"Error extracting fire rating: {e}")
    
    return None


def get_element_name(element) -> tuple[str, str]:
    """
    Extract element name and long name.
    
    Returns:
        (short_name, long_name)
    """
    short_name = ""
    long_name = ""
    
    try:
        # Try Name attribute first
        if hasattr(element, 'Name') and element.Name:
            short_name = str(element.Name)
            long_name = short_name
        
        # Try LongName if available
        if hasattr(element, 'LongName') and element.LongName:
            long_name = str(element.LongName)
        
        # If no name, try ObjectType or Description
        if not short_name:
            if hasattr(element, 'ObjectType') and element.ObjectType:
                short_name = str(element.ObjectType)
            elif hasattr(element, 'Description') and element.Description:
                short_name = str(element.Description)
        
        if not long_name and short_name:
            long_name = short_name
    except Exception as e:
        logger.debug(f"Error getting element name: {e}")
    
    return short_name or "Unknown", long_name or short_name or "Unknown"


def get_element_properties(element, element_type: str) -> Dict[str, Any]:
    """
    Extract all relevant properties from an element.
    """
    properties = {
        "id": str(element.id()),
        "type": element_type,
        "ifc_type": element.get_info()['type'],
        "guid": getattr(element, 'GlobalId', ''),
    }
    
    # Get name
    short_name, long_name = get_element_name(element)
    properties["name"] = short_name
    properties["name_long"] = long_name
    
    # Get fire rating
    fire_rating = extract_fire_rating_from_element(element)
    properties["fire_rating"] = fire_rating
    
    # Get material information
    try:
        if hasattr(element, 'Material'):
            material = element.Material
            if material:
                properties["material"] = str(material)
    except Exception as e:
        logger.debug(f"Error getting material: {e}")
    
    # Get connected elements (for structural relationships)
    try:
        if hasattr(element, 'ConnectedFrom'):
            properties["connected_from"] = [str(rel.RelatingElement) for rel in element.ConnectedFrom]
        if hasattr(element, 'ConnectedTo'):
            properties["connected_to"] = [str(rel.RelatedElement) for rel in element.ConnectedTo]
    except Exception as e:
        logger.debug(f"Error getting connections: {e}")
    
    return properties


def extract_structural_elements_from_ifc(ifc_path: str) -> List[Dict[str, Any]]:
    """
    Extract all structural elements from an IFC file.
    
    Args:
        ifc_path: Path to IFC file
    
    Returns:
        List of element data dictionaries
    """
    elements_data = []
    
    try:
        logger.info(f"Loading IFC file: {ifc_path}")
        model = ifcopenshell.open(ifc_path)
        
        # Get project name
        projects = model.by_type("IfcProject")
        project_name = projects[0].Name if projects else "Unknown"
        
        # Extract each structural element type
        for element_type in STRUCTURAL_ELEMENT_TYPES:
            try:
                elements = model.by_type(element_type)
                logger.info(f"Found {len(elements)} {element_type} elements")
                
                for element in elements:
                    try:
                        element_data = get_element_properties(element, element_type)
                        element_data["ifc_file"] = os.path.basename(ifc_path)
                        element_data["project_name"] = project_name
                        elements_data.append(element_data)
                    except Exception as e:
                        logger.error(f"Error processing {element_type}: {e}")
                        continue
            except Exception as e:
                logger.debug(f"No {element_type} elements found or error: {e}")
                continue
        
        logger.info(f"Total elements extracted from {ifc_path}: {len(elements_data)}")
        return elements_data
    
    except Exception as e:
        logger.error(f"Error processing IFC file {ifc_path}: {e}")
        return []


def generate_check_results(elements_data: List[Dict[str, Any]], start_index: int = 0) -> List[Dict[str, Any]]:
    """
    Convert element data to check result format matching the reference image.
    
    Format includes:
    - id: unique check ID
    - check_result_id: unique result ID
    - element_id: element ID
    - element_type: IFC type
    - element_name: short name
    - element_name_long: long name
    - check_status: pass/fail/unknown
    - actual_value: actual fire rating
    - required_value: required fire rating (placeholder)
    - comment: notes about the element
    - log: logging information
    
    Args:
        elements_data: List of element data dictionaries
        start_index: Starting index for CHECK IDs (for appending to existing data)
    """
    check_results = []
    
    for idx, element in enumerate(elements_data, start=start_index):
        check_id = f"CHECK_{str(idx).zfill(6)}"
        result_id = str(uuid.uuid4())
        
        # Determine check status based on fire rating
        fire_rating = element.get("fire_rating")
        if fire_rating:
            check_status = "pass"
            actual_value = fire_rating
        else:
            check_status = "unknown"
            actual_value = "Not specified"
        
        check_result = {
            "id": check_id,
            "check_result_id": result_id,
            "element_id": element["guid"],
            "element_type": element["ifc_type"],
            "element_name": element["name"],
            "element_name_long": element["name_long"],
            "check_status": check_status,
            "actual_value": actual_value,
            "required_value": "Depends on building use",
            "comment": f"Element: {element['name']} | Material: {element.get('material', 'Unknown')}",
            "log": f"Extracted from {element['ifc_file']} | Project: {element['project_name']}",
            "metadata": {
                "ifc_element_id": element["id"],
                "ifc_file": element["ifc_file"],
                "project_name": element["project_name"],
                "element_type": element["type"],
                "extracted_at": datetime.now().isoformat()
            }
        }
        
        check_results.append(check_result)
    
    return check_results


def extract_all_structural_elements(ifc_models_dir: str, output_file: str) -> None:
    """
    Extract structural elements from all IFC files in a directory and save to JSON.
    
    Args:
        ifc_models_dir: Directory containing IFC files
        output_file: Path to output JSON file
    """
    all_elements = []
    
    # Find all IFC files
    ifc_dir = Path(ifc_models_dir)
    ifc_files = list(ifc_dir.glob("*.ifc")) + list(ifc_dir.glob("*.IFC"))
    
    logger.info(f"Found {len(ifc_files)} IFC files in {ifc_models_dir}")
    
    # Extract elements from each IFC file
    for ifc_file in ifc_files:
        elements = extract_structural_elements_from_ifc(str(ifc_file))
        all_elements.extend(elements)
    
    # Generate check results in reference format
    check_results = generate_check_results(all_elements)
    
    # Save to JSON
    output_data = {
        "metadata": {
            "title": "Structural Elements Database",
            "description": "All structural elements extracted from IFC models",
            "extracted_at": datetime.now().isoformat(),
            "total_elements": len(check_results),
            "ifc_files_processed": len(ifc_files),
        },
        "elements": check_results
    }
    
    # Ensure output directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Saved {len(check_results)} structural elements to {output_file}")
    
    # Print summary
    print("\n" + "="*60)
    print("STRUCTURAL ELEMENTS EXTRACTION SUMMARY")
    print("="*60)
    print(f"Total elements extracted: {len(check_results)}")
    print(f"IFC files processed: {len(ifc_files)}")
    print(f"Elements with fire rating: {sum(1 for e in check_results if e['actual_value'] != 'Not specified')}")
    print(f"Elements without fire rating: {sum(1 for e in check_results if e['actual_value'] == 'Not specified')}")
    print(f"Output saved to: {output_file}")
    print("="*60 + "\n")


def extract_structural_elements_single_file(ifc_file_path: str, output_file: str) -> None:
    """
    Extract structural elements from a single IFC file and append to JSON database.
    
    Args:
        ifc_file_path: Path to specific IFC file
        output_file: Path to output JSON file
    """
    if not Path(ifc_file_path).exists():
        print(f"Error: IFC file not found: {ifc_file_path}")
        return
    
    logger.info(f"Processing single IFC file: {ifc_file_path}")
    
    # Extract elements from the specific file
    elements = extract_structural_elements_from_ifc(str(ifc_file_path))
    
    # Determine start index for CHECK IDs
    start_index = 0
    if Path(output_file).exists():
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
            start_index = len(existing_data.get("elements", []))
        except Exception as e:
            logger.warning(f"Could not read existing data: {e}")
    
    check_results = generate_check_results(elements, start_index=start_index)
    
    # Load existing data or create new
    if Path(output_file).exists():
        with open(output_file, 'r', encoding='utf-8') as f:
            existing_data = json.load(f)
        
        # Append new results
        existing_data["elements"].extend(check_results)
        existing_data["metadata"]["total_elements"] = len(existing_data["elements"])
        existing_data["metadata"]["last_updated"] = datetime.now().isoformat()
        existing_data["metadata"]["ifc_files_processed"] = existing_data["metadata"].get("ifc_files_processed", 1) + 1
        
        output_data = existing_data
        logger.info(f"Updated existing database with {len(check_results)} new elements")
    else:
        output_data = {
            "metadata": {
                "title": "Structural Elements Database",
                "description": "All structural elements extracted from IFC models",
                "created_at": datetime.now().isoformat(),
                "total_elements": len(check_results),
                "ifc_files_processed": 1,
            },
            "elements": check_results
        }
        logger.info(f"Created new database with {len(check_results)} elements")
    
    # Save updated data
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    
    print("\n" + "="*60)
    print("SINGLE IFC FILE EXTRACTION COMPLETE")
    print("="*60)
    print(f"IFC file: {Path(ifc_file_path).name}")
    print(f"Elements extracted: {len(check_results)}")
    print(f"Elements with fire rating: {sum(1 for e in check_results if e['actual_value'] != 'Not specified')}")
    print(f"Total database elements: {output_data['metadata']['total_elements']}")
    print(f"Output saved to: {output_file}")
    print("="*60 + "\n")


def get_fire_rating(element, building_use=None, element_type=None):
    """
    Reads the FireRating property from an IFC element.
    Returns an integer (minutes) or None if not found.
    e.g. 'R90' → 90
    
    If the element has no FireRating property and building_use is provided,
    falls back to the default rating for that building use and element type.
    
    Only accepts properties with fire-rating-related names to avoid false positives
    from generic numeric properties.
    
    Parameters:
        element (IfcElement) - IFC element to check
        building_use (str) - e.g. 'residential', 'commercial' (for fallback)
        element_type (str) - e.g. 'IfcWall', 'IfcColumn' (for type-specific defaults)
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
        default = get_default_fire_rating(building_use, element_type)
        if default is not None:
            logger.debug("No explicit FireRating for element %s; using default %s minutes for use '%s' type '%s'", 
                        getattr(element, 'GlobalId', '(no id)'), default, building_use, element_type or 'unknown')
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


# ─────────────────────────────────────────────
# IFCore-Compliant Check Function
# ─────────────────────────────────────────────

def check_fire_rating(model, building_use=None, evacuation_height_m=None, is_basement=False):
    """
    IFCore-compliant check function: Validates SI 6 fire resistance requirements.
    
    Signature complies with IFCore platform contracts:
    - First argument: model (ifcopenshell.file object, pre-loaded)
    - Returns: list[dict] matching element_results schema
    - Each dict represents one element checked
    
    Auto-detects building_use and evacuation_height_m from IFC if not provided.
    
    Parameters:
        model (ifcopenshell.file) - Pre-loaded IFC model object
        building_use (str, optional) - e.g. 'residential', 'commercial'. If None, auto-detected.
        evacuation_height_m (float, optional) - Building height in metres. If None, auto-detected.
        is_basement (bool) - True if checking basement floor
    
    Returns:
        list[dict] - Each dict has fields:
            element_id, element_type, element_name, element_name_long,
            check_status ("pass"|"fail"|"warning"|"blocked"|"log"),
            actual_value, required_value, comment, log
    """
    
    # Auto-detect if not provided
    if building_use is None:
        building_use = extract_building_use_from_ifc(model)
    if evacuation_height_m is None:
        evacuation_height_m = extract_evacuation_height_from_ifc(model)
    
    # Determine required R rating from SI 6 Table 3.1
    required_R = get_required_R(building_use, evacuation_height_m, is_basement)
    if required_R is None:
        logger.warning("Could not determine required_R for building_use=%s", building_use)
        required_R = 60  # Fallback default
    
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
    
    results = []
    
    # Loop through every structural element and check its fire rating
    for ifc_type in element_types:
        for element in model.by_type(ifc_type):
            element_id = element.GlobalId
            element_name = element.Name or f"{ifc_type}#{element.id()}"
            actual_R = get_fire_rating(element, building_use, element_type=ifc_type)
            
            # Determine check status
            if actual_R is None:
                check_status = "blocked"
                actual_value = None
                comment = "Fire rating property missing"
            elif actual_R >= required_R:
                check_status = "pass"
                actual_value = f"R{actual_R}"
                comment = None
            else:
                check_status = "fail"
                actual_value = f"R{actual_R}"
                deficit = required_R - actual_R
                comment = f"Deficit: {deficit} minutes (has R{actual_R}, needs R{required_R})"
            
            results.append({
                "element_id": element_id,
                "element_type": ifc_type,
                "element_name": element_name,
                "element_name_long": element_name,
                "check_status": check_status,
                "actual_value": actual_value,
                "required_value": f"R{required_R}",
                "comment": comment,
                "log": None,
            })
    
    return results


# ─────────────────────────────────────────────
# Legacy Functions (Backward Compatibility)
# ─────────────────────────────────────────────

def get_si6_compliance_details(ifc_path, building_use, evacuation_height_m, is_basement=False):
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
            building_use      — building use category
            evacuation_height — evacuation height in metres
            compliant         — list of passing elements
            non_compliant     — list of failing elements
            no_data           — list of elements with no FireRating found
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
            actual_R = get_fire_rating(element, building_use, element_type=ifc_type)

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

    return results


def is_si6_compliant(ifc_path, building_use, evacuation_height_m, is_basement=False):
    """
    Quick check: returns True if IFC is fully compliant with SI 6 requirements, False otherwise.
    
    An IFC is compliant only if:
      - Zero non-compliant elements (no failing fire rating), AND
      - Zero elements with missing fire rating data

    Parameters:
        ifc_path            (str)   : path to the .ifc file
        building_use        (str)   : e.g. 'residential', 'commercial'
        evacuation_height_m (float) : building evacuation height in metres
        is_basement         (bool)  : True if checking a basement floor

    Returns:
        bool: True if overall compliant, False otherwise
    """
    details = get_si6_compliance_details(ifc_path, building_use, evacuation_height_m, is_basement)
    
    # Handle error case
    if "error" in details:
        return False
    
    # Compliant only if zero failures AND zero missing data
    return (
        len(details["non_compliant"]) == 0 and
        len(details["no_data"]) == 0
    )




def export_check_results_to_json(results, output_path=None):
    """
    Exports SI 6 compliance check results to JSON file in standardized format.
    Stores ONLY failing elements (non-compliant).
    
    Parameters:
        results (dict) - Output from get_si6_compliance_details()
        output_path (str) - Path to save JSON file. Defaults to data_push/si6_compliance_check_result.json
    
    Returns:
        str - Path to the exported JSON file
    """
    if output_path is None:
        output_path = os.path.join(os.path.dirname(__file__), "..", "data_push", "si6_compliance_check_result.json")
    
    output_path = os.path.abspath(output_path)
    
    # Build check results array - ONLY failing elements
    check_results = []
    check_id = 1
    
    # Add non-compliant elements (status: fail)
    for el in results.get("non_compliant", []):
        check_results.append({
            "id": f"CHECK_{check_id:06d}",
            "check_result_id": el["id"],
            "element_id": el["id"],
            "element_type": el["type"],
            "element_name": el["name"],
            "element_name_long": el["name"],
            "check_status": "fail",
            "actual_value": f"R{el['actual_R']}" if el['actual_R'] is not None else None,
            "required_value": f"R{el['required_R']}",
            "comment": f"Deficit: {el.get('deficit', 'N/A')} minutes",
            "log": f"Element has R{el['actual_R']}, needs R{el['required_R']}"
        })
        check_id += 1
    
    # Build final output JSON
    output_json = {
        "metadata": {
            "description": "SI 6 Fire Resistance Compliance Check Results (Failing Elements Only)",
            "version": "1.0",
            "building_use": results.get("building_use"),
            "evacuation_height_m": results.get("evacuation_height"),
            "required_R": results.get("required_R"),
            "summary": {
                "total_elements_checked": len(results.get("compliant", [])) + len(results.get("non_compliant", [])) + len(results.get("no_data", [])),
                "compliant": len(results.get("compliant", [])),
                "non_compliant": len(results.get("non_compliant", [])),
                "missing_data": len(results.get("no_data", []))
            }
        },
        "check_results": check_results
    }
    
    # Write to file
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_json, f, indent=2, ensure_ascii=False)
        logger.info("Check results (failing elements only) exported to: %s", output_path)
        return output_path
    except Exception as e:
        logger.error("Failed to export check results: %s", e)
        return None


# ─────────────────────────────────────────────
# TESTING SPACE
# ─────────────────────────────────────────────

if __name__ == "__main__":

    # Ensure logging is configured when executed as a script
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    # --- Test 1: Auto-detect building use and height from IFC ---
    ifc_path = "00_data/ifc_models/Ifc4_Revit_ARC_FireRatingAdded.ifc"
    model = ifcopenshell.open(ifc_path)
    building_use = extract_building_use_from_ifc(model)
    evacuation_height_m = extract_evacuation_height_from_ifc(model)

    print("=" * 50)
    print(f"TEST 1 — {building_use.replace('_', ' ').title()}, {evacuation_height_m:.1f}m")
    print("=" * 50)
    
    # Get boolean compliance result
    is_compliant = is_si6_compliant(
        ifc_path            = ifc_path,
        building_use        = building_use,
        evacuation_height_m = evacuation_height_m
    )
    
    # Get detailed compliance report
    report = get_si6_compliance_details(
        ifc_path            = ifc_path,
        building_use        = building_use,
        evacuation_height_m = evacuation_height_m
    )

    # Overall Compliance Status
    print(f"\nOVERALL COMPLIANT: {is_compliant}\n")
    print("-" * 50)
    
    # Detailed Breakdown
    print(f"Required R rating  : R{report['required_R']}")
    print(f"Compliant elements : {len(report['compliant'])}")
    print(f"Non-compliant      : {len(report['non_compliant'])}")
    print(f"Missing data       : {len(report['no_data'])}")

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
    
    # Export results to JSON
    json_output_path = export_check_results_to_json(report)
    print(f"\n[OK] Results exported to: {json_output_path}")

    # --- Extraction Mode (optional): Pass IFC file path as argument ---
    # Uncomment or use command line: python SI_6_Fire_resistance_of_the_structure.py "path/to/file.ifc"
    if len(sys.argv) > 1:
        base_dir = Path(__file__).parent.parent
        output_file = base_dir / "data_push" / "structural_elements_data.json"
        ifc_file_path = sys.argv[1]
        
        # Support both absolute and relative paths
        if not Path(ifc_file_path).is_absolute():
            ifc_file_path = base_dir / ifc_file_path
        
        if Path(ifc_file_path).exists():
            print("\n" + "="*60)
            print("EXTRACTION MODE")
            print("="*60)
            extract_structural_elements_single_file(str(ifc_file_path), str(output_file))
        else:
            print(f"Error: IFC file not found: {ifc_file_path}")