"""
IFC Fire Safety Compliance Checker Package
"""

from .ifc_checker import check_ifc_file
from .si1_scanner import scan_ifc_basic

__all__ = ['check_ifc_file', 'scan_ifc_basic']
