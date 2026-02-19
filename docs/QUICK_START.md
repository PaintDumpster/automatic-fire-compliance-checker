# FastAPI Quick Start Guide

## Installation

```bash
# Install FastAPI and Uvicorn
pip install fastapi uvicorn python-multipart

# Or add to requirements.txt
echo "fastapi==0.104.1" >> requirements.txt
echo "uvicorn[standard]==0.24.0" >> requirements.txt
echo "python-multipart==0.0.6" >> requirements.txt
pip install -r requirements.txt
```

---

## Start the API Server

```bash
# Navigate to project root
cd /home/salva/iaac/ai_workshop/automatic-fire-compliance-checker

# Start the server
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000
```

**What this does:**
- `app.api.main:app` - Tells Uvicorn where to find the FastAPI app
- `--reload` - Auto-restart when code changes (development only)
- `--host 0.0.0.0` - Accept connections from any network interface
- `--port 8000` - Run on port 8000

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [12345] using WatchFiles
INFO:     Started server process [12346]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

---

## Test the API

### Option 1: Browser (Easiest)

Open in your browser:
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

Click on any endpoint, click "Try it out", fill in the data, and click "Execute".

---

### Option 2: Command Line (curl)

```bash
# Health check
curl http://localhost:8000/health

# SI-6 compliance check
curl -X POST "http://localhost:8000/api/si6/check" \
     -H "Content-Type: application/json" \
     -d '{
           "ifc_path": "/path/to/your/building.ifc",
           "building_use": "residential",
           "evacuation_height_m": 18.5,
           "is_basement": false
         }'

# SI-1 scan
curl -X POST "http://localhost:8000/api/si1/scan" \
     -H "Content-Type: application/json" \
     -d '{
           "ifc_path": "/path/to/your/building.ifc",
           "preview_limit": 200
         }'

# SI-3 evacuation check
curl -X POST "http://localhost:8000/api/si3/check" \
     -H "Content-Type: application/json" \
     -d '{
           "ifc_path": "/path/to/your/building.ifc",
           "building_typology": null,
           "language": "auto"
         }'
```

---

### Option 3: Python Client

```python
import requests
import json

# Base URL
BASE_URL = "http://localhost:8000"

# Example 1: SI-6 Structural Resistance Check
response = requests.post(
    f"{BASE_URL}/api/si6/check",
    json={
        "ifc_path": "/home/user/building.ifc",
        "building_use": "residential",
        "evacuation_height_m": 18.5,
        "is_basement": False
    }
)

if response.status_code == 200:
    result = response.json()
    print(f"Status: {result['status']}")
    print(f"Total elements: {result['total_elements_checked']}")
    print(f"Non-compliant: {result['non_compliant_elements']}")
else:
    print(f"Error: {response.status_code}")
    print(response.json())


# Example 2: SI-1 Scan
response = requests.post(
    f"{BASE_URL}/api/si1/scan",
    json={
        "ifc_path": "/home/user/building.ifc",
        "preview_limit": 200
    }
)

result = response.json()
print(f"Spaces found: {result['total_spaces']}")
print(f"Doors found: {result['total_doors']}")


# Example 3: SI-3 Evacuation Check
response = requests.post(
    f"{BASE_URL}/api/si3/check",
    json={
        "ifc_path": "/home/user/building.ifc",
        "building_typology": None,  # Auto-detect
        "language": "auto"
    }
)

result = response.json()
print(f"Detected typology: {result['detected_typology']}")
print(f"Total occupants: {result['total_occupants']}")
print(f"Compliance status: {result['status']}")
print(f"Issues found: {len(result['compliance_issues'])}")
```

---

## Available Endpoints

### Health & Info
```
GET  /                    - Root endpoint
GET  /health              - Health check
GET  /docs                - Swagger UI (interactive docs)
GET  /redoc               - ReDoc (alternative docs)
```

### SI-1 Interior Propagation
```
POST /api/si1/scan        - Scan IFC for spaces/doors/zones
POST /api/si1/check       - Check sector size compliance
GET  /api/si1/info        - Get endpoint information
```

### SI-3 Evacuation
```
POST /api/si3/check       - Check evacuation compliance
GET  /api/si3/info        - Get endpoint information
```

### SI-4 Fire Protection Installations
```
POST /api/si4/check       - Check fire protection systems compliance
GET  /api/si4/info        - Get endpoint information
```

### SI-6 Structural Resistance
```
POST /api/si6/check       - Check structural fire resistance
GET  /api/si6/info        - Get endpoint information
```

---

## Request Examples

### SI-6 Check Request
```json
{
    "ifc_path": "/home/user/projects/building.ifc",
    "building_use": "residential",
    "evacuation_height_m": 18.5,
    "is_basement": false
}
```

### SI-1 Scan Request
```json
{
    "ifc_path": "/home/user/projects/building.ifc",
    "preview_limit": 200
}
```

### SI-1 Compliance Check Request
```json
{
    "ifc_path": "/home/user/projects/building.ifc",
    "building_use": "residential",
    "sprinklers": false,
    "config_path": null
}
```

### SI-3 Check Request
```json
{
    "ifc_path": "/home/user/projects/building.ifc",
    "building_typology": null,
    "language": "auto"
}
```

### SI-4 Check Request
```json
{
    "ifc_path": "/home/user/projects/building.ifc",
    "config": {
        "building_use_target": "administrative",
        "rules": [
            {
                "id": "SI4-01",
                "check_key": "fire_detection",
                "requirement": "Fire detection system required",
                "applies": "always",
                "required_count": 1
            }
        ]
    }
}
```

---

## Response Format

All endpoints return JSON with consistent structure:

### Success Response (200 OK)
```json
{
    "status": "compliant",
    "total_elements_checked": 145,
    "compliant_elements": 145,
    "non_compliant_elements": 0,
    "message": "All elements comply with requirements"
}
```

### Error Response (400/500)
```json
{
    "detail": "IFC file not found: /invalid/path.ifc"
}
```

---

## Troubleshooting

### Server won't start
**Problem:** `ModuleNotFoundError: No module named 'fastapi'`
**Solution:** Install dependencies: `pip install fastapi uvicorn`

**Problem:** `Address already in use`
**Solution:** Port 8000 is busy. Use a different port:
```bash
uvicorn app.api.main:app --reload --port 8001
```

### Request fails with 422 error
**Problem:** Validation error
**Solution:** Check your request matches the expected schema. Use `/docs` to see required fields.

### Request fails with 400 error
**Problem:** IFC file not found or invalid
**Solution:** 
- Check the file path is absolute
- Verify the file exists
- Check file permissions

### Request fails with 500 error
**Problem:** Internal server error
**Solution:** Check server logs for details. Common causes:
- Corrupted IFC file
- Missing JSON config files
- Utility function errors

---

## Next Steps

1. ✅ Start the server
2. ✅ Test in browser using `/docs`
3. ✅ Try with your actual IFC files
4. ⬜ Create a client application
5. ⬜ Integrate with GIS tools
6. ⬜ Deploy to production server

---

## Production Deployment

For production, use Gunicorn with Uvicorn workers:

```bash
pip install gunicorn

gunicorn app.api.main:app \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 300
```

Or use Docker:

```dockerfile
FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Getting Help

- **API Documentation:** http://localhost:8000/docs
- **Architecture Guide:** See `docs/API_ARCHITECTURE.md`
- **Endpoint Info:** Call `GET /api/{section}/info` for each section
