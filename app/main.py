import logging
import os

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.health import router as health_router
from app.api.webhooks import router as webhook_router
from app.api.admin import router as admin_router
from app.config import get_settings
from app.database import engine
from app.redis_client import redis_client

settings = get_settings()

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Lifespan (startup/shutdown)
# ─────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("🚀 AImobiliarIA starting up...")
    logger.info(f"Environment: {settings.app_env}")

    # Test Redis connection
    try:
        await redis_client.ping()
        logger.info("✅ Redis connected")
    except Exception as e:
        logger.error(f"❌ Redis connection failed: {e}")

    # Test database connection
    try:
        async with engine.begin() as conn:
            from sqlalchemy import text
            await conn.execute(text("SELECT 1"))
        logger.info("✅ PostgreSQL connected")
    except Exception as e:
        logger.error(f"❌ PostgreSQL connection failed: {e}")

    logger.info("✅ AImobiliarIA ready to receive messages!")

    yield

    # Shutdown
    logger.info("Shutting down...")
    await redis_client.close()
    await engine.dispose()
    logger.info("👋 AImobiliarIA stopped")


# ─────────────────────────────────────────────
# App
# ─────────────────────────────────────────────

app = FastAPI(
    title="AImobiliarIA",
    description="AI-powered real estate agent for WhatsApp",
    version="0.1.0",
    lifespan=lifespan,
)

# Include routers
app.include_router(health_router, tags=["health"])
app.include_router(webhook_router, tags=["webhook"])
app.include_router(admin_router)


@app.get("/")
async def root():
    return {
        "app": "AImobiliarIA",
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs",
        "dashboard": "/dashboard",
    }


@app.get("/dashboard")
async def dashboard():
    """Serve the admin dashboard."""
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    return FileResponse(os.path.join(static_dir, "dashboard.html"))
