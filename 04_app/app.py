"""Attach War-Room — single Databricks App.

FastAPI serves the built Vite bundle (StaticFiles) AND hosts the agent loop,
Genie/metric-view queries, and Lakebase state behind /api. All Databricks
credentials stay server-side (see server/dbx.py); the browser only talks to /api.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "server"))  # vendored dbx/tools/agent + routes/

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from routes import chat, metrics, state, checkout, admin  # noqa: E402

app = FastAPI(title="Attach War-Room")
for r in (chat, metrics, state, checkout, admin):
    app.include_router(r.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"ok": True}


# serve the built frontend (vite build -> frontend/dist) as the SPA
DIST = os.path.join(HERE, "frontend", "dist")
if os.path.isdir(DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = os.path.join(DIST, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(DIST, "index.html"))
