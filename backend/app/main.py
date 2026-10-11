"""OCTForge FastAPI Application Entry Point.

Mounts /api routes from api.py and serves the plain HTML/JS frontend from frontend/ at /.
"""

from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.app.api import router as api_router

app = FastAPI(
    title="OCTForge Backend",
    description="Synthetic OCT Image Generation & Forward Modeling Platform",
    version="1.0.0",
)

# Mount API Router under /api
app.include_router(api_router, prefix="/api")

# Resolve frontend directory path relative to project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static_frontend")
