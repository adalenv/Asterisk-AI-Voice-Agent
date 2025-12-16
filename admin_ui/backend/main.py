from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
import settings
from dotenv import load_dotenv

# Load environment variables (wizard will create .env from .env.example on first Next click)
load_dotenv(settings.ENV_PATH)

from api import config, system, wizard, logs, local_ai, ollama, mcp
import auth

app = FastAPI(title="Asterisk AI Voice Agent Admin API")

# Initialize users (create default admin if needed)
auth.load_users()

# Warn loudly if JWT_SECRET isn't set (auth reads it at import time).
if auth.SECRET_KEY == "dev-secret-key-change-in-prod":
    # Don't crash dev, but make this impossible to miss in logs.
    import logging

    logging.getLogger(__name__).warning(
        "JWT_SECRET is not set; Admin UI is using the default dev secret. "
        "Set JWT_SECRET in .env for production."
    )

# Configure CORS
def _parse_cors_origins() -> list[str]:
    raw = (settings.get_setting("ADMIN_UI_CORS_ORIGINS", "") or "").strip()
    if not raw:
        # Safe-ish local defaults.
        return ["http://localhost:3003", "http://127.0.0.1:3003"]
    if raw == "*":
        return ["*"]
    # Comma-separated list
    return [o.strip() for o in raw.split(",") if o.strip()]


cors_origins = _parse_cors_origins()
cors_allow_credentials = "*" not in cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
# Public routes
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])

# Protected routes
app.include_router(config.router, prefix="/api/config", tags=["config"], dependencies=[Depends(auth.get_current_user)])
app.include_router(system.router, prefix="/api/system", tags=["system"], dependencies=[Depends(auth.get_current_user)])
app.include_router(wizard.router, prefix="/api/wizard", tags=["wizard"], dependencies=[Depends(auth.get_current_user)])
app.include_router(logs.router, prefix="/api/logs", tags=["logs"], dependencies=[Depends(auth.get_current_user)])
app.include_router(local_ai.router, prefix="/api/local-ai", tags=["local-ai"], dependencies=[Depends(auth.get_current_user)])
app.include_router(mcp.router, dependencies=[Depends(auth.get_current_user)])
app.include_router(ollama.router, tags=["ollama"], dependencies=[Depends(auth.get_current_user)])

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# Serve static files (Frontend)
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

# Mount static files if directory exists (production/docker)
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")
    
    @app.get("/{full_path:path}")
    async def serve_react_app(full_path: str):
        # API routes are already handled above
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
            
        # Serve index.html for all other routes (SPA)
        response = FileResponse(os.path.join(static_dir, "index.html"))
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
