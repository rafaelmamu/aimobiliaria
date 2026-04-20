import asyncio
import logging
import os

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse

# DNS override must be installed before any httpx client is constructed
from app.services.dns_override import install_ipv4_only_override

install_ipv4_only_override()

from app.api.auth import router as auth_router, verify_token
from app.api.health import router as health_router
from app.api.webhooks import router as webhook_router
from app.api.admin import router as admin_router
from app.config import get_settings
from app.database import engine
from app.redis_client import redis_client
from app.services.property_sync import run_sync_loop

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

    # Test database connection and ensure tables exist
    try:
        async with engine.begin() as conn:
            from sqlalchemy import text
            await conn.execute(text("SELECT 1"))
        logger.info("✅ PostgreSQL connected")

        # Ensure all tables exist (creates missing ones, safe to run always)
        from app.database import Base
        from app.models import Tenant, Lead, Message, PropertySearch, Appointment  # noqa
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Database tables synced")
    except Exception as e:
        logger.error(f"❌ PostgreSQL connection failed: {e}")

    # Start CRM49 property sync loop in background (non-blocking)
    sync_task: asyncio.Task | None = None
    try:
        sync_task = asyncio.create_task(run_sync_loop(), name="crm49-sync-loop")
        logger.info("🔄 CRM49 sync loop scheduled")
    except Exception as e:
        logger.error(f"❌ Failed to schedule CRM49 sync loop: {e}")

    logger.info("✅ AImobiliarIA ready to receive messages!")

    yield

    # Shutdown
    logger.info("Shutting down...")
    if sync_task and not sync_task.done():
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error stopping sync task: {e}")
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


# ─────────────────────────────────────────────
# Auth Middleware
# ─────────────────────────────────────────────

PROTECTED_PATHS = ["/dashboard", "/admin"]


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Protect /dashboard and /admin/* routes with session cookie."""
    path = request.url.path

    needs_auth = any(path.startswith(p) for p in PROTECTED_PATHS)

    if needs_auth:
        token = request.cookies.get("session")
        if not verify_token(token):
            if "application/json" in request.headers.get("accept", ""):
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Not authenticated"},
                )
            return RedirectResponse(url="/login", status_code=303)

    response = await call_next(request)
    return response


# Include routers
app.include_router(auth_router)
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
