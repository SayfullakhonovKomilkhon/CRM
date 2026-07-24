from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from crm.config import settings
from crm.routers import auth, catalog, google_sheets, scenarios

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}


app.include_router(auth.router, prefix="/api/v1")
app.include_router(catalog.router, prefix="/api/v1")
app.include_router(scenarios.router, prefix="/api/v1")
app.include_router(google_sheets.router, prefix="/api/v1")
