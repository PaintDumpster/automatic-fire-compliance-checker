# `utils/` — Folder Structure

```
utils/
├── SI_1_interior_propagation.py
├── si_2_exterior_propagation.py
├── Structure.py
├── evacuation.py
├── intervention.py
└── utils_sample.py
```

---

## File Descriptions

### `SI_1_interior_propagation.py`
IFC Fire Safety Scanner for **SI 1 — Interior Propagation**. Extracts fire-safety data from IFC models.

| Section | Functions | Description |
|---------|-----------|-------------|
| **A — Safe Helpers** | `_safe_get_attribute(element, attr_name)` | Safely retrieve an attribute from an IFC element |
| | `get_pset_value(element, pset_name, prop_name)` | Retrieve a property value from a property set |
| | `_to_float(x)` | Best-effort float conversion |
| **B — Storey Lookup** | `build_element_storey_map(ifc_file)` | Build a fast `{element_id: storey_name}` map |
| | `get_storey_name(element, storey_map)` | Get storey name from cached map |
| **C — Quantities** | `get_element_quantities(element)` | Extract area/volume from `IfcElementQuantity` |
| | `get_space_area_m2(space)` | Return net/gross floor area of a space |
| | `get_space_volume_m3(space)` | Return net/gross volume of a space |
| **D — Zones** | `get_space_zones(space)` | Get `IfcZone` membership names for a space |
| **E — Door Fire Rating** | `_extract_fire_rating_from_pset(element)` | Extract `FireRating` from `Pset_DoorCommon` |
| | `get_door_fire_rating(door)` | Retrieve fire rating from instance or type pset |
| **F — Main Scan API** | `scan_ifc_basic(ifc_path, preview_limit)` | Scan one IFC file → spaces, doors, counts, data quality |
| **G — Batch Scan** | `scan_ifc_folder(folder_path, recursive, preview_limit)` | Scan all IFC files in a folder |

**Dependencies:** `ifcopenshell`

---

### `si_2_exterior_propagation.py`
Placeholder for **SI 2 — Exterior Propagation** checks. Currently empty.

---

### `Structure.py`
Checks structural fire resistance compliance against **SI 6 — Table 3.1**.

| Function | Description |
|----------|-------------|
| `get_height_band(evacuation_height_m, is_basement)` | Converts evacuation height to a table lookup key (`h_le_15`, `h_le_28`, `h_gt_28`, `basement`) |
| `get_required_R(building_use, evacuation_height_m, is_basement)` | Returns required fire resistance (minutes) for a given use and height |
| `get_fire_rating(element)` | Reads `FireRating` property from an IFC element → integer (minutes) |
| `check_si6_compliance(ifc_path, building_use, evacuation_height_m, is_basement)` | Full compliance check of structural elements (`IfcBeam`, `IfcColumn`, `IfcSlab`, `IfcMember`) against SI 6 |

**Dependencies:** `ifcopenshell`, `json`, `os`
**Data:** Loads `data_push/si6_table_3_1.json` at import time.

---

### `evacuation.py`
Placeholder for **SI 3 — Evacuation** checks. Currently contains only stub content.

---

### `intervention.py`
Placeholder for **SI 5 — Intervention** checks. Currently contains only a comment header.

---

### `utils_sample.py`
Template/sample utility file with section placeholders for imports, functions, and testing.
