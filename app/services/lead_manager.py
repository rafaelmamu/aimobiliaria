import copy
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.lead import Lead
from app.models.message import Message
from app.services.qualification import (
    compute_temperature,
    qualification_stage,
)

logger = logging.getLogger(__name__)


def _deep_merge(dst: dict, src: dict) -> dict:
    """Recursive merge: src overwrites dst leaf values; dicts recurse.

    Lists/scalars from src replace dst at their key (no concat) — the bot's
    job is to send the final-state list when it knows it.
    """
    for key, value in src.items():
        if (
            isinstance(value, dict)
            and isinstance(dst.get(key), dict)
        ):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst


class LeadManager:
    """Manages lead records in PostgreSQL."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_lead(
        self, tenant_id: str, whatsapp_number: str, name: str | None = None
    ) -> Lead:
        """Get existing lead or create a new one."""
        stmt = select(Lead).where(
            Lead.tenant_id == uuid.UUID(tenant_id),
            Lead.whatsapp_number == whatsapp_number,
        )
        result = await self.db.execute(stmt)
        lead = result.scalar_one_or_none()

        if lead:
            # Update name if we got a new one
            if name and not lead.name:
                lead.name = name
                lead.updated_at = datetime.now(timezone.utc)
                await self.db.commit()
            return lead

        # Create new lead
        lead = Lead(
            tenant_id=uuid.UUID(tenant_id),
            whatsapp_number=whatsapp_number,
            name=name,
            status="new",
        )
        self.db.add(lead)
        await self.db.commit()
        await self.db.refresh(lead)

        logger.info(f"New lead created: {whatsapp_number} for tenant {tenant_id}")
        return lead

    async def save_message(
        self,
        lead_id: str,
        tenant_id: str,
        direction: str,
        content: str,
        message_type: str = "text",
        whatsapp_message_id: str | None = None,
        metadata: dict | None = None,
    ) -> Message:
        """Save a message to the conversation history."""
        message = Message(
            lead_id=uuid.UUID(lead_id),
            tenant_id=uuid.UUID(tenant_id),
            direction=direction,
            content=content,
            message_type=message_type,
            whatsapp_message_id=whatsapp_message_id,
            metadata_=metadata or {},
        )
        self.db.add(message)

        # Update lead's last_message_at
        stmt = select(Lead).where(Lead.id == uuid.UUID(lead_id))
        result = await self.db.execute(stmt)
        lead = result.scalar_one_or_none()
        if lead:
            lead.last_message_at = datetime.now(timezone.utc)
            if lead.status == "new":
                lead.status = "active"

        await self.db.commit()
        return message

    async def update_lead_profile(self, lead_id: str, profile_updates: dict) -> None:
        """Update the lead's profile data with extracted preferences.

        Top-level keys (interesse, tipo_imovel, cidade, etc.) are overwritten
        as before. The `qualification` key is deep-merged so dimensions
        accumulate across turns instead of clobbering each other.

        After merge, recomputes temperature and qualification_stage and
        persists them to the dedicated columns for fast dashboard filtering.
        """
        stmt = select(Lead).where(Lead.id == uuid.UUID(lead_id))
        result = await self.db.execute(stmt)
        lead = result.scalar_one_or_none()

        if not lead:
            return

        # Deepcopy so the deep-merge below doesn't mutate the in-flight
        # SQLAlchemy attribute before commit succeeds.
        current = copy.deepcopy(lead.profile_data or {})

        # Pull `qualification` aside so we can deep-merge it.
        incoming_qual = profile_updates.pop("qualification", None) if isinstance(profile_updates, dict) else None

        # Top-level keys: shallow overwrite (legacy behavior — buscar_imoveis
        # reads these directly).
        for key, value in profile_updates.items():
            current[key] = value

        if isinstance(incoming_qual, dict):
            existing_qual = dict(current.get("qualification") or {})
            _deep_merge(existing_qual, incoming_qual)
            existing_meta = dict(existing_qual.get("_meta") or {})
            existing_meta["atualizado_em"] = datetime.now(timezone.utc).isoformat()
            existing_qual["_meta"] = existing_meta
            current["qualification"] = existing_qual

        lead.profile_data = current
        # JSONB mutations through Python references aren't auto-detected by
        # SQLAlchemy; flag the column so the UPDATE goes out.
        flag_modified(lead, "profile_data")

        # Recompute derived columns.
        temperature, reason = compute_temperature(current)
        lead.temperature = temperature
        lead.temperature_reason = reason
        lead.qualification_stage = qualification_stage(current)

        lead.updated_at = datetime.now(timezone.utc)
        await self.db.commit()

    async def get_recent_messages(
        self, lead_id: str, limit: int = 30
    ) -> list[dict]:
        """Get recent messages for context recovery (when Redis session expired)."""
        stmt = (
            select(Message)
            .where(Message.lead_id == uuid.UUID(lead_id))
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        messages = result.scalars().all()

        # Return in chronological order
        history = []
        for msg in reversed(messages):
            role = "user" if msg.direction == "inbound" else "assistant"
            history.append({"role": role, "content": msg.content})

        return history
