# FastAPI Wrapper Architecture

## What This Is

This is a **REST API** that wraps your fire compliance checking utilities. It allows external tools (web dashboards, GIS software, mobile apps) to check building compliance through HTTP requests.

---

## Why We Created These Files

**PROBLEM:**
Your utility files (`utils/SI_1_interior_propagation.py`, etc.) are Python functions. They can only be used if someone runs Python code directly.

**SOLUTION:**

We created a web API that:

1. **Does NOT modify your utility files**
2. **Wraps them** with FastAPI endpoints
3. **Accepts HTTP requests** from any client
4. **Returns JSON responses** that any language can understand

---

## File Structure

``` MARKDOWN
app/
├── api/
│   ├── __init__.py           # Package marker
│   ├── main.py               # FastAPI app entry point (START HERE)
│   ├── models.py             # Data validation models (requests/responses)
│   └── routers/
│       ├── __init__.py       # Package marker
│       ├── si1_router.py     # Wraps SI-1 utilities
│       ├── si3_router.py     # Wraps SI-3 utilities
│       └── si6_router.py     # Wraps SI-6 utilities
```

---

## How It Works

### Step-by-Step Flow

1. **Client sends HTTP request**

   ``` JSON
   POST http://localhost:8000/api/si6/check
   {
       "ifc_path": "/home/user/building.ifc",
       "building_use": "residential",
       "evacuation_height_m": 18.5,
       "is_basement": false
   }
   ```

2. **FastAPI receives request**
   - Routes to `si6_router.py`
   - Validates data using `SI6CheckRequest` model

3. **Router calls utility function**

   ```python
   # Inside si6_router.py
   from utils.si_6_fire_resistance_of_the_structure import check_si6_compliance
   
   result = check_si6_compliance(
       ifc_path=request.ifc_path,
       building_use=request.building_use,
       ...
   )
   ```

4. **Router formats response**
   - Transforms result into `SI6CheckResponse` model
   - Returns JSON to client

5. **Client receives response**

   ```json
   {
       "status": "non_compliant",
       "total_elements_checked": 145,
       "compliant_elements": 143,
       "non_compliant_elements": 2,
       "non_compliant_details": [...]
   }
   ```

---

## Key Design Principles

### 1. **Separation of Concerns**

- **Utility files** = Business logic (compliance checks)
- **Router files** = API layer (HTTP handling)
- **Model files** = Data validation

### 2. **Zero Modification**

Your utility files are **NEVER** modified. The routers only **import and call** them.

### 3. **Clean Architecture**

``` MARKDOWN
Client (web/mobile/GIS)
    ↓ HTTP Request
FastAPI Router (wrapper)
    ↓ Function Call
Utility File (your code)
    ↓ Return Result
FastAPI Router (format)
    ↓ HTTP Response
Client (receives JSON)
```

---

## What Each File Does

### `app/api/main.py`

**PURPOSE:** Main entry point for the API

**WHAT IT DOES:**

- Creates FastAPI application
- Registers all routers
- Sets up CORS (for web browser access)
- Provides health check endpoint

**KEY CODE:**

```python
app = FastAPI(title="Fire Compliance Checker API")
app.include_router(si1_router.router, prefix="/api/si1")
app.include_router(si3_router.router, prefix="/api/si3")
app.include_router(si6_router.router, prefix="/api/si6")
```

---

### `app/api/models.py`

**PURPOSE:** Data validation templates

**WHAT IT DOES:**

- Defines structure of incoming requests
- Defines structure of outgoing responses
- Provides automatic validation
- Generates API documentation

**EXAMPLE:**

```python
class SI6CheckRequest(BaseModel):
    ifc_path: str                    # Required field
    building_use: str                # Required field
    evacuation_height_m: float       # Required, must be >= 0
    is_basement: bool = False        # Optional, defaults to False
```

If a client sends invalid data (e.g., negative height), FastAPI automatically rejects it.

---

### `app/api/routers/si6_router.py`

**PURPOSE:** Wrapper for SI-6 utilities

**WHAT IT DOES:**

1. Receives HTTP POST request
2. Validates request data
3. Calls `check_si6_compliance()` from your utility file
4. Formats result as JSON
5. Returns HTTP response

**KEY CODE:**

```python
from utils.si_6_fire_resistance_of_the_structure import check_si6_compliance

@router.post("/check")
def check_si6_endpoint(request: SI6CheckRequest):
    # Call your utility function (unchanged)
    result = check_si6_compliance(
        ifc_path=request.ifc_path,
        building_use=request.building_use,
        evacuation_height_m=request.evacuation_height_m,
        is_basement=request.is_basement
    )
    
    # Transform result into API response
    return SI6CheckResponse(**result)
```

---

### `app/api/routers/si1_router.py`

**PURPOSE:** Wrapper for SI-1 utilities

**ENDPOINTS:**

- `POST /api/si1/scan` - Scan IFC for spaces/doors/zones
- `POST /api/si1/check` - Check sector size compliance
- `GET /api/si1/info` - Get endpoint documentation

**WHAT IT WRAPS:**

- `scan_ifc_basic()` from `utils/sub_si1_checker.py`
- `check_sector_size_compliance()` from `utils/sub_si1_checker.py`
- `build_sectors()` from `utils/sub_si1_checker.py`

---

### `app/api/routers/si3_router.py`

**PURPOSE:** Wrapper for SI-3 utilities

**ENDPOINTS:**

- `POST /api/si3/check` - Check evacuation compliance
- `GET /api/si3/info` - Get endpoint documentation

**WHAT IT WRAPS:**

- `detectar_tipologia()` from `utils/SI_3_Evacuation_of_occupants.py`
- `obtener_reglas()` from `utils/SI_3_Evacuation_of_occupants.py`
- `calcular_ocupacion()` from `utils/SI_3_Evacuation_of_occupants.py`
- `evaluar_cumplimiento()` from `utils/SI_3_Evacuation_of_occupants.py`

**WORKFLOW:**

1. Detect building typology
2. Load CTE rules
3. Calculate occupancy
4. Evaluate compliance
5. Return results

---

## How to Run

### 1. Install FastAPI

```bash
pip install fastapi uvicorn
```

### 2. Start the server

```bash
cd /home/salva/iaac/ai_workshop/automatic-fire-compliance-checker
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Open browser

- Swagger UI: <http://localhost:8000/docs>
- ReDoc: <http://localhost:8000/redoc>
- Health check: <http://localhost:8000/>

---

## How to Test

### Using Swagger UI (Browser)

1. Go to <http://localhost:8000/docs>
2. Click on an endpoint (e.g., "POST /api/si6/check")
3. Click "Try it out"
4. Fill in the request body
5. Click "Execute"
6. See the response

### Using curl (Terminal)

```bash
curl -X POST "http://localhost:8000/api/si6/check" \
     -H "Content-Type: application/json" \
     -d '{
           "ifc_path": "/path/to/building.ifc",
           "building_use": "residential",
           "evacuation_height_m": 18.5,
           "is_basement": false
         }'
```

### Using Python

```python
import requests

response = requests.post(
    "http://localhost:8000/api/si6/check",
    json={
        "ifc_path": "/path/to/building.ifc",
        "building_use": "residential",
        "evacuation_height_m": 18.5,
        "is_basement": False
    }
)

result = response.json()
print(result["status"])  # 'compliant' or 'non_compliant'
```

---

## Research Connection

### FO-SEZ Spatial Planning Pipeline

This API enables **automated compliance checking** in the FO-SEZ spatial planning workflow:

```MARKDOWN
QGIS / ArcGIS
    ↓ Generate building footprints
IFC Generation Tool
    ↓ Create 3D building models
Fire Compliance API (this)
    ↓ Check compliance
Decision Support Dashboard
    ↓ Display results
Urban Planner
    ↓ Make decisions
Final Design
```

### Input Data

- **IFC files:** Building Information Models
- **Building parameters:** Use, height, sprinklers

### Output Data

- **Compliance status:** Pass/Fail
- **Specific issues:** List of non-compliant elements
- **Regulatory references:** Which rules were checked

### Research Value

1. **Automation:** No manual checking required
2. **Speed:** Instant compliance feedback
3. **Integration:** Can be called from GIS tools
4. **Scalability:** Check hundreds of buildings quickly
5. **Transparency:** Detailed reporting of all checks

---

## Available Endpoints

### SI-1 Interior Propagation

| Endpoint        | Method | Purpose                          |
| --------------- | ------ | -------------------------------- |
| `/api/si1/scan` | POST   | Scan IFC for spaces/doors/zones  |
| `/api/si1/check`| POST   | Check sector size compliance     |
| `/api/si1/info` | GET    | Get endpoint information         |

### SI-3 Evacuation

| Endpoint        | Method | Purpose                      |
| --------------- | ------ | ---------------------------- |
| `/api/si3/check`| POST   | Check evacuation compliance  |
| `/api/si3/info` | GET    | Get endpoint information     |

### SI-6 Structural Resistance

| Endpoint        | Method | Purpose                           |
| --------------- | ------ | --------------------------------- |
| `/api/si6/check`| POST   | Check structural fire resistance  |
| `/api/si6/info` | GET    | Get endpoint information          |

### General

| Endpoint   | Method | Purpose                                  |
| ---------- | ------ | ---------------------------------------- |
| `/`        | GET    | API health check                         |
| `/health`  | GET    | Health check for monitoring              |
| `/docs`    | GET    | Interactive API documentation (Swagger)  |
| `/redoc`   | GET    | Alternative API documentation (ReDoc)    |

---

## Adding New Endpoints

If you create a new utility file (e.g., `utils/SI_4_installation_of_protection.py`), follow this pattern:

### 1. Create router file

```python
# app/api/routers/si4_router.py

from fastapi import APIRouter
from utils.SI_4_installation_of_protection import check_si4_compliance

router = APIRouter()

@router.post("/check")
def check_si4_endpoint(request: SI4CheckRequest):
    result = check_si4_compliance(...)
    return SI4CheckResponse(**result)
```

### 2. Create models

```python
# In app/api/models.py

class SI4CheckRequest(BaseModel):
    ifc_path: str
    # ... other fields

class SI4CheckResponse(BaseModel):
    status: str
    # ... other fields
```

### 3. Register router

```python
# In app/api/main.py

from app.api.routers import si4_router

app.include_router(si4_router.router, prefix="/api/si4", tags=["SI-4 Protection"])
```

---

## Advantages of This Architecture

### ✅ Non-Invasive

Your original utility files are never modified. If the API breaks, your utilities still work.

### ✅ Testable

You can test routers independently from utilities.

### ✅ Maintainable

Clear separation makes it easy to update either layer.

### ✅ Scalable

Easy to add new endpoints without touching existing code.

### ✅ Self-Documenting

FastAPI automatically generates interactive documentation at `/docs`.

### ✅ Type-Safe

Pydantic models catch data errors before they reach your utilities.

---

## Common Patterns

### Error Handling

```python
try:
    result = utility_function(...)
    return SuccessResponse(**result)
except FileNotFoundError:
    raise HTTPException(status_code=400, detail="File not found")
except Exception as e:
    raise HTTPException(status_code=500, detail=str(e))
```

### Async Operations (Optional)

```python
@router.post("/check")
async def check_endpoint(request: Request):
    # For I/O-heavy operations
    result = await async_utility_function(...)
    return result
```

### Background Tasks

```python
from fastapi import BackgroundTasks

@router.post("/check")
def check_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(cleanup_temp_files)
    result = utility_function(...)
    return result
```

---

## Troubleshooting

### "Module not found" errors

- Make sure you're running from the project root
- Check that `__init__.py` files exist in all directories

### "Pydantic validation error"

- Check that your request matches the model schema
- Use `/docs` to see required fields

### "Connection refused"

- Make sure the server is running (`uvicorn app.api.main:app --reload`)
- Check the port (default: 8000)

### IFC file errors

- Ensure file paths are absolute
- Check file permissions
- Verify IFC file is valid

---

## Next Steps

1. **Test the API** using Swagger UI at `/docs`
2. **Create client code** (Python, JavaScript, etc.) to call the API
3. **Add authentication** if deploying publicly (JWT, API keys)
4. **Add caching** for frequently-checked files (Redis)
5. **Deploy to production** (Docker, cloud platform)

---

## Summary

### What We Built

- FastAPI wrapper for your compliance utilities
- Clean separation between API and business logic
- Zero modification to existing utility files
- Automatic API documentation

### What You Can Do Now

- Call compliance checks from any programming language
- Integrate with web dashboards
- Use from GIS software (QGIS, ArcGIS)
- Build mobile apps that check compliance
- Automate large-scale building analysis

### Research Impact

This API transforms your compliance utilities from **Python-only tools** into **platform-independent services** that can be integrated into the FO-SEZ spatial planning pipeline.
