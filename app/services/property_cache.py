import json
import logging
from datetime import datetime, timezone

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class PropertyCache:
    """Redis cache for CRM49 properties per tenant.

    Stores:
    - Listing (normalized, all active properties) as a JSON list without TTL,
      so a failed sync leaves the previous snapshot available.
    - Last successful sync timestamp (ISO 8601).
    - Individual property details with short TTL, used to reduce round-trips
      when the same imóvel is asked about multiple times in a session.
    """

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def _listing_key(self, tenant_id: str) -> str:
        return f"crm49:properties:{tenant_id}"

    def _last_sync_key(self, tenant_id: str) -> str:
        return f"crm49:last_sync:{tenant_id}"

    def _details_key(self, tenant_id: str, property_id: str) -> str:
        return f"crm49:details:{tenant_id}:{property_id}"

    async def set_listing(self, tenant_id: str, properties: list[dict]) -> None:
        key = self._listing_key(tenant_id)
        await self.redis.set(key, json.dumps(properties, ensure_ascii=False))

    async def get_listing(self, tenant_id: str) -> list[dict]:
        key = self._listing_key(tenant_id)
        data = await self.redis.get(key)
        if not data:
            return []
        try:
            return json.loads(data)
        except json.JSONDecodeError as e:
            logger.error(f"Bad JSON in property cache for {tenant_id}: {e}")
            return []

    async def set_last_sync(self, tenant_id: str, dt: datetime | None = None) -> None:
        dt = dt or datetime.now(timezone.utc)
        await self.redis.set(self._last_sync_key(tenant_id), dt.isoformat())

    async def get_last_sync(self, tenant_id: str) -> datetime | None:
        data = await self.redis.get(self._last_sync_key(tenant_id))
        if not data:
            return None
        try:
            return datetime.fromisoformat(data)
        except ValueError:
            return None

    async def set_details(
        self, tenant_id: str, property_id: str, data: dict, ttl: int = 900
    ) -> None:
        key = self._details_key(tenant_id, property_id)
        await self.redis.set(key, json.dumps(data, ensure_ascii=False), ex=ttl)

    async def get_details(self, tenant_id: str, property_id: str) -> dict | None:
        key = self._details_key(tenant_id, property_id)
        data = await self.redis.get(key)
        if not data:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None
