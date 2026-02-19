# -*- coding: utf-8 -*-
"""
EVAC ROUTES (GRID) - Versión robusta (con compliance CTE DB-SI SI3.3):
- Nodos del grafo global = DOORS (door_id)
- Aristas: entre puertas del mismo IfcSpace con peso = distancia caminable (Dijkstra grid)
- Seeds: puertas exteriores (exit doors) con coste 0
- Conectividad vertical: añade aristas por "level bridge" (si IfcStair no sirve)
- Para cada space: Dijkstra grid multi-source con seeds=puertas del space con base_cost=dist_door_to_exit[door_id]

+ COMPLIANCE:
- Lee regulation_rules.json (generado por tu pipeline)
- Evalúa SI3.3 "longitud máxima de recorridos" (aprox):
    * si hay 1 salida => compara con max_route_single_exit_m (general o tipología)
    * si hay >=2 salidas => compara con max_route_multiple_exits_m (general)
    * opcional: +25% si HAS_AUTO_EXTINCTION=True
"""

import math
import heapq
import json
import os
from collections import defaultdict, Counter, deque

import numpy as np
import ifcopenshell
import ifcopenshell.util.element as element_util
import ifcopenshell.util.placement as placement_util
import ifcopenshell.geom


# =========================
# CONFIG
# =========================
IFC_PATH = r"C:\Users\usuario\Documents\GitHub\automatic-fire-compliance-checker\data_PDF_LUISA\01_Duplex_Apartment.ifc"

RULES_JSON = r"C:\Users\usuario\Documents\GitHub\automatic-fire-compliance-checker\data_PDF_LUISA\generated\regulation_rules.json"

# Si ya sabes la tipología (debe coincidir con las keys del JSON):
# "Residencial Vivienda", "Residencial Público", "Administrativo", "Docente", ...
TYPOLOGY_OVERRIDE = "Residencial Vivienda"

# CTE: recorridos pueden incrementarse un 25% con extinción automática (si aplica)
HAS_AUTO_EXTINCTION = False

GRID_RES = 0.20
ALLOW_DIAGONALS = True
FLOOR_TRI_Z_TOL = 0.02
HORIZONTAL_NORMAL_TOL = 0.20

SNAP_MAX_RADIUS_CELLS = 10
SNAP_FALLBACK_RADIUS_CELLS = 25

# Escalera (proxy): radio y coste vertical aproximado
STAIR_MAX_XY_DIST_M = 3.5  # (no usado en level bridge actual, se deja por compat)
STAIR_COST_PER_M_VERTICAL = 1.4


# =========================
# Helpers
# =========================
def point_in_polygon(x, y, poly):
    inside = False
    n = len(poly)
    if n < 3:
        return False
    x0, y0 = poly[0]
    for i in range(1, n + 1):
        x1, y1 = poly[i % n]
        if ((y0 > y) != (y1 > y)):
            xinters = (x1 - x0) * (y - y0) / (y1 - y0 + 1e-12) + x0
            if x < xinters:
                inside = not inside
        x0, y0 = x1, y1
    return inside


def snap_point_to_poly_boundary(p, poly):
    x, y = p
    if point_in_polygon(x, y, poly):
        return (x, y)

    best = None
    best_d2 = float("inf")
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        vx, vy = bx - ax, by - ay
        wx, wy = x - ax, y - ay
        vv = vx * vx + vy * vy + 1e-12
        t = (wx * vx + wy * vy) / vv
        t = max(0.0, min(1.0, t))
        px = ax + t * vx
        py = ay + t * vy
        d2 = (px - x) ** 2 + (py - y) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best = (px, py)
    return best if best else (x, y)


def pset_get(entity, key):
    psets = element_util.get_psets(entity)
    for _, pset in psets.items():
        if isinstance(pset, dict) and key in pset:
            return pset[key]
    return None


def is_exit_door(door):
    is_ext = pset_get(door, "IsExternal")
    if is_ext is True:
        return True
    fn = pset_get(door, "Function")
    if isinstance(fn, int) and fn == 1:
        return True
    return False


def world_xyz_from_object_placement(obj):
    try:
        m = placement_util.get_local_placement(obj.ObjectPlacement)
        return (float(m[0, 3]), float(m[1, 3]), float(m[2, 3]))
    except Exception:
        return None


# =========================
# Compliance helpers (CTE DB-SI SI3.3)
# =========================
def load_regulation_rules(rules_path):
    if not os.path.exists(rules_path):
        raise FileNotFoundError(f"Rules JSON not found: {rules_path}")
    with open(rules_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_applicable_route_limits(all_rules, typology):
    """
    Devuelve límites relevantes (m):
      - single_exit_limit_m: max_route_single_exit_m (general o tipología)
      - multiple_exits_limit_m: max_route_multiple_exits_m (general)
      - dead_end_limit_m: dead_end_max_m (general)
    """
    general = dict(all_rules.get("general", {}))
    by_typ = all_rules.get("by_typology", {}).get(typology, {}) if typology else {}

    merged = dict(general)
    merged.update(by_typ)  # tipología puede sobreescribir single-exit, etc.

    single_exit_limit = merged.get("max_route_single_exit_m", 25)
    multiple_exits_limit = merged.get("max_route_multiple_exits_m", 50)
    dead_end_limit = merged.get("dead_end_max_m", 25)

    notes = []
    if typology and by_typ:
        notes.append(f"Using typology overrides: {typology}")
    else:
        notes.append("Using general limits only (no typology override).")

    return {
        "single_exit_limit_m": float(single_exit_limit),
        "multiple_exits_limit_m": float(multiple_exits_limit),
        "dead_end_limit_m": float(dead_end_limit),
        "notes": notes,
    }


def compliance_check_evacuation(per_space_data, spaces_by_id, n_exit_doors,
                                all_rules, typology="", has_auto_extinction=False):
    """
    Check simplificado SI3.3 (longitud máxima de recorrido).
    Returns list[dict] — one dict per IfcSpace, contract-compliant.

    per_space_data: list of (worst_distance, space_name, space_global_id)
                    or None entries for spaces that could not be computed.
    spaces_by_id:   dict mapping GlobalId -> IfcSpace entity.
    """
    limits = get_applicable_route_limits(all_rules, typology)

    if n_exit_doors <= 1:
        limit = limits["single_exit_limit_m"]
        rule_label = "SI3.3 single exit"
    else:
        limit = limits["multiple_exits_limit_m"]
        rule_label = "SI3.3 multiple exits"

    bonus = 1.25 if has_auto_extinction else 1.0
    effective_limit = limit * bonus

    results = []

    # Spaces that produced a worst distance
    computed_ids = set()
    for worst_dist, sp_name, sid in per_space_data:
        computed_ids.add(sid)
        sp = spaces_by_id.get(sid)
        long_name = (sp.LongName if sp and sp.LongName else sp_name) or sp_name
        storey = pset_get(sp, "Level") if sp else None
        name_long = f"{long_name} ({storey})" if storey else long_name

        dist_rounded = round(worst_dist, 2)
        passed = worst_dist <= effective_limit

        if passed:
            comment = None
        else:
            over = round(worst_dist - effective_limit, 2)
            comment = (f"Evacuation route exceeds limit by {over} m "
                       f"({dist_rounded} m vs {effective_limit:.1f} m)")

        results.append({
            "element_id":       sid,
            "element_type":     "IfcSpace",
            "element_name":     sp_name,
            "element_name_long": name_long,
            "check_status":     "pass" if passed else "fail",
            "actual_value":     f"{dist_rounded} m",
            "required_value":   f"{effective_limit:.1f} m ({rule_label})",
            "comment":          comment,
            "log":              None,
        })

    # Spaces that could not be computed (no grid, no doors, unreachable)
    for sid, sp in spaces_by_id.items():
        if sid in computed_ids:
            continue
        sp_name = sp.Name or f"Space #{sp.id()}"
        long_name = sp.LongName or sp_name
        storey = pset_get(sp, "Level")
        name_long = f"{long_name} ({storey})" if storey else long_name

        results.append({
            "element_id":       sid,
            "element_type":     "IfcSpace",
            "element_name":     sp_name,
            "element_name_long": name_long,
            "check_status":     "blocked",
            "actual_value":     None,
            "required_value":   f"{effective_limit:.1f} m ({rule_label})",
            "comment":          "Could not compute evacuation distance "
                                "(no mesh, no doors, or doors unreachable from exit)",
            "log":              None,
        })

    return results


# =========================
# Geometry
# =========================
def get_shape_mesh(ifc_entity):
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    shape = ifcopenshell.geom.create_shape(settings, ifc_entity)
    geom = shape.geometry
    verts = np.array(geom.verts, dtype=float).reshape(-1, 3)
    faces = np.array(geom.faces, dtype=int).reshape(-1, 3)
    return verts, faces


def tri_normal(v0, v1, v2):
    n = np.cross(v1 - v0, v2 - v0)
    norm = np.linalg.norm(n)
    if norm < 1e-12:
        return np.array([0.0, 0.0, 0.0])
    return n / norm


def footprint_from_space_mesh(verts, faces, z_tol=FLOOR_TRI_Z_TOL, horiz_tol=HORIZONTAL_NORMAL_TOL):
    zmin = float(np.min(verts[:, 2]))
    floor_tris = []
    for f in faces:
        v0, v1, v2 = verts[f[0]], verts[f[1]], verts[f[2]]
        n = tri_normal(v0, v1, v2)
        if abs(abs(n[2]) - 1.0) > horiz_tol:
            continue
        zavg = float((v0[2] + v1[2] + v2[2]) / 3.0)
        if abs(zavg - zmin) > z_tol:
            continue
        floor_tris.append((f[0], f[1], f[2]))

    if not floor_tris:
        return None

    edge_count = Counter()
    for a, b, c in floor_tris:
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            edge_count[key] += 1

    boundary_edges = [e for e, cnt in edge_count.items() if cnt == 1]
    if len(boundary_edges) < 3:
        return None

    adj = defaultdict(list)
    for u, v in boundary_edges:
        adj[u].append(v)
        adj[v].append(u)

    start = boundary_edges[0][0]
    loop = [start]
    prev = None
    cur = start
    for _ in range(len(boundary_edges) + 20):
        neigh = adj[cur]
        if not neigh:
            break
        if prev is None:
            nxt = neigh[0]
        else:
            nxt = neigh[0] if (len(neigh) == 1 or neigh[1] == prev) else neigh[1]
        if nxt == start:
            break
        loop.append(nxt)
        prev, cur = cur, nxt

    poly = [(float(verts[i, 0]), float(verts[i, 1])) for i in loop]
    return poly if len(poly) >= 3 else None


# =========================
# Grid
# =========================
def rasterize_polygon(poly, res=GRID_RES):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    pad = res * 1.0
    minx -= pad
    miny -= pad
    maxx += pad
    maxy += pad

    w = int(math.ceil((maxx - minx) / res))
    h = int(math.ceil((maxy - miny) / res))
    grid = np.zeros((h, w), dtype=bool)

    for iy in range(h):
        cy = miny + (iy + 0.5) * res
        for ix in range(w):
            cx = minx + (ix + 0.5) * res
            if point_in_polygon(cx, cy, poly):
                grid[iy, ix] = True

    return grid, (minx, miny), res


def world_to_cell(pxy, origin, res):
    x, y = pxy
    ox, oy = origin
    ix = int((x - ox) / res)
    iy = int((y - oy) / res)
    return (iy, ix)


def neighbors(iy, ix, grid, diagonals=True):
    h, w = grid.shape
    steps = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    if diagonals:
        steps += [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    for dy, dx in steps:
        ny, nx = iy + dy, ix + dx
        if 0 <= ny < h and 0 <= nx < w and grid[ny, nx]:
            cost = math.sqrt(2) if (dy != 0 and dx != 0) else 1.0
            yield ny, nx, cost


def snap_cell_to_walkable(grid, cell, max_radius_cells):
    h, w = grid.shape
    iy, ix = cell
    if 0 <= iy < h and 0 <= ix < w and grid[iy, ix]:
        return (iy, ix)

    q = deque()
    seen = set()
    q.append((iy, ix, 0))
    seen.add((iy, ix))

    while q:
        y, x, d = q.popleft()
        if d > max_radius_cells:
            continue
        if 0 <= y < h and 0 <= x < w and grid[y, x]:
            return (y, x)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            ny, nx = y + dy, x + dx
            if (ny, nx) not in seen:
                seen.add((ny, nx))
                q.append((ny, nx, d + 1))
    return None


def dijkstra_grid_from_source(grid, res, source_cell, diagonals=True):
    h, w = grid.shape
    dist = np.full((h, w), float("inf"), dtype=float)
    sy, sx = source_cell
    if not (0 <= sy < h and 0 <= sx < w and grid[sy, sx]):
        return dist

    dist[sy, sx] = 0.0
    pq = [(0.0, sy, sx)]
    while pq:
        d, y, x = heapq.heappop(pq)
        if d != dist[y, x]:
            continue
        for ny, nx, step in neighbors(y, x, grid, diagonals):
            nd = d + step * res
            if nd < dist[ny, nx]:
                dist[ny, nx] = nd
                heapq.heappush(pq, (nd, ny, nx))
    return dist


def grid_multisource_dijkstra(grid, res, seeds, diagonals=True):
    h, w = grid.shape
    dist = np.full((h, w), float("inf"), dtype=float)
    pq = []

    for (iy, ix, c0) in seeds:
        if 0 <= iy < h and 0 <= ix < w and grid[iy, ix]:
            if c0 < dist[iy, ix]:
                dist[iy, ix] = c0
                heapq.heappush(pq, (c0, iy, ix))

    while pq:
        d, y, x = heapq.heappop(pq)
        if d != dist[y, x]:
            continue
        for ny, nx, step in neighbors(y, x, grid, diagonals):
            nd = d + step * res
            if nd < dist[ny, nx]:
                dist[ny, nx] = nd
                heapq.heappush(pq, (nd, ny, nx))
    return dist


# =========================
# Conectividad space->doors (robusta)
# =========================
def build_space_door_maps_enhanced(model):
    door_to_spaces = defaultdict(set)
    space_to_doors = defaultdict(set)

    # direct door boundaries
    for rel in model.by_type("IfcRelSpaceBoundary"):
        sp = rel.RelatingSpace
        el = rel.RelatedBuildingElement
        if not sp or not el:
            continue
        if el.is_a("IfcDoor"):
            door_to_spaces[el.GlobalId].add(sp.GlobalId)
            space_to_doors[sp.GlobalId].add(el.GlobalId)

    # wall -> spaces
    wall_to_spaces = defaultdict(set)
    for rel in model.by_type("IfcRelSpaceBoundary"):
        sp = rel.RelatingSpace
        el = rel.RelatedBuildingElement
        if not sp or not el:
            continue
        if el.is_a("IfcWall") or el.is_a("IfcWallStandardCase"):
            wall_to_spaces[el.GlobalId].add(sp.GlobalId)

    # opening -> wall
    opening_to_wall = {}
    for rv in model.by_type("IfcRelVoidsElement"):
        opening = rv.RelatedOpeningElement
        wall = rv.RelatingBuildingElement
        if opening and wall:
            opening_to_wall[opening.GlobalId] = wall.GlobalId

    # door -> opening -> wall -> spaces
    for rf in model.by_type("IfcRelFillsElement"):
        door = rf.RelatedBuildingElement
        opening = rf.RelatingOpeningElement
        if not door or not opening:
            continue
        if not door.is_a("IfcDoor"):
            continue
        wall_id = opening_to_wall.get(opening.GlobalId)
        if not wall_id:
            continue
        for sid in wall_to_spaces.get(wall_id, set()):
            door_to_spaces[door.GlobalId].add(sid)
            space_to_doors[sid].add(door.GlobalId)

    return door_to_spaces, space_to_doors


def build_door_cells(model, space_polys, space_grids, space_to_doors):
    doors = model.by_type("IfcDoor")
    door_by_id = {d.GlobalId: d for d in doors}

    portal_cells = {}      # (space_id, door_id) -> cell
    door_any_cell = {}     # door_id -> (space_id, cell)
    door_xyz = {}          # door_id -> (x,y,z)
    door_level = {}        # door_id -> str(level)

    for d in doors:
        p = world_xyz_from_object_placement(d)
        if p is not None:
            door_xyz[d.GlobalId] = p
        lvl = pset_get(d, "Level")
        door_level[d.GlobalId] = str(lvl) if lvl is not None else ""

    for sid, door_ids in space_to_doors.items():
        poly = space_polys.get(sid)
        grid_info = space_grids.get(sid)
        if poly is None or grid_info is None:
            continue
        grid, origin, res = grid_info

        for did in door_ids:
            d = door_by_id.get(did)
            if d is None:
                continue
            p3 = door_xyz.get(did)
            if p3 is None:
                continue
            p2 = snap_point_to_poly_boundary((p3[0], p3[1]), poly)
            cell = world_to_cell(p2, origin, res)

            cell2 = snap_cell_to_walkable(grid, cell, SNAP_MAX_RADIUS_CELLS)
            if cell2 is None:
                cell2 = snap_cell_to_walkable(grid, cell, SNAP_FALLBACK_RADIUS_CELLS)
            if cell2 is None:
                continue

            portal_cells[(sid, did)] = cell2
            if did not in door_any_cell:
                door_any_cell[did] = (sid, cell2)

    return portal_cells, door_any_cell, door_xyz, door_level


# =========================
# Grafo global: nodos=DOORS
# =========================
def build_door_graph(space_grids, portal_cells, space_to_doors):
    graph = defaultdict(list)

    for sid, door_ids in space_to_doors.items():
        grid_info = space_grids.get(sid)
        if not grid_info:
            continue
        grid, origin, res = grid_info

        ds = [did for did in door_ids if (sid, did) in portal_cells]
        if len(ds) < 2:
            continue

        door_cells = {did: portal_cells[(sid, did)] for did in ds}

        for i, dsrc in enumerate(ds):
            distmap = dijkstra_grid_from_source(grid, res, door_cells[dsrc], diagonals=ALLOW_DIAGONALS)
            for j in range(i + 1, len(ds)):
                dtgt = ds[j]
                cy, cx = door_cells[dtgt]
                w = float(distmap[cy, cx])
                if math.isfinite(w):
                    graph[dsrc].append((dtgt, w))
                    graph[dtgt].append((dsrc, w))

    return graph


def add_level_bridge_edges(model, door_graph, doors, door_xyz, door_level,
                           cost_per_meter_vertical=1.4,
                           horizontal_penalty=0.2):
    """
    Puente vertical robusto cuando IfcStair placement no sirve:
    - Crea edges entre puertas de Level 2 y puertas 'candidatas' de Level 1 (circulación).
    - Si no detecta circulación, fallback: conecta Level2 -> todas las de Level1.
    Peso = dz*cost_per_meter_vertical + horizontal_penalty*dxy
    """

    # Elevaciones de storeys por nombre
    storey_elev = {}
    for st in model.by_type("IfcBuildingStorey"):
        storey_elev[st.Name or ""] = float(st.Elevation or 0.0)

    # Clasificar puertas por nivel
    level_to_doors = defaultdict(list)
    for d in doors:
        did = d.GlobalId
        lvl = door_level.get(did, "")
        level_to_doors[lvl].append(did)

    levels = sorted(level_to_doors.keys())
    if len(levels) < 2:
        print("[WARN] Only one Level detected -> no level bridges added")
        return 0

    def is_circulation_door(door_obj):
        name = (door_obj.Name or "").lower()
        mark = str(pset_get(door_obj, "Mark") or "").lower()
        kws = ["corridor", "hall", "lobby", "stair", "pasillo", "distrib", "circulation"]
        return any(k in name for k in kws) or any(k in mark for k in kws)

    door_by_id = {d.GlobalId: d for d in doors}

    # elegir base/upper por elevaciones si están disponibles
    lvl_elev = {lvl_name: storey_elev[lvl_name] for lvl_name in levels if lvl_name in storey_elev}

    if len(lvl_elev) >= 2:
        ordered = sorted(lvl_elev.items(), key=lambda kv: kv[1])
        base_lvl = ordered[0][0]
        upper_lvl = ordered[-1][0]
    else:
        base_lvl = "Level 1" if "Level 1" in level_to_doors else levels[0]
        upper_lvl = "Level 2" if "Level 2" in level_to_doors else levels[-1]

    base_doors = level_to_doors.get(base_lvl, [])
    upper_doors = level_to_doors.get(upper_lvl, [])

    if not base_doors or not upper_doors:
        print("[WARN] Missing doors for base/upper levels -> no level bridges added")
        return 0

    base_cands = [did for did in base_doors if is_circulation_door(door_by_id[did])]
    if not base_cands:
        base_cands = base_doors

    K = min(4, len(base_cands))

    added = 0
    for u in upper_doors:
        pu = door_xyz.get(u)
        if pu is None:
            continue

        dists = []
        for v in base_cands:
            pv = door_xyz.get(v)
            if pv is None:
                continue
            dxy = math.hypot(pu[0] - pv[0], pu[1] - pv[1])
            dz = abs(pu[2] - pv[2])
            w = dz * cost_per_meter_vertical + horizontal_penalty * dxy
            dists.append((w, v))
        dists.sort(key=lambda t: t[0])

        for w, v in dists[:K]:
            door_graph[u].append((v, w))
            door_graph[v].append((u, w))
            added += 1

    return added


def dijkstra_doors_to_exit(graph, exit_door_ids):
    dist = {}
    pq = []
    for did in exit_door_ids:
        dist[did] = 0.0
        heapq.heappush(pq, (0.0, did))

    while pq:
        d, u = heapq.heappop(pq)
        if d != dist.get(u, float("inf")):
            continue
        for v, w in graph.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return dist


# =========================
# MAIN
# =========================
def main():
    model = ifcopenshell.open(IFC_PATH)
    spaces = model.by_type("IfcSpace")
    doors = model.by_type("IfcDoor")

    print(f"Spaces: {len(spaces)} | Doors: {len(doors)}")

    door_to_spaces, space_to_doors = build_space_door_maps_enhanced(model)
    print(f"Doors adjacency (by inference): {len(door_to_spaces)} doors mapped to spaces")

    # grids
    space_polys = {}
    space_grids = {}
    for sp in spaces:
        sid = sp.GlobalId
        try:
            verts, faces = get_shape_mesh(sp)
        except Exception as e:
            print(f"[WARN] No mesh for space {sp.Name} ({sid}): {e}")
            space_polys[sid] = None
            continue

        poly = footprint_from_space_mesh(verts, faces)
        space_polys[sid] = poly
        if poly is None:
            print(f"[WARN] No footprint for space {sp.Name} ({sid})")
            continue

        grid, origin, res = rasterize_polygon(poly, GRID_RES)
        space_grids[sid] = (grid, origin, res)

    print(f"Spaces with grid: {len(space_grids)}/{len(spaces)}")

    # exits
    exit_door_ids = {d.GlobalId for d in doors if is_exit_door(d)}
    print(f"Exit doors detected: {len(exit_door_ids)}")

    # door cells
    portal_cells, door_any_cell, door_xyz, door_level = build_door_cells(
        model, space_polys, space_grids, space_to_doors
    )
    print(f"Portals with walkable cells (space-door): {len(portal_cells)}")
    print(f"Doors with at least one walkable placement: {len(door_any_cell)}/{len(doors)}")

    # door graph
    door_graph = build_door_graph(space_grids, portal_cells, space_to_doors)
    print(f"Door graph nodes: {len(door_graph)} (doors with edges)")

    # add vertical edges (LEVEL BRIDGE)
    added_vert = add_level_bridge_edges(
        model, door_graph, doors, door_xyz, door_level,
        cost_per_meter_vertical=STAIR_COST_PER_M_VERTICAL,
        horizontal_penalty=0.2
    )
    print(f"Added vertical (level-bridge) edges: {added_vert}")

    # distances door->exit
    dist_door_to_exit = dijkstra_doors_to_exit(door_graph, exit_door_ids)

    # DEBUG unreachable doors
    door_by_id = {d.GlobalId: d for d in doors}
    unreached = [did for did in door_by_id.keys() if did not in dist_door_to_exit]
    print("\nDoors unreachable from any exit:", len(unreached))
    for did in unreached:
        d = door_by_id[did]
        lvl = pset_get(d, "Level")
        print(f"  - {d.Name} | {did} | Level={lvl}")

    # per space worst
    per_space_max = []
    warn_count = 0

    for sp in spaces:
        sid = sp.GlobalId
        if sid not in space_grids:
            continue
        grid, origin, res = space_grids[sid]

        door_ids = list(space_to_doors.get(sid, []))
        seeds = []
        for did in door_ids:
            base = dist_door_to_exit.get(did)
            cell = portal_cells.get((sid, did))
            if base is None or cell is None:
                continue
            seeds.append((cell[0], cell[1], float(base)))

        if not seeds:
            warn_count += 1
            print(f"[WARN] Space {sp.Name} ({sid}) has no seeded doors to an exit.")
            continue

        dist_cells = grid_multisource_dijkstra(grid, res, seeds, diagonals=ALLOW_DIAGONALS)
        walk = dist_cells[np.isfinite(dist_cells)]
        if walk.size == 0:
            continue
        worst = float(np.max(walk))
        per_space_max.append((worst, sp.Name or "", sid))
        print(f"Space {sp.Name:>10} | worst evac dist ≈ {worst:6.2f} m")

    if not per_space_max:
        print("[ERROR] No spaces produced a worst distance.")
        return

    per_space_max.sort(reverse=True, key=lambda x: x[0])
    worst_building = per_space_max[0]

    print("\n" + "=" * 60)
    print(f"MAX EVAC ROUTE (approx, grid): {worst_building[0]:.2f} m")
    print(f"  Space: {worst_building[1]} | id: {worst_building[2]}")
    print(f"WARN spaces w/o seeded doors: {warn_count}")
    print("=" * 60)

    print("\nTop 5 worst spaces:")
    for w, name, sid in per_space_max[:5]:
        print(f"  {w:6.2f} m | {name} | {sid}")

    # =========================
    # COMPLIANCE CHECK (CTE DB-SI SI3.3) using regulation_rules.json
    # Returns list[dict] per IfcSpace (IFCore contract format)
    # =========================
    try:
        all_rules = load_regulation_rules(RULES_JSON)
        typology = (TYPOLOGY_OVERRIDE or "").strip()

        spaces_by_id = {sp.GlobalId: sp for sp in spaces}

        results = compliance_check_evacuation(
            per_space_data=per_space_max,
            spaces_by_id=spaces_by_id,
            n_exit_doors=len(exit_door_ids),
            all_rules=all_rules,
            typology=typology,
            has_auto_extinction=HAS_AUTO_EXTINCTION
        )

        print("\n" + "=" * 60)
        print("CTE DB-SI (SI 3) - EVACUATION ROUTE COMPLIANCE (per space)")
        print("-" * 60)

        for r in results:
            status = r["check_status"].upper()
            print(f"[{status:7s}] {r['element_name']}"
                  f"  |  actual: {r['actual_value'] or 'N/A'}"
                  f"  |  required: {r['required_value']}"
                  f"  |  {r['comment'] or ''}")

        # Summary
        n_pass = sum(1 for r in results if r["check_status"] == "pass")
        n_fail = sum(1 for r in results if r["check_status"] == "fail")
        n_blocked = sum(1 for r in results if r["check_status"] == "blocked")
        print("-" * 60)
        print(f"TOTAL: {len(results)} spaces | "
              f"pass: {n_pass} | fail: {n_fail} | blocked: {n_blocked}")
        print("=" * 60)

        # The results list is contract-compliant (list[dict] with IFCore keys)
        return results

    except Exception as e:
        print("\n" + "=" * 60)
        print("CTE DB-SI (SI 3) compliance check skipped (could not load rules).")
        print(f"Reason: {e}")
        print("=" * 60)
        return []


if __name__ == "__main__":
    main()
