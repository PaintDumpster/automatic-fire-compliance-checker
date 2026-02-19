# 📘 What Changed - Simple Explanation

## What You Asked For

You said: *"the list of tool files have been updated, update the api wrappers accordingly"*

## What I Did

I scanned your `tools/` directory (previously called `utils/`) and found 2 new compliance checker files:

1. **`si_5_Firefighter_intervention.py`** - Checks if windows meet firefighter access requirements
2. **`SI_3_Evacuation_of_occupants_max_route.py`** - Advanced evacuation route calculator using pathfinding

I created API wrappers for both, so you can now call them via HTTP requests.

---

## 🆕 New API Endpoints

### 1. SI-5 Firefighter Intervention

**Endpoint:** `POST http://localhost:8000/api/si5/check`

**What it does:**
- Reads your IFC building model
- Finds all windows
- Checks each window against 3 rules:
  1. Is it big enough? (≥0.8m wide × ≥1.2m tall)
  2. Is the sill at the right height? (between -0.05m and 1.20m from floor)
  3. Does it have security bars blocking it? (not allowed if building is >9m tall)

**Input:**
```json
{
  "ifc_path": "/path/to/your/building.ifc"
}
```

**Output:**
```json
{
  "summary_text": "5 out of 8 compliant",
  "total_checked": 8,
  "total_compliant": 5,
  "results": [ /* detailed info for each window */ ]
}
```

**Why this matters for your research:**
In FO-SEZ buildings (food zones), you need emergency access for firefighters. This checks if your design meets that requirement automatically.

---

### 2. SI-3 Advanced Evacuation Routes

**Endpoint:** `POST http://localhost:8000/api/si3/check-max-route`

**What it does:**
- Creates a grid map of walkable space in your building
- Finds all exit doors
- Calculates the longest walking distance from any point to the nearest exit
- Compares that distance to regulatory limits

**Input:**
```json
{
  "ifc_path": "/path/to/your/building.ifc",
  "typology": "Residencial Vivienda",
  "has_auto_extinction": false,
  "rules_json_path": "/path/to/regulation_rules.json"
}
```

**Output:**
```json
{
  "status": "non_compliant",
  "max_route_distance_m": 28.75,
  "total_spaces": 12,
  "compliant_spaces": 10,
  "non_compliant_spaces": 2,
  "results": [ /* detailed info for each room */ ]
}
```

**Why this matters for your research:**
This is the "smart" evacuation checker. Instead of just looking at building parameters, it actually calculates real walking paths. For complex FO-SEZ warehouses or processing facilities, this gives you accurate evacuation times.

**Difference from basic SI-3:**
- **Basic `/api/si3/check`** → Fast, checks general rules (occupancy limits, exit widths)
- **Advanced `/api/si3/check-max-route`** → Slower, calculates actual walking distances using Dijkstra algorithm

---

## 🔄 What Else Changed

### Import Paths Updated

All API routers now use `tools/` instead of `utils/`:

```python
# Before
from utils.SI_3_Evacuation_of_occupants import detectar_tipologia

# Now
from tools.SI_3_Evacuation_of_occupants import detectar_tipologia
```

This matches your new folder structure.

### Files Modified

1. **`app/api/models.py`** - Added data models for SI-5 and SI-3 max route
2. **`app/api/routers/si5_router.py`** - NEW FILE - Wraps firefighter checks
3. **`app/api/routers/si3_router.py`** - UPDATED - Added `/check-max-route` endpoint
4. **`app/api/main.py`** - Registered new SI-5 router
5. **`app/api/routers/__init__.py`** - Added SI-5 to imports

### Files NOT Modified

Your original `tools/` files remain **100% untouched**. The API wrappers only import and call your functions—they never modify them.

---

## 🧪 How to Test

### Start the API Server

```bash
cd /home/salva/iaac/ai_workshop/automatic-fire-compliance-checker
uvicorn app.api.main:app --reload --port 8000
```

### Open Interactive Documentation

Go to: http://localhost:8000/docs

You'll see **all 6 compliance sections** listed:
- SI-1 Interior Propagation
- SI-3 Evacuation (basic + advanced)
- SI-4 Protection Installations
- **SI-5 Firefighter Intervention** ← NEW
- SI-6 Structural Resistance

### Test SI-5 (Firefighter Windows)

1. Click on **SI-5 Firefighter Intervention**
2. Click **POST /api/si5/check**
3. Click **"Try it out"**
4. Enter your IFC file path
5. Click **"Execute"**

You'll get a JSON response showing which windows pass/fail.

### Test SI-3 Max Route

1. Click on **SI-3 Evacuation**
2. Click **POST /api/si3/check-max-route**
3. Click **"Try it out"**
4. Fill in:
   - `ifc_path`: Your IFC file
   - `typology`: Your building type (e.g., "Residencial Vivienda")
   - `has_auto_extinction`: `false` (unless you have sprinklers)
   - `rules_json_path`: Path to your rules JSON (or leave blank for auto-detect)
5. Click **"Execute"**

You'll get evacuation distances for each space.

---

## 📊 How This Connects to Your Research

### Your Thesis Topic
> "Food-Oriented Special Economic Zones (FO-SEZs) using predictive spatial planning"

### How These APIs Help

1. **Automate compliance checking** for multiple FO-SEZ design scenarios
2. **Batch process** hundreds of building layouts to find optimal designs
3. **Integrate with GIS** (QGIS, ArcGIS) to map compliant zones
4. **Generate data** for predictive models (evacuation times, safety metrics)
5. **Export results** to CSV/JSON for statistical analysis

### Example Workflow

```
1. Design 10 different FO-SEZ warehouse layouts (IFC files)
2. Send each to API: /api/si5/check + /api/si3/check-max-route + /api/si6/check
3. Collect compliance results (JSON)
4. Analyze which designs meet all fire safety requirements
5. Visualize compliant designs on a map
6. Use data in your thesis analysis
```

---

## 🗂️ Complete Endpoint List

After this update, you now have:

| Endpoint | What It Checks |
|----------|----------------|
| `/api/si1/scan` | Scans IFC for fire sectors |
| `/api/si1/check` | Sector size compliance |
| `/api/si3/check` | Basic evacuation (fast) |
| `/api/si3/check-max-route` | Advanced evacuation routes (slow, accurate) ← NEW |
| `/api/si4/check` | Fire protection systems |
| `/api/si5/check` | Firefighter access windows ← NEW |
| `/api/si6/check` | Structural fire resistance |

---

## 🔍 File Structure (What Connects to What)

```
tools/                                     app/api/routers/
├── si_5_Firefighter_intervention.py  →   ├── si5_router.py (NEW)
├── SI_3_Evacuation_of_occupants_max_route.py  →  ├── si3_router.py (UPDATED)
├── SI_1_interior_propagation.py      →   ├── si1_router.py
├── sub_si1_checker.py                →   ├── si1_router.py
├── SI_3_Evacuation_of_occupants.py   →   ├── si3_router.py
├── SI_4_installation_of_protection.py →  ├── si4_router.py
└── si_6_fire_resistance_of_the_structure.py → └── si6_router.py
```

**Arrow (→) means:** The router file wraps functions from the tool file

---

## 📋 Mermaid Diagram: Data Flow

```mermaid
graph LR
    A[Your IFC File] --> B[POST /api/si5/check]
    B --> C[si5_router.py]
    C --> D[tools/si_5_Firefighter_intervention.py]
    D --> E[validate_firefighter_access]
    E --> F[Check each window]
    F --> G[Return compliance JSON]
    G --> H[You receive results]
    
    style B fill:#90EE90
    style D fill:#FFE4B5
    style H fill:#87CEEB
```

**Legend:**
- 🟢 Green = API endpoint (what you call)
- 🟡 Yellow = Your original tool file (unchanged)
- 🔵 Blue = Result you receive

---

## ❓ Common Questions

### Q: Did you modify my original tool files?
**A:** No. All your files in `tools/` are untouched. The API wrappers only import and call them.

### Q: What if I add more tool files later?
**A:** Just let me know! I can create new routers following the same pattern.

### Q: Can I use these endpoints from QGIS?
**A:** Yes! Use Python's `requests` library or QGIS Python console to send HTTP requests.

Example:
```python
import requests
response = requests.post("http://localhost:8000/api/si5/check", json={
    "ifc_path": "/path/to/building.ifc"
})
print(response.json())
```

### Q: How do I know if it's working?
**A:** Run `uvicorn app.api.main:app --reload` and go to http://localhost:8000/docs. If you see SI-5 in the list, it's working!

---

## 📚 Additional Documentation

- **[API_WRAPPERS_UPDATE.md](./API_WRAPPERS_UPDATE.md)** - Technical details
- **[API_ARCHITECTURE.md](./API_ARCHITECTURE.md)** - Full architecture explanation
- **[QUICK_START.md](./QUICK_START.md)** - Installation and testing guide

---

## ✅ Summary

**What you have now:**
- ✅ SI-5 firefighter window checks (API endpoint)
- ✅ SI-3 advanced evacuation routes (API endpoint)
- ✅ All routers updated to use `tools/` instead of `utils/`
- ✅ Clear documentation explaining everything
- ✅ No modifications to your original tool files

**Next steps:**
1. Start the API server
2. Open http://localhost:8000/docs
3. Try the new endpoints with your IFC files
4. Use results in your FO-SEZ research

---

**Need help?** Check the error logs or ask me to explain any part in more detail!
