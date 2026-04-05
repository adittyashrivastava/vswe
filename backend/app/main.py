from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.sessions import router as sessions_router
from app.api.messages import router as messages_router
from app.api.config_routes import router as config_router
from app.api.jobs import router as jobs_router
from app.api.costs import router as costs_router
from app.api.websocket import router as ws_router
from app.api.auth import router as auth_router
from app.db.dynamo import create_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    yield


app = FastAPI(
    title="VSWE API",
    description="Virtual Software Engineer API",
    version="0.1.0",
    lifespan=lifespan,
)

from app.config import settings as _app_settings

app.add_middleware(
    CORSMiddleware,
    allow_origins=_app_settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(sessions_router, prefix="/api/sessions", tags=["sessions"])
app.include_router(messages_router, prefix="/api/sessions", tags=["messages"])
app.include_router(config_router, prefix="/api/config", tags=["config"])
app.include_router(jobs_router, prefix="/api/jobs", tags=["jobs"])
app.include_router(costs_router, prefix="/api/costs", tags=["costs"])
app.include_router(ws_router, tags=["websocket"])

# Local dev: mount webhook handler directly on FastAPI (bypasses Lambda + SQS)
from app.config import settings as _settings

if _settings.env == "local":
    from app.webhooks.local_handler import router as webhook_router
    app.include_router(webhook_router, tags=["webhooks"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "vswe"}
