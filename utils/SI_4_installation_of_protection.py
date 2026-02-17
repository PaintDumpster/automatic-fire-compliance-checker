"""
SI-4 (Instalaciones de protección contra incendios) checker for IFC models.

Focused scope:
- Building use: Administrativo.
- Rules extracted from `utils/regulation.txt` (Tabla 1.1, SI 4).

Usage:
	python utils/SI_4_installation_of_protection.py --ifc <path_to_file.ifc>
	python utils/SI_4_installation_of_protection.py --ifc <path_to_file.ifc> --json-out report.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import ifcopenshell


ADMIN_BUILDING_USE = "Administrativo"


def _to_float(value: Any) -> Optional[float]:
	try:
		if value is None:
			return None
		return float(value)
	except Exception:
		return None


def _norm(text: Any) -> str:
	if text is None:
		return ""
	return str(text).strip().lower()


def _safe_get_psets(element: ifcopenshell.entity_instance) -> Dict[str, Dict[str, Any]]:
	try:
		from ifcopenshell.util.element import get_psets  # type: ignore

		psets = get_psets(element) or {}
		if isinstance(psets, dict):
			return psets
	except Exception:
		pass
	return {}


def _entity_text_blob(element: ifcopenshell.entity_instance) -> str:
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


def _get_element_quantities(element: ifcopenshell.entity_instance) -> Dict[str, float]:
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


def _get_space_area_m2(space: ifcopenshell.entity_instance) -> Optional[float]:
	q = _get_element_quantities(space)
	for key in (
		"netfloorarea",
		"grossfloorarea",
		"area",
		"net area",
		"gross area",
		"floorarea",
		"floor area",
	):
		if key in q:
			return q[key]

	psets = _safe_get_psets(space)
	base_qto = psets.get("Qto_SpaceBaseQuantities", {})
	if isinstance(base_qto, dict):
		for k in ("NetFloorArea", "GrossFloorArea"):
			v = _to_float(base_qto.get(k))
			if v is not None:
				return v

	return None


def _calc_total_constructed_area_m2(ifc_file: ifcopenshell.file) -> Optional[float]:
	spaces = ifc_file.by_type("IfcSpace") or []
	if not spaces:
		return None

	values: List[float] = []
	for space in spaces:
		a = _get_space_area_m2(space)
		if a is not None and a > 0:
			values.append(a)

	if not values:
		return None
	return round(sum(values), 2)


def _calc_evacuation_heights_m(ifc_file: ifcopenshell.file) -> Tuple[Optional[float], Optional[float], int]:
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
	compiled = [re.compile(re.escape(k), re.IGNORECASE) for k in keywords]
	count = 0
	examples: List[str] = []
	items: List[Dict[str, Any]] = []

	for entity_type in entity_types:
		for element in ifc_file.by_type(entity_type) or []:
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


def _scan_installations(ifc_file: ifcopenshell.file) -> Dict[str, Dict[str, Any]]:
	scans: Dict[str, Dict[str, Any]] = {}

	extinguisher_count, extinguisher_examples, extinguisher_items = _count_ifc_elements_by_keywords(
		ifc_file,
		entity_types=["IfcFireSuppressionTerminal", "IfcFlowTerminal", "IfcProxy"],
		keywords=["extintor", "extinguisher", "21a", "113b"],
	)
	scans["portable_extinguishers"] = {
		"count": extinguisher_count,
		"examples": extinguisher_examples,
		"items": extinguisher_items,
	}

	bie_count, bie_examples, bie_items = _count_ifc_elements_by_keywords(
		ifc_file,
		entity_types=["IfcFireSuppressionTerminal", "IfcFlowTerminal", "IfcProxy"],
		keywords=["bie", "boca de incendio", "hose reel", "fire hose cabinet"],
	)
	scans["fire_hose_cabinets_bie"] = {
		"count": bie_count,
		"examples": bie_examples,
		"items": bie_items,
	}

	dry_riser_count, dry_riser_examples, dry_riser_items = _count_ifc_elements_by_keywords(
		ifc_file,
		entity_types=["IfcDistributionSystem", "IfcPipeSegment", "IfcPipeFitting", "IfcProxy"],
		keywords=["columna seca", "dry riser"],
	)
	scans["dry_riser"] = {
		"count": dry_riser_count,
		"examples": dry_riser_examples,
		"items": dry_riser_items,
	}

	alarm_count_1, alarm_examples_1, alarm_items = _count_ifc_elements_by_keywords(
		ifc_file,
		entity_types=["IfcAlarm", "IfcSensor", "IfcAlarmType", "IfcProxy"],
		keywords=["alarm", "alarma", "manual call point", "pulsador"],
	)
	scans["alarm_system"] = {
		"count": alarm_count_1,
		"examples": alarm_examples_1,
		"items": alarm_items,
	}

	detector_count, detector_examples, detector_items = _count_ifc_elements_by_keywords(
		ifc_file,
		entity_types=["IfcSensor", "IfcAlarm", "IfcProxy"],
		keywords=["detector", "smoke detector", "heat detector", "detector de incend"],
	)
	scans["fire_detection"] = {
		"count": detector_count,
		"examples": detector_examples,
		"items": detector_items,
	}

	hydrant_count, hydrant_examples, hydrant_items = _count_ifc_elements_by_keywords(
		ifc_file,
		entity_types=["IfcFireSuppressionTerminal", "IfcFlowTerminal", "IfcProxy"],
		keywords=["hidrante", "hydrant"],
	)
	scans["external_hydrants"] = {
		"count": hydrant_count,
		"examples": hydrant_examples,
		"items": hydrant_items,
	}

	sprinkler_count, sprinkler_examples, sprinkler_items = _count_ifc_elements_by_keywords(
		ifc_file,
		entity_types=["IfcSprinkler", "IfcFireSuppressionTerminal", "IfcProxy"],
		keywords=["sprinkler", "rociador", "extincion automatica", "extinción automática"],
	)
	scans["automatic_extinguishing"] = {
		"count": sprinkler_count,
		"examples": sprinkler_examples,
		"items": sprinkler_items,
	}

	emergency_lift_count, emergency_lift_examples, emergency_lift_items = _count_ifc_elements_by_keywords(
		ifc_file,
		entity_types=["IfcTransportElement", "IfcElevator", "IfcProxy"],
		keywords=["emergency", "emergencia", "bomberos", "firefighter"],
	)
	scans["emergency_elevator"] = {
		"count": emergency_lift_count,
		"examples": emergency_lift_examples,
		"items": emergency_lift_items,
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
	if not applies:
		status = "NOT_APPLICABLE"
	elif required_count is None:
		status = "MANUAL_REVIEW"
	else:
		status = "PASS" if found_count >= required_count else "FAIL"

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


def _get_building_marker(ifc_file: ifcopenshell.file) -> Dict[str, Any]:
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


def _build_non_compliance_reasons(rules: List[Dict[str, Any]], missing_data: List[str]) -> List[Dict[str, Any]]:
	reasons: List[Dict[str, Any]] = []
	actions_by_rule: Dict[str, str] = {
		"SI4-GEN-EXT": (
			"Install compliant portable extinguishers (minimum efficacy 21A-113B) and place them so travel distance to an extinguisher "
			"is at most 15 m on each floor, including special-risk rooms."
		),
		"SI4-ADM-BIE": (
			"Provide at least one BIE (fire hose cabinet) because built area exceeds 2,000 m², and complete hydraulic design/commissioning "
			"according to RIPCI."
		),
		"SI4-ADM-COL-SEC": (
			"Provide a dry riser because evacuation height exceeds 24 m, unless the municipality allows the alternative system permitted by note (5)."
		),
		"SI4-ADM-ALARM": (
			"Install a fire alarm system because built area exceeds 1,000 m²; ensure it provides both acoustic and visual alarm signals."
		),
		"SI4-ADM-DET": (
			"Install automatic detection: at least in high-risk rooms when area exceeds 2,000 m², and throughout the building when area exceeds 5,000 m²."
		),
		"SI4-ADM-HYD": (
			"Provide the required number of external hydrants (1 between 5,000–10,000 m², plus 1 per additional 10,000 m² or fraction), "
			"or document qualifying public hydrants within 100 m."
		),
		"SI4-GEN-LIFT": (
			"Provide at least one emergency/firefighter elevator because evacuation height exceeds 28 m."
		),
		"SI4-GEN-AUTO-EXT": (
			"Provide an automatic extinguishing system because evacuation height exceeds 80 m."
		),
	}

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


def check_si4_administrativo(ifc_path: str) -> Dict[str, Any]:
	file_name = Path(ifc_path).name
	try:
		ifc_file = ifcopenshell.open(ifc_path)
	except Exception as exc:
		return {
			"file_name": file_name,
			"building_use": ADMIN_BUILDING_USE,
			"error": f"Failed to open IFC file: {exc}",
		}

	area_m2 = _calc_total_constructed_area_m2(ifc_file)
	h_desc_m, h_asc_m, storey_count = _calc_evacuation_heights_m(ifc_file)
	scans = _scan_installations(ifc_file)

	has_area = area_m2 is not None
	has_heights = h_desc_m is not None and h_asc_m is not None

	rules: List[Dict[str, Any]] = []

	rules.append(
		_rule_result(
			"SI4-GEN-EXT",
			"Extintores portátiles (en general)",
			"portable_extinguishers",
			applies=True,
			required_count=1,
			found_count=scans["portable_extinguishers"]["count"],
			evidence=scans["portable_extinguishers"]["examples"],
			note="La separación máxima de 15 m no puede verificarse automáticamente sin geometría de recorridos.",
		)
	)

	applies_bie_admin = bool(has_area and area_m2 > 2000)
	rules.append(
		_rule_result(
			"SI4-ADM-BIE",
			"Bocas de incendio equipadas si superficie construida > 2.000 m²",
			"fire_hose_cabinets_bie",
			applies=applies_bie_admin,
			required_count=1 if applies_bie_admin else 0,
			found_count=scans["fire_hose_cabinets_bie"]["count"],
			evidence=scans["fire_hose_cabinets_bie"]["examples"],
			note="Comprobación mínima por existencia de equipos BIE.",
		)
	)

	applies_dry_riser = bool(has_heights and h_desc_m > 24)
	rules.append(
		_rule_result(
			"SI4-ADM-COL-SEC",
			"Columna seca si altura de evacuación descendente > 24 m",
			"dry_riser",
			applies=applies_dry_riser,
			required_count=1 if applies_dry_riser else 0,
			found_count=scans["dry_riser"]["count"],
			evidence=scans["dry_riser"]["examples"],
			note="En algunos municipios puede sustituirse por BIE (nota 5 de tabla).",
		)
	)

	applies_alarm = bool(has_area and area_m2 > 1000)
	rules.append(
		_rule_result(
			"SI4-ADM-ALARM",
			"Sistema de alarma si superficie construida > 1.000 m²",
			"alarm_system",
			applies=applies_alarm,
			required_count=1 if applies_alarm else 0,
			found_count=scans["alarm_system"]["count"],
			evidence=scans["alarm_system"]["examples"],
			note="Debe transmitir señal visual y acústica; esa parte requiere revisión manual.",
		)
	)

	applies_detection_zone = bool(has_area and area_m2 > 2000)
	applies_detection_full = bool(has_area and area_m2 > 5000)
	det_note = (
		"Si >2.000 m² se exigen detectores en zonas de riesgo alto; "
		"si >5.000 m² en todo el edificio. Cobertura exacta requiere validación espacial/manual."
	)
	rules.append(
		_rule_result(
			"SI4-ADM-DET",
			"Sistema de detección de incendio según umbral de superficie",
			"fire_detection",
			applies=applies_detection_zone or applies_detection_full,
			required_count=1 if (applies_detection_zone or applies_detection_full) else 0,
			found_count=scans["fire_detection"]["count"],
			evidence=scans["fire_detection"]["examples"],
			note=det_note,
		)
	)

	hydrants_required = 0
	if has_area and area_m2 >= 5000:
		if area_m2 <= 10000:
			hydrants_required = 1
		else:
			hydrants_required = 1 + math.ceil((area_m2 - 10000) / 10000)

	rules.append(
		_rule_result(
			"SI4-ADM-HYD",
			"Hidrantes exteriores por superficie (Administrativo)",
			"external_hydrants",
			applies=hydrants_required > 0,
			required_count=hydrants_required if hydrants_required > 0 else 0,
			found_count=scans["external_hydrants"]["count"],
			evidence=scans["external_hydrants"]["examples"],
			note="Pueden computarse hidrantes en vía pública a menos de 100 m (nota 3).",
		)
	)

	applies_emergency_lift = bool(has_heights and h_desc_m > 28)
	rules.append(
		_rule_result(
			"SI4-GEN-LIFT",
			"Ascensor de emergencia si altura de evacuación descendente > 28 m",
			"emergency_elevator",
			applies=applies_emergency_lift,
			required_count=1 if applies_emergency_lift else 0,
			found_count=scans["emergency_elevator"]["count"],
			evidence=scans["emergency_elevator"]["examples"],
			note="Regla general de tabla 1.1.",
		)
	)

	applies_auto_ext = bool(has_heights and h_desc_m > 80)
	rules.append(
		_rule_result(
			"SI4-GEN-AUTO-EXT",
			"Instalación automática de extinción si altura de evacuación > 80 m",
			"automatic_extinguishing",
			applies=applies_auto_ext,
			required_count=1 if applies_auto_ext else 0,
			found_count=scans["automatic_extinguishing"]["count"],
			evidence=scans["automatic_extinguishing"]["examples"],
			note="Regla general de tabla 1.1.",
		)
	)

	missing_data: List[str] = []
	if not has_area:
		missing_data.append("No se ha podido calcular la superficie construida a partir de IfcSpace.")
	if not has_heights:
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
	non_compliance_reasons = _build_non_compliance_reasons(rules, missing_data)

	return {
		"file_name": file_name,
		"building_use": ADMIN_BUILDING_USE,
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


def _build_arg_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		description="Check SI-4 fire protection compliance for Administrativo buildings using IFC (ifcopenshell)."
	)
	parser.add_argument("--ifc", required=False, help="Path to IFC file")
	parser.add_argument(
		"--json-out",
		default=None,
		help="Optional path to write JSON report",
	)
	parser.add_argument(
		"--full-report",
		action="store_true",
		help="Print complete report instead of the minimal boolean + RGB payload.",
	)
	return parser


def _resolve_ifc_input(arg_ifc_path: Optional[str]) -> str:
	if arg_ifc_path and arg_ifc_path.strip():
		return arg_ifc_path.strip()

	user_input = input("Enter IFC file path: ").strip()
	if not user_input:
		raise ValueError("IFC path is required.")
	return user_input


def main() -> int:
	parser = _build_arg_parser()
	args = parser.parse_args()
	try:
		ifc_path = _resolve_ifc_input(args.ifc)
	except ValueError as exc:
		print(json.dumps({"complies": False, "error": str(exc)}, ensure_ascii=False))
		return 2

	report = check_si4_administrativo(ifc_path)

	minimal_output = {
		"ifc_file": report.get("file_name"),
		"complies": report.get("complies", False),
		"rgb_non_compliant": report.get("highlight", {}).get("rgb_non_compliant", [255, 0, 0]),
		"non_compliant_items": report.get("highlight", {}).get("items", []),
		"why_not_compliant": report.get("non_compliance_reasons", []),
	}

	if args.full_report:
		print(json.dumps(report, indent=2, ensure_ascii=False))
	else:
		print(json.dumps(minimal_output, indent=2, ensure_ascii=False))

	if args.json_out:
		out_path = Path(args.json_out)
		out_path.parent.mkdir(parents=True, exist_ok=True)
		out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

	if "error" in report:
		return 2
	if report.get("summary", {}).get("overall_status") == "FAIL":
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
