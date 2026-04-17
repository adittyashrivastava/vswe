import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure all app loggers output at INFO level (uvicorn production defaults to WARNING)
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

from app.api.sessions import router as sessions_router
from app.api.messages import router as messages_router
from app.api.config_routes import router as config_router
from app.api.jobs import router as jobs_router
from app.api.costs import router as costs_router
from app.api.websocket import router as ws_router
from app.api.auth import router as auth_router
from app.config import settings
from app.db.dynamo import create_tables


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Lifespan startup — ENV=%s", settings.env)

    if settings.env == "local":
        logger.info("Local mode — creating DynamoDB tables")
        await create_tables()
    else:
        logger.info("Production mode — starting SQS consumer")
        import asyncio
        try:
            from app.webhooks.consumer import CloudEventConsumer
            consumer = CloudEventConsumer()
            logger.info("CloudEventConsumer created — queue_url=%s", consumer._queue_url[:50] if consumer._queue_url else "EMPTY")
            task = asyncio.create_task(consumer.start())
            logger.info("SQS consumer task created")
        except Exception:
            logger.exception("Failed to start SQS consumer")

    logger.info("Lifespan startup complete")
    yield
    logger.info("Lifespan shutdown")


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
# Production: start SQS consumer as a background task alongside the API
from app.config import settings as _settings

if _settings.env == "local":
    from app.webhooks.local_handler import router as webhook_router
    app.include_router(webhook_router, tags=["webhooks"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "vswe"}
