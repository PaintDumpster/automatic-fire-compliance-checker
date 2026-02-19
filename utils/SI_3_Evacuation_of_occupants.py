# ─────────────────────────────────────────────
# IMPORTED LIBRARIES
# ─────────────────────────────────────────────
import ifcopenshell
import ifcopenshell.util.element as element_util
from collections import Counter
import math
import json
import os
import glob


# ─────────────────────────────────────────────
# DATA LOADERS - Load keywords and regulations from JSON files
# ─────────────────────────────────────────────

def get_project_root():
    """Returns the project root directory (parent of utils/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_keywords(language="auto"):
    """
    Load typology keywords from JSON files.

    Args:
        language: Language code ('es', 'en', 'fr') or 'auto' to merge all.

    Returns:
        dict with 'typology_keywords' and 'space_density_keywords'.
    """
    keywords_dir = os.path.join(get_project_root(), "data", "keywords")

    if language == "auto":
        # Merge all keyword files for maximum detection coverage
        merged_typology = {}
        merged_density = {}

        for filepath in glob.glob(os.path.join(keywords_dir, "keywords_*.json")):
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            for tipologia, words in data.get("typology_keywords", {}).items():
                if tipologia not in merged_typology:
                    merged_typology[tipologia] = []
                # Add new words without duplicates
                existing = set(merged_typology[tipologia])
                for w in words:
                    if w not in existing:
                        merged_typology[tipologia].append(w)
                        existing.add(w)

            for category, words in data.get("space_density_keywords", {}).items():
                if category not in merged_density:
                    merged_density[category] = []
                existing = set(merged_density[category])
                for w in words:
                    if w not in existing:
                        merged_density[category].append(w)
                        existing.add(w)

        return {
            "typology_keywords": merged_typology,
            "space_density_keywords": merged_density,
        }
    else:
        filepath = os.path.join(keywords_dir, f"keywords_{language}.json")
        if not os.path.exists(filepath):
            raise FileNotFoundError(
                f"Keywords file not found: {filepath}\n"
                f"Available: {[os.path.basename(f) for f in glob.glob(os.path.join(keywords_dir, 'keywords_*.json'))]}"
            )
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)


def load_regulation(regulation_id):
    """
    Load a regulation from its JSON file.

    Args:
        regulation_id: ID of the regulation (e.g., 'CTE_DBSI_SI3').

    Returns:
        dict with the full regulation data.
    """
    reg_dir = os.path.join(get_project_root(), "data", "regulations")
    filepath = os.path.join(reg_dir, f"{regulation_id}.json")

    if not os.path.exists(filepath):
        available = [os.path.splitext(os.path.basename(f))[0]
                     for f in glob.glob(os.path.join(reg_dir, "*.json"))]
        raise FileNotFoundError(
            f"Regulation file not found: {filepath}\n"
            f"Available regulations: {available}"
        )

    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def list_available_regulations():
    """List all available regulation IDs."""
    reg_dir = os.path.join(get_project_root(), "data", "regulations")
    return [os.path.splitext(os.path.basename(f))[0]
            for f in glob.glob(os.path.join(reg_dir, "*.json"))]


def list_available_languages():
    """List all available keyword language codes."""
    keywords_dir = os.path.join(get_project_root(), "data", "keywords")
    files = glob.glob(os.path.join(keywords_dir, "keywords_*.json"))
    return [os.path.basename(f).replace("keywords_", "").replace(".json", "")
            for f in files]


# ─────────────────────────────────────────────
# FUNCTION 1. Get typology from IFC file (universal, multi-language)
# ─────────────────────────────────────────────

def obtener_espacios_ifc(ifc_path):
    """Extracts names and category descriptions from IFC spaces."""
    model = ifcopenshell.open(ifc_path)
    espacios = []

    for space in model.by_type("IfcSpace"):
        info = {
            "id": space.GlobalId,
            "nombre": space.LongName or space.Name or "",
        }

        for pset in element_util.get_psets(space).values():
            if "Category Description" in pset:
                info["categoria"] = pset["Category Description"]
            if "OmniClass Table 13 Category" in pset:
                info["omniclass"] = pset["OmniClass Table 13 Category"]

        espacios.append(info)

    return model, espacios


def obtener_info_edificio(model):
    """Extracts building information (name, description, category)."""
    info = {}
    buildings = model.by_type("IfcBuilding")
    if buildings:
        building = buildings[0]
        info["nombre"] = building.Name or ""
        info["descripcion"] = building.Description or ""

        for pset in element_util.get_psets(building).values():
            if "Category Description" in pset:
                info["categoria"] = pset["Category Description"]

    storeys = model.by_type("IfcBuildingStorey")
    info["num_plantas"] = len(storeys)
    info["plantas"] = [
        {"nombre": s.Name or "", "elevacion": s.Elevation or 0.0}
        for s in storeys
    ]

    return info


def calcular_puntuacion_tipologia(textos, typology_keywords):
    """
    Scores each typology based on keyword matches found in the texts.

    Args:
        textos: list of strings extracted from IFC.
        typology_keywords: dict {typology_name: [keywords]} from JSON.

    Returns:
        Counter with scores per typology.
    """
    puntuaciones = Counter()
    textos_lower = [t.lower() for t in textos if t]

    for tipologia, keywords in typology_keywords.items():
        for texto in textos_lower:
            for keyword in keywords:
                if keyword in texto:
                    puntuaciones[tipologia] += 1

    return puntuaciones


def detectar_tipologia(ifc_path, language="auto"):
    """
    Detects the building typology from an IFC file using keyword matching.

    Analyzes:
      1. IfcBuilding properties (name, description, Category Description)
      2. IfcSpace names and categories
      3. Filename as fallback

    Args:
        ifc_path: Path to the IFC file.
        language: Language code ('es', 'en', 'fr') or 'auto' for all.

    Returns:
        dict with detected typology, confidence, and analysis details.
    """
    keywords_data = load_keywords(language)
    typology_keywords = keywords_data["typology_keywords"]

    model, espacios = obtener_espacios_ifc(ifc_path)
    info_edificio = obtener_info_edificio(model)

    # Collect all relevant texts for analysis
    textos = []

    # From building
    textos.append(info_edificio.get("nombre", ""))
    textos.append(info_edificio.get("descripcion", ""))
    textos.append(info_edificio.get("categoria", ""))

    # From spaces
    for espacio in espacios:
        textos.append(espacio.get("nombre", ""))
        textos.append(espacio.get("categoria", ""))
        textos.append(espacio.get("omniclass", ""))

    # From filename
    nombre_archivo = ifc_path.split("/")[-1].split("\\")[-1]
    textos.append(nombre_archivo)

    # Calculate scores
    puntuaciones = calcular_puntuacion_tipologia(textos, typology_keywords)

    if not puntuaciones:
        return {
            "tipologia": "No determinada",
            "confianza": 0.0,
            "puntuaciones": {},
            "edificio": info_edificio,
            "num_espacios": len(espacios),
        }

    tipologia_detectada = puntuaciones.most_common(1)[0][0]
    max_puntuacion = puntuaciones.most_common(1)[0][1]
    total = sum(puntuaciones.values())
    confianza = round(max_puntuacion / total, 2) if total > 0 else 0.0

    return {
        "tipologia": tipologia_detectada,
        "confianza": confianza,
        "puntuaciones": dict(puntuaciones.most_common()),
        "edificio": info_edificio,
        "num_espacios": len(espacios),
        "espacios": espacios,
    }


# ─────────────────────────────────────────────
# FUNCTION 2. Get rules from regulation JSON based on typology
# ─────────────────────────────────────────────

def obtener_reglas(tipologia, regulation_id="CTE_DBSI_SI3"):
    """
    Returns evacuation rules for a given typology from the regulation JSON.

    Combines general rules with typology-specific overrides.

    Args:
        tipologia: Detected typology string.
        regulation_id: Regulation ID to load (default: CTE_DBSI_SI3).

    Returns:
        dict with all applicable rules.
    """
    regulation = load_regulation(regulation_id)

    # Start with general rules
    reglas = dict(regulation.get("general_rules", {}))

    # Apply typology-specific overrides
    especificas = regulation.get("typology_rules", {}).get(tipologia, {})
    reglas.update(especificas)

    reglas["tipologia"] = tipologia
    reglas["regulation_id"] = regulation_id
    reglas["regulation_name"] = regulation.get("regulation_name", regulation_id)

    return reglas


def imprimir_reglas(reglas, regulation_id="CTE_DBSI_SI3"):
    """Prints evacuation rules in a readable format."""
    tipologia = reglas.get("tipologia", "Desconocida")
    reg_name = reglas.get("regulation_name", regulation_id)

    print(f"\n{'='*60}")
    print(f"  EVACUATION RULES - {reg_name}")
    print(f"  Typology: {tipologia}")
    print(f"{'='*60}")

    # Load display categories from regulation JSON
    regulation = load_regulation(reglas.get("regulation_id", regulation_id))
    categorias = regulation.get("display_categories", {})

    if not categorias:
        # Fallback: print all rules flat
        for clave, valor in reglas.items():
            if clave in ("tipologia", "regulation_id", "regulation_name"):
                continue
            nombre_legible = clave.replace("_", " ")
            if valor is None:
                valor = "Ver nota"
            elif valor is False:
                valor = "NO SE ADMITE"
            elif isinstance(valor, float) and valor < 1:
                valor = f"{valor*100:.0f}%"
            print(f"    {nombre_legible:<55} {valor}")
    else:
        for nombre_cat, claves in categorias.items():
            reglas_cat = {k: reglas[k] for k in claves if k in reglas}
            if not reglas_cat:
                continue

            print(f"\n  --- {nombre_cat} ---")
            for clave, valor in reglas_cat.items():
                nombre_legible = clave.replace("_", " ")
                if valor is None:
                    valor = "Ver nota"
                elif valor is False:
                    valor = "NO SE ADMITE"
                elif isinstance(valor, float) and valor < 1:
                    valor = f"{valor*100:.0f}%"
                print(f"    {nombre_legible:<55} {valor}")

    print(f"\n{'='*60}")


# ─────────────────────────────────────────────
# FUNCTION 3. Calculate occupancy and evaluate compliance
# ─────────────────────────────────────────────

def obtener_area_espacio(model, space):
    """Extracts area (m2) of an IfcSpace from its property sets."""
    for pset in element_util.get_psets(space).values():
        if "Area" in pset:
            area = pset["Area"]
            if isinstance(area, (int, float)):
                return area
    return 0.0


def calcular_ocupacion(ifc_path, tipologia, reglas, language="auto",
                       regulation_id="CTE_DBSI_SI3"):
    """
    Calculates real occupancy from IFC areas and regulation density values.

    Occupancy per space = ceil(Area / Density)

    Args:
        ifc_path: Path to IFC file.
        tipologia: Detected CTE typology.
        reglas: Dict of applicable rules.
        language: Language code for keyword matching.
        regulation_id: Regulation ID for density map.

    Returns:
        dict with total occupancy, per floor, per space, and evacuation height.
    """
    model = ifcopenshell.open(ifc_path)  # noqa: F841

    # Load density map from regulation JSON
    regulation = load_regulation(regulation_id)
    density_map = regulation.get("space_density_map", {})

    # Load space density keywords for matching
    keywords_data = load_keywords(language)
    space_keywords = keywords_data.get("space_density_keywords", {})

    # Default density from typology rules
    densidad_defecto = reglas.get("densidad_ocupacion_m2_persona", 20)

    # Organize spaces by storey
    storeys = model.by_type("IfcBuildingStorey")
    storeys_sorted = sorted(storeys, key=lambda s: s.Elevation or 0.0)

    ocupacion_por_planta = {}
    espacios_detalle = []
    superficie_total = 0.0
    ocupacion_total = 0

    for storey in storeys_sorted:
        nombre_planta = storey.Name or "Sin nombre"
        elevacion = storey.Elevation or 0.0
        ocupacion_planta = 0
        superficie_planta = 0.0

        for rel in model.by_type("IfcRelAggregates"):
            if rel.RelatingObject == storey:
                for obj in rel.RelatedObjects:
                    if obj.is_a("IfcSpace"):
                        area = obtener_area_espacio(model, obj)
                        nombre_espacio = obj.LongName or obj.Name or ""

                        categoria = ""
                        for pset in element_util.get_psets(obj).values():
                            if "Category Description" in pset:
                                categoria = pset["Category Description"]

                        # Determine density using keyword matching from JSON
                        densidad = densidad_defecto
                        cat_lower = categoria.lower()
                        nombre_lower = nombre_espacio.lower()
                        texto_busqueda = f"{cat_lower} {nombre_lower}"

                        matched = False
                        for density_category, kw_list in space_keywords.items():
                            for kw in kw_list:
                                if kw in texto_busqueda:
                                    densidad = density_map.get(
                                        density_category, densidad_defecto)
                                    matched = True
                                    break
                            if matched:
                                break

                        # Calculate occupancy
                        if densidad is None or area == 0:
                            ocupantes = 0
                        else:
                            ocupantes = math.ceil(area / densidad)

                        superficie_planta += area
                        ocupacion_planta += ocupantes

                        espacios_detalle.append({
                            "planta": nombre_planta,
                            "espacio": nombre_espacio,
                            "categoria": categoria,
                            "area_m2": round(area, 2),
                            "densidad_m2_persona": densidad,
                            "ocupantes": ocupantes,
                        })

        superficie_total += superficie_planta
        ocupacion_total += ocupacion_planta
        ocupacion_por_planta[nombre_planta] = {
            "elevacion_m": elevacion,
            "superficie_m2": round(superficie_planta, 2),
            "ocupantes": ocupacion_planta,
        }

    # Calculate evacuation height
    elevaciones = [s.Elevation or 0.0 for s in storeys_sorted]
    planta_salida = min(e for e in elevaciones if e >= 0) if any(e >= 0 for e in elevaciones) else 0.0
    planta_mas_alta = max(elevaciones)
    planta_mas_baja = min(elevaciones)
    altura_evacuacion_desc = planta_mas_alta - planta_salida
    altura_evacuacion_asc = planta_salida - planta_mas_baja if planta_mas_baja < planta_salida else 0.0

    return {
        "ocupacion_total": ocupacion_total,
        "superficie_total_m2": round(superficie_total, 2),
        "ocupacion_por_planta": ocupacion_por_planta,
        "espacios": espacios_detalle,
        "altura_evacuacion_descendente_m": round(altura_evacuacion_desc, 2),
        "altura_evacuacion_ascendente_m": round(altura_evacuacion_asc, 2),
        "planta_salida_elevacion_m": planta_salida,
    }


def evaluar_cumplimiento(ocupacion, reglas):
    """
    Evaluates if the building complies with evacuation rules
    based on calculated occupancy and typology rules.

    Args:
        ocupacion: dict from calcular_ocupacion.
        reglas: dict from obtener_reglas.

    Returns:
        list of dicts with each check and its result.
    """
    verificaciones = []
    ocup_total = ocupacion["ocupacion_total"]
    h_desc = ocupacion["altura_evacuacion_descendente_m"]
    h_asc = ocupacion["altura_evacuacion_ascendente_m"]
    tipologia = reglas.get("tipologia", "")

    # 1. Max occupancy with single exit
    max_1_salida = reglas.get("ocupacion_max_1_salida", 100)
    if max_1_salida is not None:
        cumple = ocup_total <= max_1_salida
        verificaciones.append({
            "regla": f"Ocupacion max. con 1 salida ({max_1_salida} pers.)",
            "valor_edificio": f"{ocup_total} personas",
            "limite": f"{max_1_salida} personas",
            "resultado": "CUMPLE" if cumple else "NO CUMPLE - Se requieren mas salidas",
        })

    # 2. Max descending evacuation height (single exit)
    h_max = reglas.get("altura_evacuacion_desc_max_1_salida_m")
    if h_max is not None:
        cumple = h_desc <= h_max
        verificaciones.append({
            "regla": f"Altura evacuacion descendente max. con 1 salida ({h_max} m)",
            "valor_edificio": f"{h_desc} m",
            "limite": f"{h_max} m",
            "resultado": "CUMPLE" if cumple else "NO CUMPLE - Se requieren mas salidas",
        })

    # 3. Max ascending evacuation height (single exit)
    if h_asc > 0:
        h_max_asc = reglas.get("altura_evacuacion_asc_max_1_salida_m", 10)
        cumple = h_asc <= h_max_asc
        verificaciones.append({
            "regla": f"Altura evacuacion ascendente max. con 1 salida ({h_max_asc} m)",
            "valor_edificio": f"{h_asc} m",
            "limite": f"{h_max_asc} m",
            "resultado": "CUMPLE" if cumple else "NO CUMPLE",
        })

    # 4. Descending stair protection
    h_no_prot = reglas.get("escalera_desc_no_protegida_max_h_m")
    h_prot = reglas.get("escalera_desc_protegida_max_h_m")
    if h_no_prot is not None and h_desc > 0:
        if h_desc <= h_no_prot:
            tipo_escalera = "No protegida (suficiente)"
        elif h_prot is not None and h_desc <= h_prot:
            tipo_escalera = "Protegida (requerida)"
        else:
            tipo_escalera = "Especialmente protegida (requerida)"
        verificaciones.append({
            "regla": "Tipo de escalera requerida (evacuacion descendente)",
            "valor_edificio": f"{h_desc} m de altura",
            "limite": f"No protegida: h<={h_no_prot}m | Protegida: h<={h_prot}m",
            "resultado": tipo_escalera,
        })

    # 5. Ascending stair protection
    if h_asc > 0:
        h_asc_no_prot = reglas.get("escalera_asc_no_protegida_max_h_m", 2.80)
        if reglas.get("escalera_asc_no_protegida") == "No se admite":
            tipo_esc_asc = "Especialmente protegida (requerida)"
        elif h_asc <= h_asc_no_prot:
            tipo_esc_asc = "No protegida (suficiente)"
        elif h_asc <= 6.0:
            tipo_esc_asc = f"No protegida (max {reglas.get('escalera_asc_no_protegida_max_personas', 100)} pers.) o Protegida"
        else:
            tipo_esc_asc = "Protegida o Especialmente protegida (requerida)"
        verificaciones.append({
            "regla": "Tipo de escalera requerida (evacuacion ascendente)",
            "valor_edificio": f"{h_asc} m de altura",
            "limite": f"No protegida: h<={h_asc_no_prot}m",
            "resultado": tipo_esc_asc,
        })

    # 6. Door opening direction
    limite_puertas = reglas.get("puertas_sentido_evacuacion_personas", 100)
    necesita = ocup_total > limite_puertas
    verificaciones.append({
        "regla": f"Puertas abren en sentido evacuacion (>{limite_puertas} pers.)",
        "valor_edificio": f"{ocup_total} personas",
        "limite": f"{limite_puertas} personas",
        "resultado": "REQUERIDO" if necesita else "No requerido",
    })

    # 7. Minimum door width (A >= P/200 >= 0.80 m)
    anchura_min_puerta = max(ocup_total / 200, 0.80)
    verificaciones.append({
        "regla": "Anchura minima puertas de evacuacion",
        "valor_edificio": f"P={ocup_total} personas",
        "limite": f"A >= P/200 >= 0.80 m",
        "resultado": f"Anchura minima requerida: {anchura_min_puerta:.2f} m",
    })

    # 8. Minimum corridor width (A >= P/200 >= 1.00 m)
    anchura_min_pasillo = max(ocup_total / 200, 1.00)
    anchura_min_especifica = reglas.get("anchura_min_pasillos_m")
    if anchura_min_especifica:
        anchura_min_pasillo = max(anchura_min_pasillo, anchura_min_especifica)
    verificaciones.append({
        "regla": "Anchura minima pasillos de evacuacion",
        "valor_edificio": f"P={ocup_total} personas",
        "limite": f"A >= P/200 >= 1.00 m",
        "resultado": f"Anchura minima requerida: {anchura_min_pasillo:.2f} m",
    })

    # 9. Unprotected descending stair width (A >= P/160)
    if h_desc > 0:
        anchura_min_esc = ocup_total / 160
        verificaciones.append({
            "regla": "Anchura minima escalera no protegida (descendente)",
            "valor_edificio": f"P={ocup_total} personas",
            "limite": "A >= P/160",
            "resultado": f"Anchura minima requerida: {anchura_min_esc:.2f} m",
        })

    # 10. Unprotected ascending stair width (A >= P/(160-10h))
    if h_asc > 0:
        divisor = 160 - 10 * h_asc
        if divisor > 0:
            anchura_min_esc_asc = ocup_total / divisor
            verificaciones.append({
                "regla": "Anchura minima escalera no protegida (ascendente)",
                "valor_edificio": f"P={ocup_total}, h={h_asc}m",
                "limite": "A >= P/(160-10h)",
                "resultado": f"Anchura minima requerida: {anchura_min_esc_asc:.2f} m",
            })

    # 11. Smoke control
    control_humo_min = reglas.get("control_humo_ocupacion_min")
    control_humo_fijo = reglas.get("control_humo")
    if control_humo_fijo:
        verificaciones.append({
            "regla": "Control de humo de incendio",
            "valor_edificio": f"{ocup_total} personas",
            "limite": control_humo_fijo,
            "resultado": "REQUERIDO",
        })
    elif control_humo_min:
        necesita = ocup_total > control_humo_min
        verificaciones.append({
            "regla": f"Control de humo de incendio (>{control_humo_min} pers.)",
            "valor_edificio": f"{ocup_total} personas",
            "limite": f"{control_humo_min} personas",
            "resultado": "REQUERIDO" if necesita else "No requerido",
        })

    # 12. Disability refuge zones
    evac_disc_h = reglas.get("evacuacion_discapacidad_altura_min_m")
    if evac_disc_h and h_desc > evac_disc_h:
        plazas_silla = math.ceil(ocup_total / 100)
        plazas_movilidad = math.ceil(ocup_total / 33) if tipologia != "Residencial Vivienda" else 0
        verificaciones.append({
            "regla": f"Zonas de refugio (altura evac. > {evac_disc_h} m)",
            "valor_edificio": f"h={h_desc}m, {ocup_total} personas",
            "limite": "1 silla/100 pers. + 1 movilidad/33 pers.",
            "resultado": f"Plazas silla: {plazas_silla}, Plazas movilidad reducida: {plazas_movilidad}",
        })

    return verificaciones


# ─────────────────────────────────────────────
# PRINT FUNCTIONS
# ─────────────────────────────────────────────

def imprimir_ocupacion(ocupacion):
    """Prints occupancy calculation in a readable format."""
    print(f"\n{'='*60}")
    print(f"  OCCUPANCY CALCULATION")
    print(f"{'='*60}")
    print(f"  Total area: {ocupacion['superficie_total_m2']} m2")
    print(f"  Total occupancy: {ocupacion['ocupacion_total']} persons")
    print(f"  Descending evacuation height: {ocupacion['altura_evacuacion_descendente_m']} m")
    print(f"  Ascending evacuation height: {ocupacion['altura_evacuacion_ascendente_m']} m")

    print(f"\n  --- Occupancy per floor ---")
    for nombre, datos in ocupacion["ocupacion_por_planta"].items():
        print(f"    {nombre:<20} elev: {datos['elevacion_m']:>6.2f}m | "
              f"area: {datos['superficie_m2']:>8.2f} m2 | "
              f"occ: {datos['ocupantes']:>3} pers.")

    print(f"\n  --- Detail per space ---")
    for esp in ocupacion["espacios"]:
        dens_str = f"{esp['densidad_m2_persona']}" if esp['densidad_m2_persona'] else "null"
        print(f"    {esp['planta']:<12} {esp['espacio']:<18} {esp['categoria']:<25} "
              f"{esp['area_m2']:>7.2f} m2 / {dens_str:<5} = {esp['ocupantes']:>2} pers.")

    print(f"{'='*60}")


def imprimir_cumplimiento(verificaciones):
    """Prints compliance evaluation results."""
    print(f"\n{'='*60}")
    print(f"  COMPLIANCE EVALUATION")
    print(f"{'='*60}")

    for v in verificaciones:
        res = v["resultado"]
        if "NO CUMPLE" in res:
            indicador = "[X]"
        elif "REQUERIDO" in res:
            indicador = "[!]"
        elif "CUMPLE" in res:
            indicador = "[OK]"
        else:
            indicador = "[i]"

        print(f"\n  {indicador} {v['regla']}")
        print(f"      Building: {v['valor_edificio']}")
        print(f"      Limit:    {v['limite']}")
        print(f"      >>> {res}")

    print(f"\n{'='*60}")
    print(f"  Legend: [OK]=Pass  [X]=Fail  [!]=Required  [i]=Info")
    print(f"{'='*60}")


# ==========================================================
#  CONFIGURATION: Set your file paths and options here
# ==========================================================

# Path to the IFC building file to analyze
RUTA_IFC = r"C:\Users\usuario\Documents\GitHub\automatic-fire-compliance-checker\data_PDF_LUISA\01_Duplex_Apartment.ifc"

# Regulation to use (JSON file name without extension from data/regulations/)
REGULATION_ID = "CTE_DBSI_SI3"

# Language for keyword detection: 'es', 'en', 'fr', or 'auto' (merges all)
LANGUAGE = "auto"

# ==========================================================


# --- Main execution ---
if __name__ == "__main__":
    # Show available configurations
    print(f"\n{'='*60}")
    print(f"  AUTOMATIC FIRE COMPLIANCE CHECKER")
    print(f"{'='*60}")
    print(f"  Available regulations: {list_available_regulations()}")
    print(f"  Available languages:   {list_available_languages()}")
    print(f"  Selected regulation:   {REGULATION_ID}")
    print(f"  Selected language:     {LANGUAGE}")
    print(f"{'='*60}")

    # STEP 1: Detect typology
    resultado = detectar_tipologia(RUTA_IFC, language=LANGUAGE)

    print(f"\n{'='*60}")
    print(f"  TYPOLOGY DETECTION")
    print(f"{'='*60}")
    print(f"  File: {RUTA_IFC}")
    print(f"  Building: {resultado['edificio'].get('nombre', 'Unknown')}")
    print(f"  IFC Category: {resultado['edificio'].get('categoria', 'N/A')}")
    print(f"  Floors: {resultado['edificio'].get('num_plantas', 0)}")
    print(f"  Spaces analyzed: {resultado['num_espacios']}")
    print(f"{'='*60}")
    print(f"  DETECTED TYPOLOGY: {resultado['tipologia']}")
    print(f"  Confidence: {resultado['confianza']*100:.0f}%")
    print(f"{'='*60}")

    if resultado["puntuaciones"]:
        print(f"\n  Score breakdown:")
        for tip, pts in resultado["puntuaciones"].items():
            barra = "#" * pts
            print(f"    {tip:<25} {pts:>3} {barra}")

    # STEP 2: Get applicable rules
    reglas = obtener_reglas(resultado["tipologia"], regulation_id=REGULATION_ID)
    imprimir_reglas(reglas, regulation_id=REGULATION_ID)

    # STEP 3: Calculate real occupancy
    ocupacion = calcular_ocupacion(
        RUTA_IFC, resultado["tipologia"], reglas,
        language=LANGUAGE, regulation_id=REGULATION_ID
    )
    imprimir_ocupacion(ocupacion)

    # STEP 4: Evaluate compliance
    verificaciones = evaluar_cumplimiento(ocupacion, reglas)
    imprimir_cumplimiento(verificaciones)
