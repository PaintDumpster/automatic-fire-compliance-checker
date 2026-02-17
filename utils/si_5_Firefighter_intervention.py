#import libraries

import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.util.placement
import numpy as np
import sys

#Function

def validate_firefighter_access(ifc_window, floor_elevation, floor_evacuation_height):
    """
    Analyzes an IfcWindow for compliance with DB SI 5 Section 2 (Firefighter Access).
    
    Parameters:
    - ifc_window: The IfcWindow entity.
    - floor_elevation: Absolute Z of the finished floor.
    - floor_evacuation_height: Height of the floor relative to ground (for the 9m rule).
    """
    try:
        # 1. Dimensions Check (DB SI 5-2.1.b)
        # Required: Clear width >= 0.80m, Clear height >= 1.20m
        w = getattr(ifc_window, "OverallWidth", 0)
        h = getattr(ifc_window, "OverallHeight", 0)

        if w < 0.80 or h < 1.20:
            return False, f"Dimensions fail: {w}m x {h}m"

        # 2. Sill Height Check (DB SI 5-2.1.a)
        # Required: Sill <= 1.20m from floor level
        # We use ifcopenshell's placement utility to get the global matrix
        matrix = ifcopenshell.util.placement.get_any_displacement(ifc_window)
        z_insertion = matrix[2, 3] 
        
        # Calculate Sill Z (Assuming insertion point is at window center)
        sill_z = z_insertion - (h / 2)
        relative_sill_height = sill_z - floor_elevation

        if relative_sill_height > 1.20:
            return False, f"Sill too high: {relative_sill_height:.2f}m"
        
        if relative_sill_height < -0.05:
            return False, f"Sill below floor: {relative_sill_height:.2f}m"

        # 3. Security Obstruction Check (DB SI 5-2.1.c)
        # Required: No grilles/bars if evacuation height > 9m
        psets = ifcopenshell.util.element.get_psets(ifc_window)
        has_bars = psets.get("Pset_WindowCommon", {}).get("SecurityBars", False)

        if has_bars and floor_evacuation_height > 9.0:
            return False, f"Bars prohibited at evac height {floor_evacuation_height}m"

        return True, "Compliant"

    except Exception as e:
        return False, f"Logic Error: {str(e)}"
    
# testing space

# testing space

# testing space

if __name__ == "__main__":
    # --- Environment Check ---
    print(f"--- Environment Check ---")
    print(f"Python Version: {sys.version.split()[0]}")
    print(f"IfcOpenShell Version: {ifcopenshell.version}")
    print(f"Numpy Version: {np.__version__}\n")

    # --- Mocking IFC Object for Logic Validation ---
    class MockWindow:
        def __init__(self, w, h, z_center):
            self.OverallWidth = w
            self.OverallHeight = h
            self.GlobalId = "Mock_ID_123"
            # 1. Provide a dummy file
            self.file = ifcopenshell.file() 
            # 2. Provide the matrix for placement util
            self.matrix = np.eye(4)
            self.matrix[2, 3] = z_center

        # 3. Provide the .is_a() method so get_psets knows it's an IfcWindow
        def is_a(self, class_name=None):
            if class_name is None:
                return "IfcWindow"
            return class_name == "IfcWindow"

    # Redirecting the placement util to our mock matrix
    ifcopenshell.util.placement.get_any_displacement = lambda x: x.matrix

    # --- Test Scenario ---
    # Floor at 12.0m. Evacuation height 12.0m.
    # Window center at 13.0m, height 1.4m -> Sill at 12.3m. 
    # Result: Sill height = 0.3m (Compliant).
    test_window = MockWindow(w=0.9, h=1.4, z_center=13.0)
    
    is_ok, msg = validate_firefighter_access(
        ifc_window=test_window, 
        floor_elevation=12.0, 
        floor_evacuation_height=12.0
    )

    print(f"--- Test Results ---")
    print(f"ID: WIN-TEST-01 | Compliance: {is_ok} | Message: {msg}")