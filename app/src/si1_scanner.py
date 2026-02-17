"""
IFC Fire Safety Scanner Module (SI 1)

Scans IFC files for fire safety relevant information including:
- Spatial elements (spaces, doors, walls, storeys)
- Fire rating information from property sets
- Data quality metrics
"""

from typing import Dict, List, Any, Optional
import ifcopenshell
from pathlib import Path


def get_pset_value(
    element: ifcopenshell.entity_instance, 
    pset_name: str, 
    prop_name: str
) -> Optional[Any]:
    """
    Safely retrieve a property value from an element's property set.
    
    Args:
        element: ifcopenshell element instance
        pset_name: Name of the property set (e.g., 'Pset_DoorCommon')
        prop_name: Name of the property within the set
        
    Returns:
        Property value if found, None otherwise
    """
    try:
        # Use ifcopenshell utility to get all property sets
        from ifcopenshell.util.element import get_psets
        
        psets = get_psets(element)
        if pset_name in psets:
            pset_properties = psets[pset_name]
            if prop_name in pset_properties:
                return pset_properties[prop_name]
    except Exception:
        # Gracefully handle any errors accessing property sets
        pass
    
    return None


def get_storey_of_element(element: ifcopenshell.entity_instance) -> Optional[str]:
    """
    Get the storey name that spatially contains this element.
    
    Uses IfcRelContainedInSpatialStructure to find the parent storey.
    
    Args:
        element: ifcopenshell element instance
        
    Returns:
        Storey name if found, None otherwise
    """
    try:
        # Query for spatial containment relationships
        ifc_file = element.file
        
        for rel in ifc_file.by_type('IfcRelContainedInSpatialStructure'):
            if element in rel.RelatedElements:
                container = rel.RelatingStructure
                
                # Check if container is a storey
                if container.is_a() == 'IfcBuildingStorey':
                    storey_name = container.Name
                    return storey_name if storey_name else 'Unknown'
    except Exception:
        # Gracefully handle any errors
        pass
    
    return None


def _safe_get_attribute(element: ifcopenshell.entity_instance, attr_name: str) -> Optional[Any]:
    """
    Safely retrieve an attribute from an element.
    
    Args:
        element: ifcopenshell element instance
        attr_name: Name of the attribute
        
    Returns:
        Attribute value if exists, None otherwise
    """
    try:
        if hasattr(element, attr_name):
            return getattr(element, attr_name)
    except Exception:
        pass
    
    return None


def _get_quantity_area(element: ifcopenshell.entity_instance) -> Optional[float]:
    """
    Extract area quantity from an element's quantities.
    
    Args:
        element: ifcopenshell element instance
        
    Returns:
        Area value if found, None otherwise
    """
    try:
        if not hasattr(element, 'Quantities') or element.Quantities is None:
            return None
        
        for qty in element.Quantities:
            qty_name = _safe_get_attribute(qty, 'Name')
            if qty_name and qty_name.lower() in ['grossfloorarea', 'area', 'netfloorarea']:
                area_value = _safe_get_attribute(qty, 'AreaValue')
                if area_value is not None:
                    return float(area_value)
    except Exception:
        pass
    
    return None


def scan_ifc_basic(ifc_path: str) -> Dict[str, Any]:
    """
    Scan an IFC file and extract basic fire safety relevant information.
    
    Queries for spatial elements and their properties, including:
    - Spaces (IfcSpace)
    - Doors (IfcDoor) with fire rating info
    - Walls (IfcWall)
    - Building storeys (IfcBuildingStorey)
    
    Args:
        ifc_path: Path to the IFC file
        
    Returns:
        Dictionary with:
        - file_name: the input file path
        - counts: dict with element type counts
        - spaces: list of first 20 spaces with properties
        - doors: list of first 20 doors with fire rating info
        - data_quality: flags for has_spaces, has_storeys, has_fire_ratings_doors
        - error: (optional) error message if file failed to open
    """
    file_name = Path(ifc_path).name if ifc_path else "unknown"
    
    # Try to open the IFC file
    try:
        ifc_file = ifcopenshell.open(ifc_path)
    except Exception as e:
        return {
            'file_name': file_name,
            'error': f"Failed to open IFC file: {str(e)}",
            'spaces': [],
            'doors': [],
            'counts': {
                'IfcSpace': 0,
                'IfcDoor': 0,
                'IfcWall': 0,
                'IfcBuildingStorey': 0
            },
            'data_quality': {
                'has_spaces': False,
                'has_storeys': False,
                'has_fire_ratings_doors': False
            }
        }
    
    try:
        # Get all elements of interest
        spaces = ifc_file.by_type('IfcSpace')
        doors = ifc_file.by_type('IfcDoor')
        walls = ifc_file.by_type('IfcWall')
        storeys = ifc_file.by_type('IfcBuildingStorey')
        
        # Extract space details (first 20)
        space_list: List[Dict[str, Any]] = []
        for space in spaces[:20]:
            storey_name = get_storey_of_element(space)
            area = _get_quantity_area(space)
            
            space_entry = {
                'guid': _safe_get_attribute(space, 'GlobalId'),
                'name': _safe_get_attribute(space, 'Name') or 'Unnamed',
                'long_name': _safe_get_attribute(space, 'LongName'),
                'object_type': _safe_get_attribute(space, 'ObjectType'),
                'storey_name': storey_name,
                'area': area
            }
            space_list.append(space_entry)
        
        # Extract door details (first 20)
        door_list: List[Dict[str, Any]] = []
        fire_rating_count = 0
        
        for door in doors[:20]:
            # Try to get fire rating from the door element's property sets first
            fire_rating = get_pset_value(door, 'Pset_DoorCommon', 'FireRating')
            
            # If not found, try the related door type's property sets / attributes
            if fire_rating is None:
                try:
                    is_defined_by = getattr(door, 'IsDefinedBy', None) or []
                    for rel in is_defined_by:
                        door_type_obj = getattr(rel, 'RelatingType', None)
                        if door_type_obj is None:
                            continue
                        
                        # Pset on the type object
                        fire_rating = get_pset_value(door_type_obj, 'Pset_DoorCommon', 'FireRating')
                        
                        # Direct attribute on the type as a last resort
                        if fire_rating is None:
                            fire_rating = _safe_get_attribute(door_type_obj, 'FireRating')
                        
                        if fire_rating is not None:
                            break
                except Exception:
                    fire_rating = None
            
            # Fall back to door's own FireRating attribute if present
            if fire_rating is None:
                fire_rating = _safe_get_attribute(door, 'FireRating')
            
            if fire_rating is not None:
                # Ensure JSON-serializable value
                if not isinstance(fire_rating, (str, int, float, bool)):
                    fire_rating = str(fire_rating)
                fire_rating_count += 1
            
            door_entry = {
                'guid': _safe_get_attribute(door, 'GlobalId'),
                'name': _safe_get_attribute(door, 'Name') or 'Unnamed',
                'door_type': _safe_get_attribute(door, 'PredefinedType') or 'Unknown',
                'fire_rating': fire_rating
            }
            door_list.append(door_entry)
        
        # Compile results
        return {
            'file_name': file_name,
            'spaces': space_list,
            'doors': door_list,
            'counts': {
                'IfcSpace': len(spaces),
                'IfcDoor': len(doors),
                'IfcWall': len(walls),
                'IfcBuildingStorey': len(storeys)
            },
            'data_quality': {
                'has_spaces': len(spaces) > 0,
                'has_storeys': len(storeys) > 0,
                'has_fire_ratings_doors': fire_rating_count > 0
            }
        }
    
    except Exception as e:
        # If scanning fails, return error but keep file open result
        return {
            'file_name': file_name,
            'error': f"Error scanning IFC file: {str(e)}",
            'spaces': [],
            'doors': [],
            'counts': {
                'IfcSpace': 0,
                'IfcDoor': 0,
                'IfcWall': 0,
                'IfcBuildingStorey': 0
            },
            'data_quality': {
                'has_spaces': False,
                'has_storeys': False,
                'has_fire_ratings_doors': False
            }
        }
