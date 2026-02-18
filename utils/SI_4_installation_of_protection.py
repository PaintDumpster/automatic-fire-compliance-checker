"""
SI-4 (Instalaciones de protección contra incendios) checker for IFC models.

Focused scope:
- Building use: Administrativo.
- Rules and thresholds loaded from `data_push/SI_4_table.json`.

Usage:
	1) Set `ifc_input_path` in `data_push/SI_4_table.json`.
	2) Run: `python utils/SI_4_installation_of_protection.py`
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import ifcopenshell


ADMIN_BUILDING_USE = "Administrativo"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "data_push" / "SI_4_table.json"
DEFAULT_IFC_INPUT_PATH = ""
DEFAULT_JSON_OUT_PATH: Optional[str] = None
PRINT_FULL_REPORT = False


def _to_float(value: Any) -> Optional[float]:
	"""Convert a value to float and return None when conversion fails."""
	try:
		if value is None:
			return None
		return float(value)
	except Exception:
		return None


def _norm(text: Any) -> str:
	"""Normalize values to lowercase stripped strings for robust text matching."""
	if text is None:
		return ""
	return str(text).strip().lower()


def _safe_get_psets(element: ifcopenshell.entity_instance) -> Dict[str, Dict[str, Any]]:
	"""Safely read IFC property sets from an element."""
	try:
		from ifcopenshell.util.element import get_psets  # type: ignore

		psets = get_psets(element) or {}
		if isinstance(psets, dict):
			return psets
	except Exception:
		pass
	return {}


def _entity_text_blob(element: ifcopenshell.entity_instance) -> str:
	"""Build a searchable text blob from IFC attributes and property set data."""

	parts = [
		_norm(getattr(element, "Name", None)),
		_norm(getattr(element, "Description", None)),
		_norm(getattr(element, "ObjectType", None)),
		_norm(getattr(element, "LongName", None)),
		_norm(getattr(element, "Tag", None)),
		_norm(getattr(element, "PredefinedType", None)),
	]

	psets = _safe_get_psets(element)
	for _, props in psets.items():
		if isinstance(props, dict):
			for prop_name, prop_value in props.items():
				parts.append(_norm(prop_name))
				parts.append(_norm(prop_value))

	return " | ".join([p for p in parts if p])


def _load_si4_table_config() -> Dict[str, Any]:
	"""Load SI-4 parameters from JSON and validate minimum required sections."""
	if not CONFIG_PATH.exists():
		raise FileNotFoundError(f"Missing configuration file: {CONFIG_PATH}")

	data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
	if not isinstance(data, dict):
		raise ValueError("SI-4 configuration must be a JSON object.")

	for key in ("scan_definitions", "rules", "actions_by_rule"):
		if key not in data:
			raise ValueError(f"Missing required key in SI-4 config: {key}")

	return data


def _get_element_quantities(element: ifcopenshell.entity_instance) -> Dict[str, float]:
	"""Extract numeric quantities from IfcElementQuantity relations."""
	quantities: Dict[str, float] = {}
	try:
		for rel in getattr(element, "IsDefinedBy", []) or []:
			if not rel.is_a("IfcRelDefinesByProperties"):
				continue
			prop_def = getattr(rel, "RelatingPropertyDefinition", None)
			if not prop_def or not prop_def.is_a("IfcElementQuantity"):
				continue

			for q in getattr(prop_def, "Quantities", []) or []:
				q_name = _norm(getattr(q, "Name", ""))
				if not q_name:
					continue

				if q.is_a("IfcQuantityArea"):
					v = _to_float(getattr(q, "AreaValue", None))
				elif q.is_a("IfcQuantityVolume"):
					v = _to_float(getattr(q, "VolumeValue", None))
				else:
					v = None

				if v is not None:
					quantities[q_name] = v
	except Exception:
		pass

	return quantities


def _get_space_area_m2(space: ifcopenshell.entity_instance, config: Dict[str, Any]) -> Optional[float]:
	"""Resolve space area using configured quantity keys and configured pset fallbacks."""
	q = _get_element_quantities(space)
	for key in config.get("space_area_quantity_keys", []):
		norm_key = _norm(key)
		if norm_key in q:
			return q[norm_key]

	psets = _safe_get_psets(space)
	pset_cfg = config.get("space_area_pset", {}) if isinstance(config.get("space_area_pset"), dict) else {}
	pset_name = pset_cfg.get("name", "Qto_SpaceBaseQuantities")
	base_qto = psets.get(pset_name, {})
	if isinstance(base_qto, dict):
		for k in pset_cfg.get("keys", ["NetFloorArea", "GrossFloorArea"]):
			v = _to_float(base_qto.get(k))
			if v is not None:
				return v

	return None


def _calc_total_constructed_area_m2(ifc_file: ifcopenshell.file, config: Dict[str, Any]) -> Optional[float]:
	"""Compute total built area as the sum of all valid `IfcSpace` areas."""
	spaces = ifc_file.by_type("IfcSpace") or []
	if not spaces:
		return None

	values: List[float] = []
	for space in spaces:
		a = _get_space_area_m2(space, config)
		if a is not None and a > 0:
			values.append(a)

	if not values:
		return None
	return round(sum(values), 2)


def _calc_evacuation_heights_m(ifc_file: ifcopenshell.file) -> Tuple[Optional[float], Optional[float], int]:
	"""Compute descending/ascending evacuation heights from storey elevations."""
	storeys = ifc_file.by_type("IfcBuildingStorey") or []
	if not storeys:
		return None, None, 0

	elevations: List[float] = []
	for storey in storeys:
		elev = _to_float(getattr(storey, "Elevation", None))
		if elev is not None:
			elevations.append(elev)

	if not elevations:
		return None, None, len(storeys)

	descending = max(e for e in elevations if e >= 0) if any(e >= 0 for e in elevations) else 0.0
	ascending = abs(min(e for e in elevations if e < 0)) if any(e < 0 for e in elevations) else 0.0
	return round(descending, 2), round(ascending, 2), len(storeys)


def _count_ifc_elements_by_keywords(
	ifc_file: ifcopenshell.file,
	entity_types: Sequence[str],
	keywords: Sequence[str],
) -> Tuple[int, List[str], List[Dict[str, Any]]]:
	"""Count IFC elements matching any keyword and collect evidence snippets/items."""
	compiled = [re.compile(re.escape(k), re.IGNORECASE) for k in keywords]
	count = 0
	examples: List[str] = []
	items: List[Dict[str, Any]] = []

	for entity_type in entity_types:
		try:
			elements = ifc_file.by_type(entity_type) or []
		except Exception:
			continue

		for element in elements:
			blob = _entity_text_blob(element)
			if not blob:
				continue

			if any(p.search(blob) for p in compiled):
				count += 1
				name = getattr(element, "Name", None) or "Unnamed"
				items.append(
					{
						"entity": element.is_a(),
						"id": element.id(),
						"guid": getattr(element, "GlobalId", None),
						"name": str(name),
					}
				)
				if len(examples) < 5:
					example_name = getattr(element, "Name", None) or getattr(element, "GlobalId", None) or "Unnamed"
					examples.append(str(example_name))

	return count, examples, items


def _scan_installations(ifc_file: ifcopenshell.file, config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
	"""Scan IFC elements using keyword/entity parameters defined in JSON config."""
	scans: Dict[str, Dict[str, Any]] = {}
	scan_definitions = config.get("scan_definitions", {})
	if not isinstance(scan_definitions, dict):
		return scans

	for check_key, definition in scan_definitions.items():
		if not isinstance(definition, dict):
			continue

		entity_types = definition.get("entity_types", [])
		keywords = definition.get("keywords", [])
		if not isinstance(entity_types, list) or not isinstance(keywords, list):
			continue

		count, examples, items = _count_ifc_elements_by_keywords(
			ifc_file,
			entity_types=[str(v) for v in entity_types],
			keywords=[str(v) for v in keywords],
		)
		scans[str(check_key)] = {
			"count": count,
			"examples": examples,
			"items": items,
		}

	return scans


def _rule_result(
	rule_id: str,
	text: str,
	check_key: str,
	applies: bool,
	required_count: Optional[int],
	found_count: int,
	evidence: List[str],
	note: str,
) -> Dict[str, Any]:
	"""Build a normalized rule result payload with PASS/FAIL/NA status."""
	if not applies:
		status = "NOT_APPLICABLE"
	elif required_count is None:
		status = "MANUAL_REVIEW"
	else:
		status = "PASS" if found_count >= required_count else "Fa"

	return {
		"id": rule_id,
		"check_key": check_key,
		"requirement": text,
		"applies": applies,
		"required_min_count": required_count,
		"found_count": found_count,
		"status": status,
		"evidence_examples": evidence,
		"note": note,
	}


def _resolve_rule_applicability(
	rule_cfg: Dict[str, Any],
	has_area: bool,
	area_m2: Optional[float],
	h_desc_m: Optional[float],
) -> bool:
	"""Evaluate whether a rule applies based on config-driven applicability conditions."""
	applies_cfg = rule_cfg.get("applies", {}) if isinstance(rule_cfg.get("applies"), dict) else {}
	applies_type = applies_cfg.get("type", "always")

	if applies_type == "always":
		return True

	if applies_type == "area_gt":
		value = _to_float(applies_cfg.get("value"))
		return bool(has_area and area_m2 is not None and value is not None and area_m2 > value)

	if applies_type == "area_gte":
		value = _to_float(applies_cfg.get("value"))
		return bool(has_area and area_m2 is not None and value is not None and area_m2 >= value)

	if applies_type == "area_gt_any":
		values = applies_cfg.get("values", []) if isinstance(applies_cfg.get("values"), list) else []
		thresholds = [_to_float(v) for v in values]
		return bool(has_area and area_m2 is not None and any(t is not None and area_m2 > t for t in thresholds))

	if applies_type == "desc_height_gt":
		value = _to_float(applies_cfg.get("value"))
		return bool(h_desc_m is not None and value is not None and h_desc_m > value)

	return False


def _resolve_required_count(rule_cfg: Dict[str, Any], applies: bool, area_m2: Optional[float]) -> Optional[int]:
	"""Resolve minimum required count from config using fixed or formula modes."""
	if not applies:
		return 0

	required_cfg = rule_cfg.get("required", {}) if isinstance(rule_cfg.get("required"), dict) else {}
	required_type = required_cfg.get("type", "fixed")

	if required_type == "fixed":
		value = _to_float(required_cfg.get("value"))
		return int(value) if value is not None else None

	if required_type == "hydrants_formula":
		if area_m2 is None or area_m2 < 5000:
			return 0
		if area_m2 <= 10000:
			return 1
		return 1 + math.ceil((area_m2 - 10000) / 10000)

	return None


def _detect_building_use(ifc_file: ifcopenshell.file, ifc_path: str) -> str:
	"""Infer building use text from all buildings, project metadata, and IFC filename."""
	parts: List[str] = []

	for b in ifc_file.by_type("IfcBuilding") or []:
		parts.extend(
			[
				_norm(getattr(b, "Name", None)),
				_norm(getattr(b, "Description", None)),
				_norm(getattr(b, "ObjectType", None)),
				_norm(getattr(b, "LongName", None)),
			]
		)
		psets = _safe_get_psets(b)
		for _, props in psets.items():
			if isinstance(props, dict):
				for key, value in props.items():
					if _norm(key) in {"occupancytype", "buildinguse", "function", "use", "classification"}:
						parts.append(_norm(value))

	for p in ifc_file.by_type("IfcProject") or []:
		parts.extend(
			[
				_norm(getattr(p, "Name", None)),
				_norm(getattr(p, "Description", None)),
				_norm(getattr(p, "LongName", None)),
			]
		)

	parts.append(_norm(Path(ifc_path).name))
	blob = " | ".join([p for p in parts if p])
	return blob or "unknown"


def _is_administrative_building(detected_use_text: str, config: Dict[str, Any]) -> bool:
	"""Check whether detected building use matches configured administrative keywords."""
	keywords = [_norm(v) for v in config.get("building_use_keywords", [ADMIN_BUILDING_USE])]
	target = _norm(config.get("building_use_target", ADMIN_BUILDING_USE))
	if target and target not in keywords:
		keywords.append(target)
	blob = _norm(detected_use_text)
	return any(keyword and keyword in blob for keyword in keywords)


def _get_building_marker(ifc_file: ifcopenshell.file) -> Dict[str, Any]:
	"""Return a fallback IFC marker when no specific failing items are found."""
	buildings = ifc_file.by_type("IfcBuilding") or []
	if buildings:
		b = buildings[0]
		return {
			"entity": b.is_a(),
			"id": b.id(),
			"guid": getattr(b, "GlobalId", None),
			"name": getattr(b, "Name", None) or "Building",
		}
	return {
		"entity": "IfcProject",
		"id": None,
		"guid": None,
		"name": "Building",
	}


def _build_non_compliance_highlight(
	rules: List[Dict[str, Any]],
	scans: Dict[str, Dict[str, Any]],
	ifc_file: ifcopenshell.file,
) -> Dict[str, Any]:
	"""Create red highlight payload for non-compliant IFC elements."""
	red_rgb = [255, 0, 0]
	items: List[Dict[str, Any]] = []
	seen: set = set()

	for rule in rules:
		if rule.get("status") != "FAIL":
			continue
		check_key = rule.get("check_key")
		candidates = scans.get(check_key, {}).get("items", []) if check_key else []

		if not candidates:
			candidates = [_get_building_marker(ifc_file)]

		for it in candidates:
			identity = (it.get("entity"), it.get("id"), rule.get("id"))
			if identity in seen:
				continue
			seen.add(identity)
			items.append(
				{
					"rule_id": rule.get("id"),
					"entity": it.get("entity"),
					"id": it.get("id"),
					"guid": it.get("guid"),
					"name": it.get("name"),
					"rgb": red_rgb,
				}
			)

	return {
		"rgb_non_compliant": red_rgb,
		"items": items,
	}


def _build_non_compliance_reasons(
	rules: List[Dict[str, Any]],
	missing_data: List[str],
	actions_by_rule: Dict[str, str],
) -> List[Dict[str, Any]]:
	"""Build textual reasons and corrective actions for failed rules and data-quality warnings."""
	reasons: List[Dict[str, Any]] = []

	for rule in rules:
		if rule.get("status") != "FAIL":
			continue

		required_min = rule.get("required_min_count")
		found = rule.get("found_count")
		reason_text = (
			f"Incumple {rule.get('id')}: {rule.get('requirement')}. "
			f"Requerido mínimo: {required_min}; encontrado: {found}."
		)

		reasons.append(
			{
				"rule_id": rule.get("id"),
				"requirement": rule.get("requirement"),
				"reason": reason_text,
				"what_to_do": actions_by_rule.get(
					rule.get("id", ""),
					"Provide the missing installation(s), update IFC data, and verify thresholds against SI-4 Table 1.1.",
				),
				"detail": rule.get("note"),
			}
		)

	for warning in missing_data:
		reasons.append(
			{
				"rule_id": "DATA-QUALITY",
				"requirement": "Datos IFC necesarios para verificación completa",
				"reason": warning,
				"what_to_do": (
					"Populate IFC with `IfcSpace` areas and `IfcBuildingStorey.Elevation`, then re-run the check to evaluate all SI-4 thresholds correctly."
				),
				"detail": "Este aviso puede limitar parte del alcance automático de la comprobación.",
			}
		)

	return reasons


def check_si4_administrativo(ifc_path: str, config: Dict[str, Any]) -> Dict[str, Any]:
	"""Run SI-4 verification for administrative buildings using JSON-driven parameters."""
	file_name = Path(ifc_path).name
	building_use_target = str(config.get("building_use_target", ADMIN_BUILDING_USE))
	try:
		ifc_file = ifcopenshell.open(ifc_path)
	except Exception as exc:
		return {
			"file_name": file_name,
			"building_use": building_use_target,
			"error": f"Failed to open IFC file: {exc}",
		}

	detected_building_use = _detect_building_use(ifc_file, ifc_path)
	if not _is_administrative_building(detected_building_use, config):
		return {
			"file_name": file_name,
			"building_use": detected_building_use,
			"target_building_use": building_use_target,
			"complies": False,
			"highlight": {"rgb_non_compliant": [255, 0, 0], "items": []},
			"non_compliance_reasons": [
				{
					"rule_id": "BUILDING-USE",
					"requirement": f"Building use must be {building_use_target}",
					"reason": "El edificio no se ha identificado como Administrativo. El chequeo SI-4 no se ejecuta.",
					"what_to_do": "Revise la clasificación de uso del edificio en el IFC o ejecute el checker correspondiente al uso real.",
					"detail": f"Uso detectado: {detected_building_use}",
				}
			],
			"source_regulation": "utils/regulation.txt (SI 4, Tabla 1.1, fila Administrativo + reglas generales)",
			"metrics": {
				"total_constructed_area_m2": None,
				"evacuation_height_descending_m": None,
				"evacuation_height_ascending_m": None,
				"storey_count": 0,
			},
			"detected_installations": {},
			"requirements": [],
			"summary": {
				"overall_status": "NOT_EXECUTED",
				"complies": False,
				"pass": 0,
				"fail": 0,
				"not_applicable": 0,
				"manual_review": 0,
				"missing_data_warnings": ["Chequeo no ejecutado por uso distinto de Administrativo."],
			},
		}

	area_m2 = _calc_total_constructed_area_m2(ifc_file, config)
	h_desc_m, h_asc_m, storey_count = _calc_evacuation_heights_m(ifc_file)
	scans = _scan_installations(ifc_file, config)

	has_area = area_m2 is not None

	rules: List[Dict[str, Any]] = []
	for rule_cfg in config.get("rules", []):
		if not isinstance(rule_cfg, dict):
			continue

		rule_id = str(rule_cfg.get("id", "UNKNOWN"))
		check_key = str(rule_cfg.get("check_key", ""))
		requirement = str(rule_cfg.get("requirement", ""))
		note = str(rule_cfg.get("note", ""))

		applies = _resolve_rule_applicability(rule_cfg, has_area, area_m2, h_desc_m)
		required_count = _resolve_required_count(rule_cfg, applies, area_m2)

		scan_entry = scans.get(check_key, {})
		found_count = int(scan_entry.get("count", 0))
		evidence = scan_entry.get("examples", []) if isinstance(scan_entry.get("examples", []), list) else []

		rules.append(
			_rule_result(
				rule_id,
				requirement,
				check_key,
				applies,
				required_count,
				found_count,
				[str(v) for v in evidence],
				note,
			)
		)

	missing_data: List[str] = []
	if not has_area:
		missing_data.append("No se ha podido calcular la superficie construida a partir de IfcSpace.")
	if h_desc_m is None or h_asc_m is None:
		missing_data.append("No se ha podido calcular la altura de evacuación desde IfcBuildingStorey.Elevation.")

	fail_count = sum(1 for r in rules if r["status"] == "FAIL")
	manual_count = sum(1 for r in rules if r["status"] == "MANUAL_REVIEW")
	pass_count = sum(1 for r in rules if r["status"] == "PASS")
	na_count = sum(1 for r in rules if r["status"] == "NOT_APPLICABLE")

	overall_status = "PASS" if fail_count == 0 else "FAIL"
	if fail_count == 0 and missing_data:
		overall_status = "PASS_WITH_WARNINGS"

	complies = fail_count == 0
	highlight = _build_non_compliance_highlight(rules, scans, ifc_file)
	actions_by_rule = config.get("actions_by_rule", {}) if isinstance(config.get("actions_by_rule"), dict) else {}
	non_compliance_reasons = _build_non_compliance_reasons(rules, missing_data, actions_by_rule)

	return {
		"file_name": file_name,
		"building_use": building_use_target,
		"detected_building_use": detected_building_use,
		"complies": complies,
		"highlight": highlight,
		"non_compliance_reasons": non_compliance_reasons,
		"source_regulation": "utils/regulation.txt (SI 4, Tabla 1.1, fila Administrativo + reglas generales)",
		"metrics": {
			"total_constructed_area_m2": area_m2,
			"evacuation_height_descending_m": h_desc_m,
			"evacuation_height_ascending_m": h_asc_m,
			"storey_count": storey_count,
		},
		"detected_installations": scans,
		"requirements": rules,
		"summary": {
			"overall_status": overall_status,
			"complies": complies,
			"pass": pass_count,
			"fail": fail_count,
			"not_applicable": na_count,
			"manual_review": manual_count,
			"missing_data_warnings": missing_data,
		},
	}


def _resolve_ifc_input(config: Dict[str, Any]) -> str:
	"""Resolve IFC input path from JSON config first, then from script constant fallback."""
	ifc_in_json = str(config.get("ifc_input_path", "")).strip()
	if ifc_in_json:
		return ifc_in_json

	if DEFAULT_IFC_INPUT_PATH.strip():
		return DEFAULT_IFC_INPUT_PATH.strip()

	raise ValueError(
		"IFC path is required. Set `ifc_input_path` in data_push/SI_4_table.json or set DEFAULT_IFC_INPUT_PATH in this script."
	)


def main() -> int:
	"""Entrypoint: load config, execute SI-4 check, print result, and optionally persist report."""
	try:
		config = _load_si4_table_config()
		ifc_path = _resolve_ifc_input(config)
	except Exception as exc:
		print(json.dumps({"overall_status": False, "reason": str(exc)}, ensure_ascii=False))
		return 2

	report = check_si4_administrativo(ifc_path, config)
	summary_status = report.get("summary", {}).get("overall_status")
	overall_status = summary_status if isinstance(summary_status, bool) else bool(report.get("complies", False))

	minimal_output: Dict[str, Any] = {"overall_status": overall_status}
	if not overall_status:
		reasons_raw = report.get("non_compliance_reasons", [])
		reasons: List[str] = []
		if isinstance(reasons_raw, list):
			for item in reasons_raw:
				if isinstance(item, dict):
					reason_text = item.get("reason")
					if reason_text:
						reasons.append(str(reason_text))
				else:
					reasons.append(str(item))
		minimal_output["why_not_compliant"] = reasons

	if PRINT_FULL_REPORT:
		print(json.dumps(report, indent=2, ensure_ascii=False))
	else:
		print(json.dumps(minimal_output, indent=2, ensure_ascii=False))

	json_out_path = config.get("json_out_path") or DEFAULT_JSON_OUT_PATH
	if json_out_path:
		out_path = Path(str(json_out_path))
		out_path.parent.mkdir(parents=True, exist_ok=True)
		out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

	if "error" in report:
		return 2
	if overall_status is False:
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())

"""
SI-4 (Instalaciones de protección contra incendios) checker for IFC models.

Focused scope:
- Building use: Administrativo.
- Rules and thresholds loaded from `data_push/SI_4_table.json`.

Usage:
	1) Set `ifc_input_path` in `data_push/SI_4_table.json`.
	2) Run: `python utils/SI_4_installation_of_protection.py`
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import ifcopenshell


ADMIN_BUILDING_USE = "Administrativo"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "data_push" / "SI_4_table.json"
DEFAULT_IFC_INPUT_PATH = ""
DEFAULT_JSON_OUT_PATH: Optional[str] = None
PRINT_FULL_REPORT = False


def _to_float(value: Any) -> Optional[float]:
	"""Convert a value to float and return None when conversion fails."""
	try:
		if value is None:
			return None
		return float(value)
	except Exception:
		return None


def _norm(text: Any) -> str:
	"""Normalize values to lowercase stripped strings for robust text matching."""
	if text is None:
		return ""
	return str(text).strip().lower()


def _safe_get_psets(element: ifcopenshell.entity_instance) -> Dict[str, Dict[str, Any]]:
	"""Safely read IFC property sets from an element."""
	try:
		from ifcopenshell.util.element import get_psets  # type: ignore

		psets = get_psets(element) or {}
		if isinstance(psets, dict):
			return psets
	except Exception:
		pass
	return {}


def _entity_text_blob(element: ifcopenshell.entity_instance) -> str:
	"""Build a searchable text blob from IFC attributes and property set data."""

	parts = [
		_norm(getattr(element, "Name", None)),
		_norm(getattr(element, "Description", None)),
		_norm(getattr(element, "ObjectType", None)),
		_norm(getattr(element, "LongName", None)),
		_norm(getattr(element, "Tag", None)),
		_norm(getattr(element, "PredefinedType", None)),
	]

	psets = _safe_get_psets(element)
	for _, props in psets.items():
		if isinstance(props, dict):
			for prop_name, prop_value in props.items():
				parts.append(_norm(prop_name))
				parts.append(_norm(prop_value))

	return " | ".join([p for p in parts if p])


def _load_si4_table_config() -> Dict[str, Any]:
	"""Load SI-4 parameters from JSON and validate minimum required sections."""
	if not CONFIG_PATH.exists():
		raise FileNotFoundError(f"Missing configuration file: {CONFIG_PATH}")

	data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
	if not isinstance(data, dict):
		raise ValueError("SI-4 configuration must be a JSON object.")

	for key in ("scan_definitions", "rules", "actions_by_rule"):
		if key not in data:
			raise ValueError(f"Missing required key in SI-4 config: {key}")

	return data


def _get_element_quantities(element: ifcopenshell.entity_instance) -> Dict[str, float]:
	"""Extract numeric quantities from IfcElementQuantity relations."""
	quantities: Dict[str, float] = {}
	try:
		for rel in getattr(element, "IsDefinedBy", []) or []:
			if not rel.is_a("IfcRelDefinesByProperties"):
				continue
			prop_def = getattr(rel, "RelatingPropertyDefinition", None)
			if not prop_def or not prop_def.is_a("IfcElementQuantity"):
				continue

			for q in getattr(prop_def, "Quantities", []) or []:
				q_name = _norm(getattr(q, "Name", ""))
				if not q_name:
					continue

				if q.is_a("IfcQuantityArea"):
					v = _to_float(getattr(q, "AreaValue", None))
				elif q.is_a("IfcQuantityVolume"):
					v = _to_float(getattr(q, "VolumeValue", None))
				else:
					v = None

				if v is not None:
					quantities[q_name] = v
	except Exception:
		pass

	return quantities


def _get_space_area_m2(space: ifcopenshell.entity_instance, config: Dict[str, Any]) -> Optional[float]:
	"""Resolve space area using configured quantity keys and configured pset fallbacks."""
	q = _get_element_quantities(space)
	for key in config.get("space_area_quantity_keys", []):
		norm_key = _norm(key)
		if norm_key in q:
			return q[norm_key]

	psets = _safe_get_psets(space)
	pset_cfg = config.get("space_area_pset", {}) if isinstance(config.get("space_area_pset"), dict) else {}
	pset_name = pset_cfg.get("name", "Qto_SpaceBaseQuantities")
	base_qto = psets.get(pset_name, {})
	if isinstance(base_qto, dict):
		for k in pset_cfg.get("keys", ["NetFloorArea", "GrossFloorArea"]):
			v = _to_float(base_qto.get(k))
			if v is not None:
				return v

	return None


def _calc_total_constructed_area_m2(ifc_file: ifcopenshell.file, config: Dict[str, Any]) -> Optional[float]:
	"""Compute total built area as the sum of all valid `IfcSpace` areas."""
	spaces = ifc_file.by_type("IfcSpace") or []
	if not spaces:
		return None

	values: List[float] = []
	for space in spaces:
		a = _get_space_area_m2(space, config)
		if a is not None and a > 0:
			values.append(a)

	if not values:
		return None
	return round(sum(values), 2)


def _calc_evacuation_heights_m(ifc_file: ifcopenshell.file) -> Tuple[Optional[float], Optional[float], int]:
	"""Compute descending/ascending evacuation heights from storey elevations."""
	storeys = ifc_file.by_type("IfcBuildingStorey") or []
	if not storeys:
		return None, None, 0

	elevations: List[float] = []
	for storey in storeys:
		elev = _to_float(getattr(storey, "Elevation", None))
		if elev is not None:
			elevations.append(elev)

	if not elevations:
		return None, None, len(storeys)

	descending = max(e for e in elevations if e >= 0) if any(e >= 0 for e in elevations) else 0.0
	ascending = abs(min(e for e in elevations if e < 0)) if any(e < 0 for e in elevations) else 0.0
	return round(descending, 2), round(ascending, 2), len(storeys)


def _count_ifc_elements_by_keywords(
	ifc_file: ifcopenshell.file,
	entity_types: Sequence[str],
	keywords: Sequence[str],
) -> Tuple[int, List[str], List[Dict[str, Any]]]:
	"""Count IFC elements matching any keyword and collect evidence snippets/items."""
	compiled = [re.compile(re.escape(k), re.IGNORECASE) for k in keywords]
	count = 0
	examples: List[str] = []
	items: List[Dict[str, Any]] = []

	for entity_type in entity_types:
		try:
			elements = ifc_file.by_type(entity_type) or []
		except Exception:
			continue

		for element in elements:
			blob = _entity_text_blob(element)
			if not blob:
				continue

			if any(p.search(blob) for p in compiled):
				count += 1
				name = getattr(element, "Name", None) or "Unnamed"
				items.append(
					{
						"entity": element.is_a(),
						"id": element.id(),
						"guid": getattr(element, "GlobalId", None),
						"name": str(name),
					}
				)
				if len(examples) < 5:
					example_name = getattr(element, "Name", None) or getattr(element, "GlobalId", None) or "Unnamed"
					examples.append(str(example_name))

	return count, examples, items


def _scan_installations(ifc_file: ifcopenshell.file, config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
	"""Scan IFC elements using keyword/entity parameters defined in JSON config."""
	scans: Dict[str, Dict[str, Any]] = {}
	scan_definitions = config.get("scan_definitions", {})
	if not isinstance(scan_definitions, dict):
		return scans

	for check_key, definition in scan_definitions.items():
		if not isinstance(definition, dict):
			continue

		entity_types = definition.get("entity_types", [])
		keywords = definition.get("keywords", [])
		if not isinstance(entity_types, list) or not isinstance(keywords, list):
			continue

		count, examples, items = _count_ifc_elements_by_keywords(
			ifc_file,
			entity_types=[str(v) for v in entity_types],
			keywords=[str(v) for v in keywords],
		)
		scans[str(check_key)] = {
			"count": count,
			"examples": examples,
			"items": items,
		}

	return scans


def _rule_result(
	rule_id: str,
	text: str,
	check_key: str,
	applies: bool,
	required_count: Optional[int],
	found_count: int,
	evidence: List[str],
	note: str,
) -> Dict[str, Any]:
	"""Build a normalized rule result payload with PASS/FAIL/NA status."""
	if not applies:
		status = "NOT_APPLICABLE"
	elif required_count is None:
		status = "MANUAL_REVIEW"
	else:
		status = "PASS" if found_count >= required_count else "Fa"

	return {
		"id": rule_id,
		"check_key": check_key,
		"requirement": text,
		"applies": applies,
		"required_min_count": required_count,
		"found_count": found_count,
		"status": status,
		"evidence_examples": evidence,
		"note": note,
	}


def _resolve_rule_applicability(
	rule_cfg: Dict[str, Any],
	has_area: bool,
	area_m2: Optional[float],
	h_desc_m: Optional[float],
) -> bool:
	"""Evaluate whether a rule applies based on config-driven applicability conditions."""
	applies_cfg = rule_cfg.get("applies", {}) if isinstance(rule_cfg.get("applies"), dict) else {}
	applies_type = applies_cfg.get("type", "always")

	if applies_type == "always":
		return True

	if applies_type == "area_gt":
		value = _to_float(applies_cfg.get("value"))
		return bool(has_area and area_m2 is not None and value is not None and area_m2 > value)

	if applies_type == "area_gte":
		value = _to_float(applies_cfg.get("value"))
		return bool(has_area and area_m2 is not None and value is not None and area_m2 >= value)

	if applies_type == "area_gt_any":
		values = applies_cfg.get("values", []) if isinstance(applies_cfg.get("values"), list) else []
		thresholds = [_to_float(v) for v in values]
		return bool(has_area and area_m2 is not None and any(t is not None and area_m2 > t for t in thresholds))

	if applies_type == "desc_height_gt":
		value = _to_float(applies_cfg.get("value"))
		return bool(h_desc_m is not None and value is not None and h_desc_m > value)

	return False


def _resolve_required_count(rule_cfg: Dict[str, Any], applies: bool, area_m2: Optional[float]) -> Optional[int]:
	"""Resolve minimum required count from config using fixed or formula modes."""
	if not applies:
		return 0

	required_cfg = rule_cfg.get("required", {}) if isinstance(rule_cfg.get("required"), dict) else {}
	required_type = required_cfg.get("type", "fixed")

	if required_type == "fixed":
		value = _to_float(required_cfg.get("value"))
		return int(value) if value is not None else None

	if required_type == "hydrants_formula":
		if area_m2 is None or area_m2 < 5000:
			return 0
		if area_m2 <= 10000:
			return 1
		return 1 + math.ceil((area_m2 - 10000) / 10000)

	return None


def _detect_building_use(ifc_file: ifcopenshell.file, ifc_path: str) -> str:
	"""Infer building use text from all buildings, project metadata, and IFC filename."""
	parts: List[str] = []

	for b in ifc_file.by_type("IfcBuilding") or []:
		parts.extend(
			[
				_norm(getattr(b, "Name", None)),
				_norm(getattr(b, "Description", None)),
				_norm(getattr(b, "ObjectType", None)),
				_norm(getattr(b, "LongName", None)),
			]
		)
		psets = _safe_get_psets(b)
		for _, props in psets.items():
			if isinstance(props, dict):
				for key, value in props.items():
					if _norm(key) in {"occupancytype", "buildinguse", "function", "use", "classification"}:
						parts.append(_norm(value))

	for p in ifc_file.by_type("IfcProject") or []:
		parts.extend(
			[
				_norm(getattr(p, "Name", None)),
				_norm(getattr(p, "Description", None)),
				_norm(getattr(p, "LongName", None)),
			]
		)

	parts.append(_norm(Path(ifc_path).name))
	blob = " | ".join([p for p in parts if p])
	return blob or "unknown"


def _is_administrative_building(detected_use_text: str, config: Dict[str, Any]) -> bool:
	"""Check whether detected building use matches configured administrative keywords."""
	keywords = [_norm(v) for v in config.get("building_use_keywords", [ADMIN_BUILDING_USE])]
	target = _norm(config.get("building_use_target", ADMIN_BUILDING_USE))
	if target and target not in keywords:
		keywords.append(target)
	blob = _norm(detected_use_text)
	return any(keyword and keyword in blob for keyword in keywords)


def _get_building_marker(ifc_file: ifcopenshell.file) -> Dict[str, Any]:
	"""Return a fallback IFC marker when no specific failing items are found."""
	buildings = ifc_file.by_type("IfcBuilding") or []
	if buildings:
		b = buildings[0]
		return {
			"entity": b.is_a(),
			"id": b.id(),
			"guid": getattr(b, "GlobalId", None),
			"name": getattr(b, "Name", None) or "Building",
		}
	return {
		"entity": "IfcProject",
		"id": None,
		"guid": None,
		"name": "Building",
	}


def _build_non_compliance_highlight(
	rules: List[Dict[str, Any]],
	scans: Dict[str, Dict[str, Any]],
	ifc_file: ifcopenshell.file,
) -> Dict[str, Any]:
	"""Create red highlight payload for non-compliant IFC elements."""
	red_rgb = [255, 0, 0]
	items: List[Dict[str, Any]] = []
	seen: set = set()

	for rule in rules:
		if rule.get("status") != "FAIL":
			continue
		check_key = rule.get("check_key")
		candidates = scans.get(check_key, {}).get("items", []) if check_key else []

		if not candidates:
			candidates = [_get_building_marker(ifc_file)]

		for it in candidates:
			identity = (it.get("entity"), it.get("id"), rule.get("id"))
			if identity in seen:
				continue
			seen.add(identity)
			items.append(
				{
					"rule_id": rule.get("id"),
					"entity": it.get("entity"),
					"id": it.get("id"),
					"guid": it.get("guid"),
					"name": it.get("name"),
					"rgb": red_rgb,
				}
			)

	return {
		"rgb_non_compliant": red_rgb,
		"items": items,
	}


def _build_non_compliance_reasons(
	rules: List[Dict[str, Any]],
	missing_data: List[str],
	actions_by_rule: Dict[str, str],
) -> List[Dict[str, Any]]:
	"""Build textual reasons and corrective actions for failed rules and data-quality warnings."""
	reasons: List[Dict[str, Any]] = []

	for rule in rules:
		if rule.get("status") != "FAIL":
			continue

		required_min = rule.get("required_min_count")
		found = rule.get("found_count")
		reason_text = (
			f"Incumple {rule.get('id')}: {rule.get('requirement')}. "
			f"Requerido mínimo: {required_min}; encontrado: {found}."
		)

		reasons.append(
			{
				"rule_id": rule.get("id"),
				"requirement": rule.get("requirement"),
				"reason": reason_text,
				"what_to_do": actions_by_rule.get(
					rule.get("id", ""),
					"Provide the missing installation(s), update IFC data, and verify thresholds against SI-4 Table 1.1.",
				),
				"detail": rule.get("note"),
			}
		)

	for warning in missing_data:
		reasons.append(
			{
				"rule_id": "DATA-QUALITY",
				"requirement": "Datos IFC necesarios para verificación completa",
				"reason": warning,
				"what_to_do": (
					"Populate IFC with `IfcSpace` areas and `IfcBuildingStorey.Elevation`, then re-run the check to evaluate all SI-4 thresholds correctly."
				),
				"detail": "Este aviso puede limitar parte del alcance automático de la comprobación.",
			}
		)

	return reasons


def check_si4_administrativo(ifc_path: str, config: Dict[str, Any]) -> Dict[str, Any]:
	"""Run SI-4 verification for administrative buildings using JSON-driven parameters."""
	file_name = Path(ifc_path).name
	building_use_target = str(config.get("building_use_target", ADMIN_BUILDING_USE))
	try:
		ifc_file = ifcopenshell.open(ifc_path)
	except Exception as exc:
		return {
			"file_name": file_name,
			"building_use": building_use_target,
			"error": f"Failed to open IFC file: {exc}",
		}

	detected_building_use = _detect_building_use(ifc_file, ifc_path)
	if not _is_administrative_building(detected_building_use, config):
		return {
			"file_name": file_name,
			"building_use": detected_building_use,
			"target_building_use": building_use_target,
			"complies": False,
			"highlight": {"rgb_non_compliant": [255, 0, 0], "items": []},
			"non_compliance_reasons": [
				{
					"rule_id": "BUILDING-USE",
					"requirement": f"Building use must be {building_use_target}",
					"reason": "El edificio no se ha identificado como Administrativo. El chequeo SI-4 no se ejecuta.",
					"what_to_do": "Revise la clasificación de uso del edificio en el IFC o ejecute el checker correspondiente al uso real.",
					"detail": f"Uso detectado: {detected_building_use}",
				}
			],
			"source_regulation": "utils/regulation.txt (SI 4, Tabla 1.1, fila Administrativo + reglas generales)",
			"metrics": {
				"total_constructed_area_m2": None,
				"evacuation_height_descending_m": None,
				"evacuation_height_ascending_m": None,
				"storey_count": 0,
			},
			"detected_installations": {},
			"requirements": [],
			"summary": {
				"overall_status": "NOT_EXECUTED",
				"complies": False,
				"pass": 0,
				"fail": 0,
				"not_applicable": 0,
				"manual_review": 0,
				"missing_data_warnings": ["Chequeo no ejecutado por uso distinto de Administrativo."],
			},
		}

	area_m2 = _calc_total_constructed_area_m2(ifc_file, config)
	h_desc_m, h_asc_m, storey_count = _calc_evacuation_heights_m(ifc_file)
	scans = _scan_installations(ifc_file, config)

	has_area = area_m2 is not None

	rules: List[Dict[str, Any]] = []
	for rule_cfg in config.get("rules", []):
		if not isinstance(rule_cfg, dict):
			continue

		rule_id = str(rule_cfg.get("id", "UNKNOWN"))
		check_key = str(rule_cfg.get("check_key", ""))
		requirement = str(rule_cfg.get("requirement", ""))
		note = str(rule_cfg.get("note", ""))

		applies = _resolve_rule_applicability(rule_cfg, has_area, area_m2, h_desc_m)
		required_count = _resolve_required_count(rule_cfg, applies, area_m2)

		scan_entry = scans.get(check_key, {})
		found_count = int(scan_entry.get("count", 0))
		evidence = scan_entry.get("examples", []) if isinstance(scan_entry.get("examples", []), list) else []

		rules.append(
			_rule_result(
				rule_id,
				requirement,
				check_key,
				applies,
				required_count,
				found_count,
				[str(v) for v in evidence],
				note,
			)
		)

	missing_data: List[str] = []
	if not has_area:
		missing_data.append("No se ha podido calcular la superficie construida a partir de IfcSpace.")
	if h_desc_m is None or h_asc_m is None:
		missing_data.append("No se ha podido calcular la altura de evacuación desde IfcBuildingStorey.Elevation.")

	fail_count = sum(1 for r in rules if r["status"] == "FAIL")
	manual_count = sum(1 for r in rules if r["status"] == "MANUAL_REVIEW")
	pass_count = sum(1 for r in rules if r["status"] == "PASS")
	na_count = sum(1 for r in rules if r["status"] == "NOT_APPLICABLE")

	overall_status = "PASS" if fail_count == 0 else "FAIL"
	if fail_count == 0 and missing_data:
		overall_status = "PASS_WITH_WARNINGS"

	complies = fail_count == 0
	highlight = _build_non_compliance_highlight(rules, scans, ifc_file)
	actions_by_rule = config.get("actions_by_rule", {}) if isinstance(config.get("actions_by_rule"), dict) else {}
	non_compliance_reasons = _build_non_compliance_reasons(rules, missing_data, actions_by_rule)

	return {
		"file_name": file_name,
		"building_use": building_use_target,
		"detected_building_use": detected_building_use,
		"complies": complies,
		"highlight": highlight,
		"non_compliance_reasons": non_compliance_reasons,
		"source_regulation": "utils/regulation.txt (SI 4, Tabla 1.1, fila Administrativo + reglas generales)",
		"metrics": {
			"total_constructed_area_m2": area_m2,
			"evacuation_height_descending_m": h_desc_m,
			"evacuation_height_ascending_m": h_asc_m,
			"storey_count": storey_count,
		},
		"detected_installations": scans,
		"requirements": rules,
		"summary": {
			"overall_status": overall_status,
			"complies": complies,
			"pass": pass_count,
			"fail": fail_count,
			"not_applicable": na_count,
			"manual_review": manual_count,
			"missing_data_warnings": missing_data,
		},
	}


def _resolve_ifc_input(config: Dict[str, Any]) -> str:
	"""Resolve IFC input path from JSON config first, then from script constant fallback."""
	ifc_in_json = str(config.get("ifc_input_path", "")).strip()
	if ifc_in_json:
		return ifc_in_json

	if DEFAULT_IFC_INPUT_PATH.strip():
		return DEFAULT_IFC_INPUT_PATH.strip()

	raise ValueError(
		"IFC path is required. Set `ifc_input_path` in data_push/SI_4_table.json or set DEFAULT_IFC_INPUT_PATH in this script."
	)


def main() -> int:
	"""Entrypoint: load config, execute SI-4 check, print result, and optionally persist report."""
	try:
		config = _load_si4_table_config()
		ifc_path = _resolve_ifc_input(config)
	except Exception as exc:
		print(json.dumps({"overall_status": False, "reason": str(exc)}, ensure_ascii=False))
		return 2

	report = check_si4_administrativo(ifc_path, config)
	summary_status = report.get("summary", {}).get("overall_status")
	overall_status = summary_status if isinstance(summary_status, bool) else bool(report.get("complies", False))

	minimal_output: Dict[str, Any] = {"overall_status": overall_status}
	if not overall_status:
		reasons_raw = report.get("non_compliance_reasons", [])
		reasons: List[str] = []
		if isinstance(reasons_raw, list):
			for item in reasons_raw:
				if isinstance(item, dict):
					reason_text = item.get("reason")
					if reason_text:
						reasons.append(str(reason_text))
				else:
					reasons.append(str(item))
		minimal_output["why_not_compliant"] = reasons

	if PRINT_FULL_REPORT:
		print(json.dumps(report, indent=2, ensure_ascii=False))
	else:
		print(json.dumps(minimal_output, indent=2, ensure_ascii=False))

	json_out_path = config.get("json_out_path") or DEFAULT_JSON_OUT_PATH
	if json_out_path:
		out_path = Path(str(json_out_path))
		out_path.parent.mkdir(parents=True, exist_ok=True)
		out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

	if "error" in report:
		return 2
	if overall_status is False:
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
