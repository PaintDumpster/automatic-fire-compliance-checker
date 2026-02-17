"""
IFC File Checker module

Provides high-level API for checking IFC fire compliance.
"""

from typing import Dict, Any, Tuple
from .si1_scanner import scan_ifc_basic


def check_ifc_file(ifc_path: str) -> Dict[str, Any]:
    """
    Check an IFC file for fire safety compliance.
    
    Scans the IFC file and determines compliance based on:
    - File can be opened successfully
    - Contains at least 1 space
    
    Args:
        ifc_path: Path to the IFC file to check
        
    Returns:
        Dictionary with:
        - compliant: bool (True if file opens and has >=1 space)
        - color: str ("green" if compliant, "red" otherwise)
        - details: dict (the complete scan output from scan_ifc_basic)
    """
    # Perform basic scan
    scan_results = scan_ifc_basic(ifc_path)
    
    # Determine compliance
    has_error = 'error' in scan_results
    has_spaces = scan_results.get('counts', {}).get('IfcSpace', 0) >= 1
    
    compliant = not has_error and has_spaces
    color = "green" if compliant else "red"
    
    return {
        'compliant': compliant,
        'color': color,
        'details': scan_results
    }
