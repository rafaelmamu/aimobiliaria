import asyncio
import logging
import time

from sqlalchemy import select

from app.config import get_settings
from app.database import async_session
from app.models.tenant import Tenant
from app.redis_client import redis_client
from app.services.crm49_client import CRM49Client, is_crm49_tenant
from app.services.property_cache import PropertyCache

logger = logging.getLogger(__name__)
settings = get_settings()


async def sync_tenant_properties(tenant: Tenant) -> int:
    """Sync a single tenant's properties from CRM49 into Redis.

    Returns the number of properties synced, or 0 if the tenant is not
    a CRM49 tenant / the sync failed / the API returned nothing.
    On failure the previous cache snapshot is preserved.
    """
    if not is_crm49_tenant(tenant):
        logger.debug(
            f"Skipping tenant {tenant.slug}: not a CRM49 tenant "
            f"(has_url={bool(tenant.api_base_url)}, has_key={bool(tenant.api_key)}, "
            f"provider={(tenant.api_config or {}).get('provider')!r})"
        )
        return 0

    tenant_id = str(tenant.id)
    cache = PropertyCache(redis_client)
    client = CRM49Client(
        base_url=tenant.api_base_url,
        api_key=tenant.api_key,
        tenant_id=tenant_id,
        redis_client=redis_client,
    )

    t0 = time.monotonic()
    try:
        properties = await client.list_all_active()
    except Exception as e:
        logger.error(
            f"CRM49 sync failed for tenant {tenant.slug}: {e}", exc_info=True
        )
        return 0

    if not properties:
        logger.warning(
            f"CRM49 sync returned 0 properties for tenant {tenant.slug}; "
            "keeping previous cache"
        )
        return 0

    await cache.set_listing(tenant_id, properties)
    await cache.set_last_sync(tenant_id)
    elapsed = time.monotonic() - t0
    logger.info(
        f"✅ Synced {len(properties)} properties for tenant {tenant.slug} "
        f"in {elapsed:.1f}s"
    )
    return len(properties)


async def sync_all_tenants_once() -> dict:
    """One pass of the sync across all active CRM49 tenants.

    Returns a summary dict so callers (admin endpoint, tests) can inspect
    what happened without scraping logs.
    """
    async with async_session() as db:
        stmt = select(Tenant).where(Tenant.active == True)  # noqa: E712
        result = await db.execute(stmt)
        tenants = list(result.scalars().all())

    crm49_tenants = [t for t in tenants if is_crm49_tenant(t)]
    logger.info(
        f"CRM49 sync iteration starting: {len(tenants)} active tenant(s), "
        f"{len(crm49_tenants)} CRM49"
    )
    if not crm49_tenants:
        logger.warning(
            "No CRM49 tenants found. Ensure the Upside tenant has api_base_url, "
            "api_key, and api_config.provider='crm49' (or api_base_url pointing to "
            "upsideimoveis.com.br)."
        )

    results: list[dict] = []
    for tenant in crm49_tenants:
        try:
            count = await sync_tenant_properties(tenant)
            results.append({"slug": tenant.slug, "synced": count})
        except Exception as e:
            logger.error(
                f"Unexpected error syncing tenant {tenant.slug}: {e}",
                exc_info=True,
            )
            results.append({"slug": tenant.slug, "synced": 0, "error": str(e)})

    return {
        "total_tenants": len(tenants),
        "crm49_tenants": len(crm49_tenants),
        "results": results,
    }


async def run_sync_loop() -> None:
    """Background task: sync all CRM49 tenants on startup and every N minutes."""
    if not settings.crm49_sync_enabled:
        logger.info("CRM49 sync disabled via CRM49_SYNC_ENABLED=false")
        return

    interval = max(1, settings.crm49_sync_interval_minutes) * 60

    # Small delay so the HTTP server is responsive before the first sync
    try:
        await asyncio.sleep(2)
    except asyncio.CancelledError:
        return

    while True:
        try:
            await sync_all_tenants_once()
        except asyncio.CancelledError:
            logger.info("CRM49 sync loop cancelled")
            raise
        except Exception as e:
            logger.error(f"CRM49 sync loop iteration failed: {e}", exc_info=True)

        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("CRM49 sync loop cancelled during sleep")
            raise
