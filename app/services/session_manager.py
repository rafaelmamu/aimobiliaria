import json
import logging
from datetime import datetime, timezone

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class SessionManager:
    """Manages conversation sessions in Redis for fast access."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.ttl = settings.session_ttl_seconds
        self.max_history = settings.max_conversation_history

    def _session_key(self, tenant_id: str, whatsapp_number: str) -> str:
        return f"session:{tenant_id}:{whatsapp_number}"

    async def get_session(self, tenant_id: str, whatsapp_number: str) -> dict | None:
        """Get active session for a lead."""
        key = self._session_key(tenant_id, whatsapp_number)
        data = await self.redis.get(key)
        if data:
            return json.loads(data)
        return None

    async def create_session(
        self, tenant_id: str, whatsapp_number: str, lead_id: str
    ) -> dict:
        """Create a new conversation session."""
        session = {
            "lead_id": lead_id,
            "conversation_history": [],
            "extracted_preferences": {},
            "last_properties_shown": [],
            "state": "greeting",
            "last_activity": datetime.now(timezone.utc).isoformat(),
        }
        key = self._session_key(tenant_id, whatsapp_number)
        await self.redis.set(key, json.dumps(session), ex=self.ttl)
        return session

    async def update_session(
        self, tenant_id: str, whatsapp_number: str, session: dict
    ) -> None:
        """Update session data and refresh TTL."""
        session["last_activity"] = datetime.now(timezone.utc).isoformat()
        key = self._session_key(tenant_id, whatsapp_number)
        await self.redis.set(key, json.dumps(session), ex=self.ttl)

    async def add_message_to_history(
        self,
        tenant_id: str,
        whatsapp_number: str,
        role: str,
        content: str,
    ) -> dict:
        """Add a message to the conversation history.

        Trims history to max_history to keep context window manageable.
        """
        session = await self.get_session(tenant_id, whatsapp_number)
        if not session:
            return None

        session["conversation_history"].append({"role": role, "content": content})

        # Keep only last N messages to avoid huge context
        if len(session["conversation_history"]) > self.max_history:
            session["conversation_history"] = session["conversation_history"][
                -self.max_history :
            ]

        await self.update_session(tenant_id, whatsapp_number, session)
        return session

    async def update_preferences(
        self, tenant_id: str, whatsapp_number: str, preferences: dict
    ) -> None:
        """Update extracted preferences from conversation."""
        session = await self.get_session(tenant_id, whatsapp_number)
        if session:
            session["extracted_preferences"].update(preferences)
            await self.update_session(tenant_id, whatsapp_number, session)

    async def set_last_properties(
        self, tenant_id: str, whatsapp_number: str, property_ids: list[str]
    ) -> None:
        """Track which properties were shown to the lead."""
        session = await self.get_session(tenant_id, whatsapp_number)
        if session:
            session["last_properties_shown"] = property_ids
            await self.update_session(tenant_id, whatsapp_number, session)

    async def delete_session(self, tenant_id: str, whatsapp_number: str) -> None:
        """Delete a session (e.g., after transfer to broker)."""
        key = self._session_key(tenant_id, whatsapp_number)
        await self.redis.delete(key)
