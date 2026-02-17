# ─────────────────────────────────────────────
# IMPORTED LIBRARIES
# ─────────────────────────────────────────────
import json
import os
import ifcopenshell


# ─────────────────────────────────────────────
# FUNCTION
# ─────────────────────────────────────────────

# Load SI 6 Table 3.1 from external JSON file
# The JSON file should be in the same directory as this script
_JSON_PATH = "data_push/si6_table_3_1.json"

with open(_JSON_PATH, "r") as f:
    _SI6_DATA = json.load(f)

SI6_TABLE_3_1 = {
    key: value
    for key, value in _SI6_DATA.items()
    if not key.startswith("_")        # skip metadata keys
}


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


def get_required_R(building_use, evacuation_height_m, is_basement=False):
    """Returns the required fire resistance in minutes for a given use and height."""
    use = SI6_TABLE_3_1.get(building_use.lower())
    if not use:
        return None
    if "all" in use:
        return use["all"]
    band = get_height_band(evacuation_height_m, is_basement)
    return use.get(band)


def get_fire_rating(element):
    """
    Reads the FireRating property from an IFC element.
    Returns an integer (minutes) or None if not found.
    e.g. 'R90' → 90
    """
    for rel in element.IsDefinedBy:
        if rel.is_a("IfcRelDefinesByProperties"):
            pset = rel.RelatingPropertyDefinition
            if hasattr(pset, "HasProperties"):
                for prop in pset.HasProperties:
                    if prop.Name == "FireRating":
                        val = prop.NominalValue
                        if val:
                            raw = str(val.wrappedValue)
                            numeric = ''.join(filter(str.isdigit, raw))
                            return int(numeric) if numeric else None
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
        # Check for usage description in Description or other properties
        if hasattr(building, "Description") and building.Description:
            desc = str(building.Description).lower()
            if "residential" in desc:
                return "residential"
            elif "commercial" in desc:
                return "commercial"
            elif "office" in desc:
                return "office"
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
        "IfcMember"
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
            actual_R = get_fire_rating(element)

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

    # --- Test 1: Residential building, 20m evacuation height ---
    report = check_si6_compliance(
        ifc_path            = "00_data/ifc_models/01_Duplex_Apartment.ifc",
        building_use        = "residential",
        evacuation_height_m = 20
    )

    print("=" * 50)
    print("TEST 1 — Residential, 20m")
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

    # --- Test 2: Auto-extract IFC file parameters and check structural fire compliance ---
    print("\n" + "=" * 70)
    print("TEST 2 — Automatic IFC Analysis & Structural Fire Compliance Check")
    print("=" * 70)
    
    ifc_file_path = "00_data/ifc_models/Ifc4_Revit_ARC_FireRatingAdded.ifc"
    print(f"\nIFC File: {ifc_file_path}")
    
    # Automatically extract building parameters
    model = ifcopenshell.open(ifc_file_path)
    auto_building_use = extract_building_use_from_ifc(model)
    auto_evacuation_height = extract_evacuation_height_from_ifc(model)
    
    print("\n--- Automatically Extracted Parameters ---")
    print(f"Building Use         : {auto_building_use.upper()}")
    print(f"Evacuation Height    : {auto_evacuation_height}m")
    
    # Get structural element information
    all_beams = model.by_type("IfcBeam")
    all_columns = model.by_type("IfcColumn")
    all_slabs = model.by_type("IfcSlab")
    all_members = model.by_type("IfcMember")
    total_structural = len(all_beams) + len(all_columns) + len(all_slabs) + len(all_members)
    
    print(f"\n--- Structural Elements Found ---")
    print(f"Beams                : {len(all_beams)}")
    print(f"Columns              : {len(all_columns)}")
    print(f"Slabs                : {len(all_slabs)}")
    print(f"Members              : {len(all_members)}")
    print(f"Total Elements       : {total_structural}")
    
    # Run structural fire compliance check
    print(f"\n--- Running Structural Fire Compliance Check ---")
    report2 = check_si6_compliance(
        ifc_path            = ifc_file_path,
        building_use        = auto_building_use,
        evacuation_height_m = auto_evacuation_height
    )
    
    print(f"\nRequired Fire Resistance (SI 6 Table 3.1) : R{report2['required_R']} minutes")
    print(f"Compliant Elements   : {len(report2['compliant'])}")
    print(f"Non-Compliant        : {len(report2['non_compliant'])}")
    print(f"Missing Fire Rating  : {len(report2['no_data'])}")
    print(f"\n✓ OVERALL STRUCTURAL COMPLIANCE: {('PASS ✓' if report2['overall_compliant'] else 'FAIL ✗')}")
    
    if report2["non_compliant"]:
        print(f"\n--- Non-Compliant Structural Elements ({len(report2['non_compliant'])}) ---")
        for i, el in enumerate(report2["non_compliant"][:5], 1):
            deficit = el.get('deficit', el['required_R'] - (el['actual_R'] or 0))
            print(f"{i}. [{el['type']}] {el['name']}")
            print(f"   Required: R{el['required_R']} | Actual: R{el['actual_R'] or 'N/A'} | Deficit: {deficit} min")
        if len(report2["non_compliant"]) > 5:
            print(f"   ... and {len(report2['non_compliant']) - 5} more non-compliant elements")
    
    if report2["no_data"]:
        print(f"\n--- Structural Elements With Missing Fire Rating Data ({len(report2['no_data'])}) ---")
        material_missing = {}
        for el in report2["no_data"]:
            material_missing[el['type']] = material_missing.get(el['type'], 0) + 1
        
        for elem_type, count in material_missing.items():
            print(f"  {elem_type}: {count} elements without FireRating property")
    
    print("\n" + "=" * 70)