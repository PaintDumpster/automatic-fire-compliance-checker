# ─────────────────────────────────────────────
# IMPORTED LIBRARIES
# ─────────────────────────────────────────────
import ifcopenshell
import ifcopenshell.util.element as element_util
from collections import Counter

# ─────────────────────────────────────────────
# FUNCTION 1. Get typology from IFC file (spanish)
# ─────────────────────────────────────────────

# Tipologías del CTE DB-SI
TIPOLOGIAS_CTE = [
    "Residencial Vivienda",
    "Residencial Público",
    "Administrativo",
    "Docente",
    "Hospitalario",
    "Comercial",
    "Pública Concurrencia",
    "Aparcamiento",
]

# Palabras clave asociadas a cada tipología (en minúsculas)
KEYWORDS_TIPOLOGIA = {
    "Residencial Vivienda": [
        "vivienda", "duplex", "apartment", "dwelling", "residential",
        "bedroom", "living room", "living spaces", "kitchen", "bathroom",
        "dormitorio", "cocina", "salón", "habitación",
        "general residential space",
    ],
    "Residencial Público": [
        "hotel", "hostel", "alojamiento", "resort", "motel",
        "guest room", "suite", "lobby", "reception",
    ],
    "Administrativo": [
        "office", "oficina", "despacho", "meeting room", "conference",
        "sala de reuniones", "workstation", "administrative",
    ],
    "Docente": [
        "classroom", "aula", "school", "escuela", "university",
        "universidad", "laboratory", "laboratorio", "library",
        "biblioteca", "gymnasium", "gimnasio", "teaching",
    ],
    "Hospitalario": [
        "hospital", "clinic", "clínica", "surgery", "ward",
        "patient", "paciente", "emergency", "urgencias",
        "operating", "quirófano", "consultation", "consulta",
        "diagnosis", "diagnóstico",
    ],
    "Comercial": [
        "shop", "tienda", "retail", "store", "comercial", "mall",
        "centro comercial", "market", "mercado", "sales", "ventas",
        "shopping",
    ],
    "Pública Concurrencia": [
        "auditorium", "auditorio", "theater", "teatro", "cinema",
        "cine", "stadium", "estadio", "restaurant", "restaurante",
        "bar", "cafetería", "museum", "museo", "exhibition",
        "exposición", "disco", "nightclub", "swimming pool", "piscina",
        "sports", "deportivo", "arena", "concert",
    ],
    "Aparcamiento": [
        "parking", "aparcamiento", "garage", "garaje", "car park",
        "estacionamiento",
    ],
}


def obtener_espacios_ifc(ifc_path):
    """Extrae los nombres y descripciones de categoría de los espacios del IFC."""
    model = ifcopenshell.open(ifc_path)
    espacios = []

    for space in model.by_type("IfcSpace"):
        info = {
            "id": space.GlobalId,
            "nombre": space.LongName or space.Name or "",
        }

        # Extraer Category Description y OmniClass de los property sets
        for pset in element_util.get_psets(space).values():
            if "Category Description" in pset:
                info["categoria"] = pset["Category Description"]
            if "OmniClass Table 13 Category" in pset:
                info["omniclass"] = pset["OmniClass Table 13 Category"]

        espacios.append(info)

    return model, espacios


def obtener_info_edificio(model):
    """Extrae información del edificio (nombre, descripción, categoría)."""
    info = {}
    buildings = model.by_type("IfcBuilding")
    if buildings:
        building = buildings[0]
        info["nombre"] = building.Name or ""
        info["descripcion"] = building.Description or ""

        for pset in element_util.get_psets(building).values():
            if "Category Description" in pset:
                info["categoria"] = pset["Category Description"]

    # Extraer plantas
    storeys = model.by_type("IfcBuildingStorey")
    info["num_plantas"] = len(storeys)
    info["plantas"] = [
        {"nombre": s.Name or "", "elevacion": s.Elevation or 0.0}
        for s in storeys
    ]

    return info


def calcular_puntuacion_tipologia(textos):
    """Calcula la puntuación de cada tipología según las palabras clave encontradas."""
    puntuaciones = Counter()
    textos_lower = [t.lower() for t in textos if t]

    for tipologia, keywords in KEYWORDS_TIPOLOGIA.items():
        for texto in textos_lower:
            for keyword in keywords:
                if keyword in texto:
                    puntuaciones[tipologia] += 1

    return puntuaciones


def detectar_tipologia(ifc_path):
    """
    Detecta la tipología CTE DB-SI de un edificio a partir de su archivo IFC.

    Analiza:
      1. Propiedades del IfcBuilding (nombre, descripción, Category Description)
      2. Nombres y categorías de los IfcSpace
      3. Nombre del archivo como fallback

    Returns:
        dict con tipología detectada, confianza y detalle del análisis.
    """
    model, espacios = obtener_espacios_ifc(ifc_path)
    info_edificio = obtener_info_edificio(model)

    # Recopilar todos los textos relevantes para el análisis
    textos = []

    # Del edificio
    textos.append(info_edificio.get("nombre", ""))
    textos.append(info_edificio.get("descripcion", ""))
    textos.append(info_edificio.get("categoria", ""))

    # De los espacios
    for espacio in espacios:
        textos.append(espacio.get("nombre", ""))
        textos.append(espacio.get("categoria", ""))
        textos.append(espacio.get("omniclass", ""))

    # Del nombre del archivo
    nombre_archivo = ifc_path.split("/")[-1].split("\\")[-1]
    textos.append(nombre_archivo)

    # Calcular puntuaciones
    puntuaciones = calcular_puntuacion_tipologia(textos)

    if not puntuaciones:
        return {
            "tipologia": "No determinada",
            "confianza": 0.0,
            "puntuaciones": {},
            "edificio": info_edificio,
            "num_espacios": len(espacios),
        }

    # La tipología con mayor puntuación
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
# FUNCTION 2. based on typology, determine which rules to apply
# Fuente: CTE DB-SI Sección 3 (DBSI-22-31.pdf)
# ─────────────────────────────────────────────

# Reglas generales que aplican a TODAS las tipologías (Tabla 3.1)
REGLAS_GENERALES = {
    "recorrido_max_1_salida_m": 25,
    "recorrido_max_varias_salidas_m": 50,
    "ocupacion_max_1_salida": 100,
    "altura_evacuacion_desc_max_1_salida_m": 28,
    "altura_evacuacion_asc_max_1_salida_m": 10,
    "recorrido_max_aire_libre_m": 75,
    "recorrido_hasta_2_alternativas_m": 25,
    "bonus_extincion_automatica": 0.25,
    "ocupacion_max_1_salida_evac_ascendente_mayor_2m": 50,
    "puertas_sentido_evacuacion_personas": 100,
    "escalera_asc_no_protegida_max_h_m": 2.80,
    "escalera_asc_no_protegida_max_personas": 100,
    "escalera_asc_no_protegida_max_h_sin_limite_m": 2.80,
    "escalera_asc_protegida": "Se admite en todo caso",
    "escalera_asc_especialmente_protegida": "Se admite en todo caso",
}

# Reglas específicas por tipología (excepciones a las generales)
REGLAS_POR_TIPOLOGIA = {
    "Residencial Vivienda": {
        "ocupacion_max_1_salida": 500,
        "ocupacion_max_1_salida_nota": "500 personas en el conjunto del edificio (salida de edificio de viviendas)",
        "puertas_sentido_evacuacion_personas": 200,
        "escalera_desc_no_protegida_max_h_m": 14,
        "escalera_desc_protegida_max_h_m": 28,
        "escalera_desc_especialmente_protegida": "Se admite en todo caso",
        "densidad_ocupacion_m2_persona": 20,
        "planta_salida_mas_de_1_salida_ocupacion": 500,
    },
    "Residencial Público": {
        "altura_evacuacion_desc_max_1_salida_m": None,
        "altura_evacuacion_desc_max_1_salida_nota": "Como maximo la 2a planta por encima de la salida del edificio",
        "recorrido_max_varias_salidas_ocupantes_duermen_m": 35,
        "escalera_desc_no_protegida_max_h_m": None,
        "escalera_desc_no_protegida_nota": "Baja mas una planta",
        "escalera_desc_protegida_max_h_m": 28,
        "escalera_desc_protegida_nota": "Si <20 plazas con deteccion y alarma, se puede usar limite general de 28 m",
        "escalera_desc_especialmente_protegida": "Se admite en todo caso",
        "densidad_ocupacion_m2_persona": 20,
        "evacuacion_discapacidad_altura_min_m": 14,
    },
    "Administrativo": {
        "escalera_desc_no_protegida_max_h_m": 14,
        "escalera_desc_protegida_max_h_m": 28,
        "escalera_desc_especialmente_protegida": "Se admite en todo caso",
        "densidad_ocupacion_m2_persona": 10,
        "evacuacion_discapacidad_altura_min_m": 14,
    },
    "Docente": {
        "ocupacion_max_1_salida_escuelas": 50,
        "ocupacion_max_1_salida_escuelas_nota": "50 alumnos en escuelas infantiles, primaria o secundaria",
        "recorrido_max_varias_salidas_escuela_infantil_primaria_m": 35,
        "escalera_desc_no_protegida_max_h_m": 14,
        "escalera_desc_protegida_max_h_m": 28,
        "escalera_desc_especialmente_protegida": "Se admite en todo caso",
        "densidad_ocupacion_m2_persona": 10,
        "densidad_ocupacion_aulas_m2_persona": 1.5,
        "densidad_ocupacion_escuelas_infantiles_m2_persona": 2,
        "evacuacion_discapacidad_altura_min_m": 14,
    },
    "Hospitalario": {
        "salida_unica_hospitalizacion": False,
        "salida_unica_hospitalizacion_nota": "NO se admite salida unica en plantas de hospitalizacion o tratamiento intensivo",
        "salida_unica_salas_pacientes_max_m2": 90,
        "salida_unica_salas_pacientes_nota": "NO se admite salida unica en salas de pacientes >90 m2",
        "recorrido_max_varias_salidas_hospitalizacion_m": 35,
        "recorrido_hasta_2_alternativas_hospitalizacion_m": 15,
        "escalera_desc_no_protegida_hospitalizacion": "No se admite",
        "escalera_desc_protegida_hospitalizacion_max_h_m": 14,
        "escalera_desc_no_protegida_otras_zonas_max_h_m": 10,
        "escalera_desc_protegida_otras_zonas_max_h_m": 20,
        "escalera_desc_especialmente_protegida": "Se admite en todo caso",
        "anchura_min_puertas_m": 1.05,
        "anchura_min_pasillos_m": 2.20,
        "anchura_min_paso_puertas_m": 2.10,
        "densidad_ocupacion_salas_espera_m2_persona": 2,
        "densidad_ocupacion_hospitalizacion_m2_persona": 15,
        "densidad_ocupacion_ambulatorio_m2_persona": 10,
        "densidad_ocupacion_tratamiento_m2_persona": 20,
    },
    "Comercial": {
        "escalera_desc_no_protegida_max_h_m": 10,
        "escalera_desc_protegida_max_h_m": 20,
        "escalera_desc_especialmente_protegida": "Se admite en todo caso",
        "control_humo_ocupacion_min": 1000,
        "anchura_pasillos_venta_mayor_400m2_con_carros_cajas_m": 4.00,
        "anchura_pasillos_venta_mayor_400m2_con_carros_otros_m": 1.80,
        "anchura_pasillos_venta_mayor_400m2_sin_carros_m": 1.40,
        "anchura_pasillos_venta_menor_400m2_con_carros_cajas_m": 3.00,
        "anchura_pasillos_venta_menor_400m2_con_carros_otros_m": 1.40,
        "anchura_pasillos_venta_menor_400m2_sin_carros_m": 1.20,
        "densidad_ocupacion_ventas_sotano_baja_m2_persona": 2,
        "densidad_ocupacion_ventas_otras_m2_persona": 3,
        "evacuacion_discapacidad_altura_min_m": 10,
    },
    "Pública Concurrencia": {
        "escalera_desc_no_protegida_max_h_m": 10,
        "escalera_desc_protegida_max_h_m": 20,
        "escalera_desc_especialmente_protegida": "Se admite en todo caso",
        "control_humo_ocupacion_min": 1000,
        "evacuacion_discapacidad_altura_min_m": 10,
    },
    "Aparcamiento": {
        "recorrido_max_1_salida_m": 35,
        "recorrido_max_1_salida_directo_exterior_m": 50,
        "recorrido_max_1_salida_directo_exterior_nota": "Solo si salida directa al exterior y ocupacion <= 25 personas",
        "escalera_desc_no_protegida": "No se admite",
        "escalera_desc_protegida": "No se admite",
        "escalera_desc_especialmente_protegida": "Se admite en todo caso",
        "escalera_asc_no_protegida": "No se admite",
        "escalera_asc_protegida": "No se admite",
        "escalera_asc_especialmente_protegida": "Se admite en todo caso",
        "control_humo": "Obligatorio (salvo aparcamiento abierto)",
        "densidad_ocupacion_con_horario_m2_persona": 15,
        "densidad_ocupacion_otros_m2_persona": 40,
    },
}


def obtener_reglas(tipologia):
    """
    Devuelve las reglas de evacuacion aplicables a una tipologia.

    Combina las reglas generales (DB-SI Seccion 3, Tabla 3.1) con las
    reglas especificas de la tipologia detectada. Las reglas especificas
    sobreescriben a las generales cuando hay conflicto.

    Args:
        tipologia: str con la tipologia CTE (ej: "Residencial Vivienda")

    Returns:
        dict con todas las reglas aplicables.
    """
    # Empezar con las reglas generales
    reglas = dict(REGLAS_GENERALES)

    # Aplicar las reglas especificas de la tipologia (sobreescriben las generales)
    especificas = REGLAS_POR_TIPOLOGIA.get(tipologia, {})
    reglas.update(especificas)

    reglas["tipologia"] = tipologia
    return reglas


def imprimir_reglas(reglas):
    """Imprime las reglas de evacuacion de forma legible."""
    tipologia = reglas.get("tipologia", "Desconocida")

    print(f"\n{'='*60}")
    print(f"  REGLAS DE EVACUACION - DB-SI Seccion 3")
    print(f"  Tipologia: {tipologia}")
    print(f"{'='*60}")

    # Agrupar las reglas por categoría para mejor lectura
    categorias = {
        "SALIDAS Y RECORRIDOS (1 salida)": [
            "recorrido_max_1_salida_m",
            "recorrido_max_1_salida_directo_exterior_m",
            "recorrido_max_1_salida_directo_exterior_nota",
            "ocupacion_max_1_salida",
            "ocupacion_max_1_salida_nota",
            "ocupacion_max_1_salida_escuelas",
            "ocupacion_max_1_salida_escuelas_nota",
            "ocupacion_max_1_salida_evac_ascendente_mayor_2m",
            "altura_evacuacion_desc_max_1_salida_m",
            "altura_evacuacion_desc_max_1_salida_nota",
            "altura_evacuacion_asc_max_1_salida_m",
            "salida_unica_hospitalizacion",
            "salida_unica_hospitalizacion_nota",
            "salida_unica_salas_pacientes_max_m2",
            "salida_unica_salas_pacientes_nota",
        ],
        "SALIDAS Y RECORRIDOS (varias salidas)": [
            "recorrido_max_varias_salidas_m",
            "recorrido_max_varias_salidas_ocupantes_duermen_m",
            "recorrido_max_varias_salidas_hospitalizacion_m",
            "recorrido_max_varias_salidas_escuela_infantil_primaria_m",
            "recorrido_max_aire_libre_m",
            "recorrido_hasta_2_alternativas_m",
            "recorrido_hasta_2_alternativas_hospitalizacion_m",
            "bonus_extincion_automatica",
        ],
        "ESCALERAS EVACUACION DESCENDENTE": [
            "escalera_desc_no_protegida_max_h_m",
            "escalera_desc_no_protegida_nota",
            "escalera_desc_no_protegida_hospitalizacion",
            "escalera_desc_no_protegida_otras_zonas_max_h_m",
            "escalera_desc_no_protegida",
            "escalera_desc_protegida_max_h_m",
            "escalera_desc_protegida_nota",
            "escalera_desc_protegida_hospitalizacion_max_h_m",
            "escalera_desc_protegida_otras_zonas_max_h_m",
            "escalera_desc_protegida",
            "escalera_desc_especialmente_protegida",
        ],
        "ESCALERAS EVACUACION ASCENDENTE": [
            "escalera_asc_no_protegida_max_h_m",
            "escalera_asc_no_protegida_max_h_sin_limite_m",
            "escalera_asc_no_protegida_max_personas",
            "escalera_asc_no_protegida",
            "escalera_asc_protegida",
            "escalera_asc_especialmente_protegida",
        ],
        "DIMENSIONADO Y PUERTAS": [
            "puertas_sentido_evacuacion_personas",
            "anchura_min_puertas_m",
            "anchura_min_pasillos_m",
            "anchura_min_paso_puertas_m",
            "anchura_pasillos_venta_mayor_400m2_con_carros_cajas_m",
            "anchura_pasillos_venta_mayor_400m2_con_carros_otros_m",
            "anchura_pasillos_venta_mayor_400m2_sin_carros_m",
            "anchura_pasillos_venta_menor_400m2_con_carros_cajas_m",
            "anchura_pasillos_venta_menor_400m2_con_carros_otros_m",
            "anchura_pasillos_venta_menor_400m2_sin_carros_m",
        ],
        "OCUPACION (densidad)": [
            "densidad_ocupacion_m2_persona",
            "densidad_ocupacion_aulas_m2_persona",
            "densidad_ocupacion_escuelas_infantiles_m2_persona",
            "densidad_ocupacion_salas_espera_m2_persona",
            "densidad_ocupacion_hospitalizacion_m2_persona",
            "densidad_ocupacion_ambulatorio_m2_persona",
            "densidad_ocupacion_tratamiento_m2_persona",
            "densidad_ocupacion_ventas_sotano_baja_m2_persona",
            "densidad_ocupacion_ventas_otras_m2_persona",
            "densidad_ocupacion_con_horario_m2_persona",
            "densidad_ocupacion_otros_m2_persona",
        ],
        "CONTROL DE HUMO Y DISCAPACIDAD": [
            "control_humo",
            "control_humo_ocupacion_min",
            "evacuacion_discapacidad_altura_min_m",
            "planta_salida_mas_de_1_salida_ocupacion",
        ],
    }

    for nombre_cat, claves in categorias.items():
        # Solo mostrar categorías que tengan reglas aplicables
        reglas_cat = {k: reglas[k] for k in claves if k in reglas}
        if not reglas_cat:
            continue

        print(f"\n  --- {nombre_cat} ---")
        for clave, valor in reglas_cat.items():
            nombre_legible = clave.replace("_", " ").replace(" m2", " m2").replace(" m ", " m ")
            if valor is None:
                valor = "Ver nota"
            elif valor is False:
                valor = "NO SE ADMITE"
            elif isinstance(valor, float) and valor < 1:
                valor = f"{valor*100:.0f}%"
            print(f"    {nombre_legible:<55} {valor}")

    print(f"\n{'='*60}")


# ─────────────────────────────────────────────
# FUNCTION 3. Calcular ocupacion y evaluar reglas con datos reales del IFC
# Fuente: CTE DB-SI Sección 3, Tabla 2.1 (DBSI-22-31.pdf)
# ─────────────────────────────────────────────

import math


def obtener_area_espacio(model, space):
    """Extrae el area (m2) de un IfcSpace desde sus property sets."""
    for pset in element_util.get_psets(space).values():
        if "Area" in pset:
            area = pset["Area"]
            if isinstance(area, (int, float)):
                return area
    return 0.0


def calcular_ocupacion(ifc_path, tipologia, reglas):
    """
    Calcula la ocupacion real del edificio a partir de las areas del IFC
    y la densidad de ocupacion segun la tipologia detectada.

    Ocupacion por espacio = ceil(Area / Densidad)

    Args:
        ifc_path: Ruta al archivo IFC.
        tipologia: Tipologia CTE detectada.
        reglas: Dict de reglas aplicables (de obtener_reglas).

    Returns:
        dict con ocupacion total, por planta, por espacio, y altura de evacuacion.
    """
    model = ifcopenshell.open(ifc_path)

    # Densidad por defecto segun tipologia
    densidad_defecto = reglas.get("densidad_ocupacion_m2_persona", 20)

    # Mapeo de categorias de espacio a densidades especificas (Tabla 2.1)
    densidad_por_categoria = {
        # Cualquiera
        "stairway": None,           # Ocupacion nula (circulacion)
        "stair": None,
        "service distribution": None,
        "other general facility": None,
        "roof": None,
        # Residencial Vivienda
        "living spaces": densidad_defecto,
        "general residential space": densidad_defecto,
        "bedroom": densidad_defecto,
        "kitchen": densidad_defecto,
        "bathroom": 3,              # Aseos de planta = 3 m2/persona
        "utility": None,            # Ocupacion nula (mantenimiento)
        # Administrativo
        "office": 10,
        # Docente
        "classroom": 1.5,
        "laboratory": 5,
        "library": 2,
        # Hospitalario
        "waiting room": 2,
        "ward": 15,
        "diagnosis": 10,
        "treatment": 20,
        # Comercial
        "retail": 2,
        "sales": 2,
        # Publica Concurrencia
        "restaurant": 1.5,
        "bar": 1,
        "museum": 2,
        "auditorium": 0.5,
    }

    # Organizar espacios por planta
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

        # Buscar espacios que pertenecen a esta planta
        for rel in model.by_type("IfcRelAggregates"):
            if rel.RelatingObject == storey:
                for obj in rel.RelatedObjects:
                    if obj.is_a("IfcSpace"):
                        area = obtener_area_espacio(model, obj)
                        nombre_espacio = obj.LongName or obj.Name or ""

                        # Obtener categoria del espacio
                        categoria = ""
                        for pset in element_util.get_psets(obj).values():
                            if "Category Description" in pset:
                                categoria = pset["Category Description"]

                        # Determinar densidad segun categoria
                        densidad = densidad_defecto
                        cat_lower = categoria.lower()
                        for cat_key, dens in densidad_por_categoria.items():
                            if cat_key in cat_lower:
                                densidad = dens
                                break

                        # Calcular ocupacion del espacio
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

    # Calcular altura de evacuacion (diferencia entre planta mas alta ocupada y salida)
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
    Evalua si el edificio cumple las reglas de evacuacion del DB-SI
    basandose en la ocupacion calculada y las reglas de la tipologia.

    Args:
        ocupacion: dict resultado de calcular_ocupacion.
        reglas: dict resultado de obtener_reglas.

    Returns:
        lista de dicts con cada verificacion y su resultado (CUMPLE/NO CUMPLE/INFO).
    """
    verificaciones = []
    ocup_total = ocupacion["ocupacion_total"]
    h_desc = ocupacion["altura_evacuacion_descendente_m"]
    h_asc = ocupacion["altura_evacuacion_ascendente_m"]
    tipologia = reglas.get("tipologia", "")

    # 1. Numero minimo de salidas por ocupacion
    max_1_salida = reglas.get("ocupacion_max_1_salida", 100)
    if max_1_salida is not None:
        cumple = ocup_total <= max_1_salida
        verificaciones.append({
            "regla": f"Ocupacion max. con 1 salida ({max_1_salida} pers.)",
            "valor_edificio": f"{ocup_total} personas",
            "limite": f"{max_1_salida} personas",
            "resultado": "CUMPLE" if cumple else "NO CUMPLE - Se requieren mas salidas",
        })

    # 2. Altura de evacuacion descendente (1 salida)
    h_max = reglas.get("altura_evacuacion_desc_max_1_salida_m")
    if h_max is not None:
        cumple = h_desc <= h_max
        verificaciones.append({
            "regla": f"Altura evacuacion descendente max. con 1 salida ({h_max} m)",
            "valor_edificio": f"{h_desc} m",
            "limite": f"{h_max} m",
            "resultado": "CUMPLE" if cumple else "NO CUMPLE - Se requieren mas salidas",
        })

    # 3. Altura de evacuacion ascendente (1 salida)
    if h_asc > 0:
        h_max_asc = reglas.get("altura_evacuacion_asc_max_1_salida_m", 10)
        cumple = h_asc <= h_max_asc
        verificaciones.append({
            "regla": f"Altura evacuacion ascendente max. con 1 salida ({h_max_asc} m)",
            "valor_edificio": f"{h_asc} m",
            "limite": f"{h_max_asc} m",
            "resultado": "CUMPLE" if cumple else "NO CUMPLE",
        })

    # 4. Proteccion de escaleras descendentes
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

    # 5. Proteccion de escaleras ascendentes
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

    # 6. Puertas en sentido de evacuacion
    limite_puertas = reglas.get("puertas_sentido_evacuacion_personas", 100)
    necesita = ocup_total > limite_puertas
    verificaciones.append({
        "regla": f"Puertas abren en sentido evacuacion (>{limite_puertas} pers.)",
        "valor_edificio": f"{ocup_total} personas",
        "limite": f"{limite_puertas} personas",
        "resultado": "REQUERIDO" if necesita else "No requerido",
    })

    # 7. Dimensionado minimo de puertas (A >= P/200 >= 0.80 m)
    anchura_min_puerta = max(ocup_total / 200, 0.80)
    verificaciones.append({
        "regla": "Anchura minima puertas de evacuacion",
        "valor_edificio": f"P={ocup_total} personas",
        "limite": f"A >= P/200 >= 0.80 m",
        "resultado": f"Anchura minima requerida: {anchura_min_puerta:.2f} m",
    })

    # 8. Dimensionado minimo de pasillos (A >= P/200 >= 1.00 m)
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

    # 9. Dimensionado escaleras no protegidas descendentes (A >= P/160)
    if h_desc > 0:
        anchura_min_esc = ocup_total / 160
        verificaciones.append({
            "regla": "Anchura minima escalera no protegida (descendente)",
            "valor_edificio": f"P={ocup_total} personas",
            "limite": "A >= P/160",
            "resultado": f"Anchura minima requerida: {anchura_min_esc:.2f} m",
        })

    # 10. Dimensionado escaleras no protegidas ascendentes (A >= P/(160-10h))
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

    # 11. Control de humo
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

    # 12. Zonas de refugio discapacidad
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


def imprimir_ocupacion(ocupacion):
    """Imprime el calculo de ocupacion de forma legible."""
    print(f"\n{'='*60}")
    print(f"  CALCULO DE OCUPACION - DB-SI Seccion 3, Tabla 2.1")
    print(f"{'='*60}")
    print(f"  Superficie total: {ocupacion['superficie_total_m2']} m2")
    print(f"  Ocupacion total: {ocupacion['ocupacion_total']} personas")
    print(f"  Altura evacuacion descendente: {ocupacion['altura_evacuacion_descendente_m']} m")
    print(f"  Altura evacuacion ascendente: {ocupacion['altura_evacuacion_ascendente_m']} m")

    print(f"\n  --- Ocupacion por planta ---")
    for nombre, datos in ocupacion["ocupacion_por_planta"].items():
        print(f"    {nombre:<20} elev: {datos['elevacion_m']:>6.2f}m | "
              f"sup: {datos['superficie_m2']:>8.2f} m2 | "
              f"ocup: {datos['ocupantes']:>3} pers.")

    print(f"\n  --- Detalle por espacio ---")
    for esp in ocupacion["espacios"]:
        dens_str = f"{esp['densidad_m2_persona']}" if esp['densidad_m2_persona'] else "nula"
        print(f"    {esp['planta']:<12} {esp['espacio']:<18} {esp['categoria']:<25} "
              f"{esp['area_m2']:>7.2f} m2 / {dens_str:<5} = {esp['ocupantes']:>2} pers.")

    print(f"{'='*60}")


def imprimir_cumplimiento(verificaciones):
    """Imprime los resultados de la evaluacion de cumplimiento."""
    print(f"\n{'='*60}")
    print(f"  EVALUACION DE CUMPLIMIENTO - DB-SI Seccion 3")
    print(f"{'='*60}")

    for v in verificaciones:
        # Determinar indicador visual
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
        print(f"      Edificio: {v['valor_edificio']}")
        print(f"      Limite:   {v['limite']}")
        print(f"      >>> {res}")

    print(f"\n{'='*60}")
    print(f"  Leyenda: [OK]=Cumple  [X]=No cumple  [!]=Requerido  [i]=Info")
    print(f"{'='*60}")


# ==========================================================
#  CONFIGURACION: Pon aqui las rutas de tus archivos
# ==========================================================

# Ruta al archivo IFC del edificio a analizar
RUTA_IFC = r"C:\Users\usuario\Documents\GitHub\automatic-fire-compliance-checker\data_PDF_LUISA\01_Duplex_Apartment.ifc"

# Ruta al PDF del DB-SI (por ahora solo informativo, las reglas estan en el codigo)
RUTA_PDF = r"C:\Users\usuario\Documents\GitHub\automatic-fire-compliance-checker\data\DBSI-22-31.pdf"

# ==========================================================


# --- Ejecución directa ---
if __name__ == "__main__":
    resultado = detectar_tipologia(RUTA_IFC)

    print(f"\n{'='*60}")
    print(f"  DETECCION DE TIPOLOGIA CTE DB-SI")
    print(f"{'='*60}")
    print(f"  Archivo: {RUTA_IFC}")
    print(f"  Edificio: {resultado['edificio'].get('nombre', 'Sin nombre')}")
    print(f"  Categoria IFC: {resultado['edificio'].get('categoria', 'No disponible')}")
    print(f"  Plantas: {resultado['edificio'].get('num_plantas', 0)}")
    print(f"  Espacios analizados: {resultado['num_espacios']}")
    print(f"{'='*60}")
    print(f"  TIPOLOGIA DETECTADA: {resultado['tipologia']}")
    print(f"  Confianza: {resultado['confianza']*100:.0f}%")
    print(f"{'='*60}")

    if resultado["puntuaciones"]:
        print(f"\n  Desglose de puntuaciones:")
        for tip, pts in resultado["puntuaciones"].items():
            barra = "#" * pts
            print(f"    {tip:<25} {pts:>3} {barra}")

    # Obtener y mostrar las reglas aplicables
    reglas = obtener_reglas(resultado["tipologia"])
    imprimir_reglas(reglas)

    # Calcular ocupacion real del edificio
    ocupacion = calcular_ocupacion(RUTA_IFC, resultado["tipologia"], reglas)
    imprimir_ocupacion(ocupacion)

    # Evaluar cumplimiento de reglas
    verificaciones = evaluar_cumplimiento(ocupacion, reglas)
    imprimir_cumplimiento(verificaciones)
