"""
Pydantic Models for API Request/Response Validation

PURPOSE:
These models define the structure of data that comes into the API (requests)
and goes out of the API (responses). They provide automatic validation.

WHAT ARE PYDANTIC MODELS?
Think of them as "data templates" that ensure:
- The API receives the correct data format
- The API sends back the correct data format
- Automatic error messages if data is wrong

RESEARCH CONNECTION:
These models structure the input/output for fire compliance checks.
They ensure that building data (IFC files, heights, uses) is validated
before running compliance checks.
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


# ─────────────────────────────────────────────
# SI-6 STRUCTURAL RESISTANCE MODELS
# ─────────────────────────────────────────────

class SI6CheckRequest(BaseModel):
    """
    Request to check structural fire resistance (SI-6).
    
    Example:
        {
            "ifc_path": "/path/to/building.ifc",
            "building_use": "residential",
            "evacuation_height_m": 12.5,
            "is_basement": false
        }
    """
    ifc_path: str = Field(..., description="Absolute path to IFC file")
    building_use: str = Field(..., description="Building typology (residential, office, etc.)")
    evacuation_height_m: float = Field(..., description="Evacuation height in meters", ge=0)
    is_basement: bool = Field(default=False, description="Is this a basement check?")


class SI6CheckResponse(BaseModel):
    """
    Response from SI-6 compliance check.
    
    Contains:
    - Overall compliance status
    - List of elements checked
    - Non-compliant elements (if any)
    - Required vs actual fire resistance
    """
    status: str = Field(..., description="'compliant' or 'non_compliant'")
    building_use: str
    evacuation_height_m: float
    required_fire_resistance_minutes: int
    total_elements_checked: int
    compliant_elements: int
    non_compliant_elements: int
    non_compliant_details: List[Dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None


# ─────────────────────────────────────────────
# SI-1 INTERIOR PROPAGATION MODELS
# ─────────────────────────────────────────────

class SI1ScanRequest(BaseModel):
    """
    Request to scan IFC for interior propagation data (SI-1).
    
    Example:
        {
            "ifc_path": "/path/to/building.ifc",
            "preview_limit": 200
        }
    """
    ifc_path: str = Field(..., description="Absolute path to IFC file")
    preview_limit: int = Field(default=200, description="Max items to preview", ge=1)


class SI1ComplianceRequest(BaseModel):
    """
    Request to check sector size compliance (SI-1).
    
    Example:
        {
            "ifc_path": "/path/to/building.ifc",
            "building_use": "residential",
            "sprinklers": false
        }
    """
    ifc_path: str = Field(..., description="Absolute path to IFC file")
    building_use: str = Field(..., description="Building typology")
    sprinklers: bool = Field(default=False, description="Does building have sprinklers?")
    config_path: Optional[str] = Field(default=None, description="Path to rules JSON config")


class SI1ScanResponse(BaseModel):
    """
    Response from SI-1 scan.
    Contains spaces, doors, zones extracted from IFC.
    """
    ifc_path: str
    total_spaces: int
    total_doors: int
    spaces_preview: List[Dict[str, Any]]
    doors_preview: List[Dict[str, Any]]
    data_quality: Dict[str, Any]
    message: Optional[str] = None


class SI1ComplianceResponse(BaseModel):
    """
    Response from SI-1 compliance check.
    """
    status: str
    sectors: Dict[str, Any]
    compliance_summary: Dict[str, Any]
    non_compliant_sectors: List[Dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None


# ─────────────────────────────────────────────
# SI-3 EVACUATION MODELS
# ─────────────────────────────────────────────

class SI3CheckRequest(BaseModel):
    """
    Request to check evacuation compliance (SI-3).
    
    Example:
        {
            "ifc_path": "/path/to/building.ifc",
            "building_typology": "residential",
            "language": "auto"
        }
    """
    ifc_path: str = Field(..., description="Absolute path to IFC file")
    building_typology: Optional[str] = Field(default=None, description="Building typology override")
    language: str = Field(default="auto", description="Language for detection (es, en, fr, auto)")


class SI3CheckResponse(BaseModel):
    """
    Response from SI-3 evacuation check.
    """
    status: str
    ifc_path: str
    detected_typology: Optional[str]
    total_spaces: int
    total_occupants: int
    evacuation_summary: Dict[str, Any]
    exit_analysis: Dict[str, Any]
    compliance_issues: List[Dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None


# ─────────────────────────────────────────────
# SI-4 FIRE PROTECTION INSTALLATIONS MODELS
# ─────────────────────────────────────────────

class SI4CheckRequest(BaseModel):
    """
    Request to check fire protection installations (SI-4).
    
    Example:
        {
            "ifc_path": "/path/to/building.ifc",
            "config": {
                "building_use_target": "administrative",
                "rules": [...]
            }
        }
    """
    ifc_path: str = Field(..., description="Absolute path to IFC file")
    config: Dict[str, Any] = Field(..., description="Configuration dict with building_use and rules")


class SI4CheckResponse(BaseModel):
    """
    Response from SI-4 compliance check.
    """
    status: str = Field(..., description="Overall compliance status: PASS | FAIL | PASS_WITH_WARNINGS")
    file_name: str
    building_use: str
    complies: bool
    total_rules_checked: int
    passing_rules: int
    failing_rules: int
    manual_review_required: int
    non_compliant_details: List[Dict[str, Any]] = Field(default_factory=list)
    missing_data: List[str] = Field(default_factory=list)
    message: Optional[str] = None


# ─────────────────────────────────────────────
# GENERIC ERROR RESPONSE
# ─────────────────────────────────────────────

class ErrorResponse(BaseModel):
    """
    Standard error response for all endpoints.
    """
    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Additional error context")
