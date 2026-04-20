import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.appointment import Appointment
from app.models.lead import Lead
from app.models.message import Message
from app.models.tenant import Tenant
from app.redis_client import redis_client
from app.services.crm49_client import is_crm49_tenant
from app.services.property_cache import PropertyCache
from app.services.property_sync import sync_all_tenants_once
from app.services.session_manager import SessionManager

router = APIRouter(prefix="/admin", tags=["admin"])


# ─────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────


class TenantCreate(BaseModel):
    name: str
    slug: str
    api_base_url: str | None = None
    api_key: str | None = None
    whatsapp_phone_id: str
    whatsapp_token: str
    whatsapp_verify_token: str
    system_prompt: str | None = None


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────
# Tenant Endpoints
# ─────────────────────────────────────────────


@router.post("/tenants", response_model=TenantResponse)
async def create_tenant(data: TenantCreate, db: AsyncSession = Depends(get_db)):
    """Create a new tenant (real estate agency)."""
    # Check if slug exists
    stmt = select(Tenant).where(Tenant.slug == data.slug)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Slug already exists")

    tenant = Tenant(**data.model_dump())
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)

    return TenantResponse(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        active=tenant.active,
        created_at=tenant.created_at,
    )


@router.get("/tenants")
async def list_tenants(db: AsyncSession = Depends(get_db)):
    """List all tenants."""
    stmt = select(Tenant).order_by(Tenant.created_at)
    result = await db.execute(stmt)
    tenants = result.scalars().all()

    return [
        {
            "id": str(t.id),
            "name": t.name,
            "slug": t.slug,
            "active": t.active,
            "created_at": t.created_at,
        }
        for t in tenants
    ]


# ─────────────────────────────────────────────
# Lead Endpoints
# ─────────────────────────────────────────────


@router.get("/tenants/{tenant_slug}/leads")
async def list_leads(
    tenant_slug: str,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List leads for a tenant."""
    # Get tenant
    stmt = select(Tenant).where(Tenant.slug == tenant_slug)
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Query leads
    query = select(Lead).where(Lead.tenant_id == tenant.id)
    if status:
        query = query.where(Lead.status == status)
    query = query.order_by(Lead.last_message_at.desc().nullslast())

    result = await db.execute(query)
    leads = result.scalars().all()

    return [
        {
            "id": str(l.id),
            "name": l.name,
            "whatsapp_number": l.whatsapp_number,
            "status": l.status,
            "profile_data": l.profile_data,
            "last_message_at": l.last_message_at,
            "created_at": l.created_at,
        }
        for l in leads
    ]


@router.post("/leads/{lead_id}/reset-conversation")
async def reset_lead_conversation(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Wipe a lead's conversation so the bot starts fresh on the next message.

    Clears BOTH the Redis session AND the PostgreSQL `messages` rows for the
    lead. Clearing only Redis is not enough: when the session expires, the
    webhook restores history from the DB (`webhooks.py:180`), which would
    resurrect the same context that made the bot say "no results".
    """
    try:
        lead_uuid = uuid.UUID(lead_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid lead_id")

    lead = await db.get(Lead, lead_uuid)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    result = await db.execute(
        delete(Message).where(Message.lead_id == lead_uuid)
    )
    deleted = result.rowcount or 0

    lead.last_message_at = None
    await db.commit()

    session_mgr = SessionManager(redis_client)
    await session_mgr.delete_session(str(lead.tenant_id), lead.whatsapp_number)

    return {
        "success": True,
        "lead_id": lead_id,
        "messages_deleted": deleted,
    }


@router.get("/leads/{lead_id}/messages")
async def get_lead_messages(
    lead_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Get conversation history for a lead."""
    stmt = (
        select(Message)
        .where(Message.lead_id == uuid.UUID(lead_id))
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()

    return [
        {
            "id": str(m.id),
            "direction": m.direction,
            "content": m.content,
            "message_type": m.message_type,
            "created_at": m.created_at,
            "metadata": m.metadata_,
        }
        for m in reversed(messages)
    ]


# ─────────────────────────────────────────────
# Stats Endpoint
# ─────────────────────────────────────────────


@router.get("/tenants/{tenant_slug}/stats")
async def get_tenant_stats(
    tenant_slug: str,
    db: AsyncSession = Depends(get_db),
):
    """Get basic stats for a tenant."""
    stmt = select(Tenant).where(Tenant.slug == tenant_slug)
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Total leads
    total_leads = await db.scalar(
        select(func.count()).select_from(Lead).where(Lead.tenant_id == tenant.id)
    )

    # Active leads (messaged in last 7 days)
    active_leads = await db.scalar(
        select(func.count())
        .select_from(Lead)
        .where(
            Lead.tenant_id == tenant.id,
            Lead.status == "active",
        )
    )

    # Total messages
    total_messages = await db.scalar(
        select(func.count())
        .select_from(Message)
        .where(Message.tenant_id == tenant.id)
    )

    # Appointments
    total_appointments = await db.scalar(
        select(func.count())
        .select_from(Appointment)
        .where(Appointment.tenant_id == tenant.id)
    )

    return {
        "tenant": tenant.name,
        "total_leads": total_leads,
        "active_leads": active_leads,
        "total_messages": total_messages,
        "total_appointments": total_appointments,
    }


# ─────────────────────────────────────────────
# Appointments Endpoint
# ─────────────────────────────────────────────


@router.get("/tenants/{tenant_slug}/appointments")
async def list_appointments(
    tenant_slug: str,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List all appointments for a tenant."""
    stmt = select(Tenant).where(Tenant.slug == tenant_slug)
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    query = (
        select(Appointment, Lead)
        .join(Lead, Appointment.lead_id == Lead.id)
        .where(Appointment.tenant_id == tenant.id)
    )
    if status:
        query = query.where(Appointment.status == status)
    query = query.order_by(Appointment.created_at.desc())

    try:
        result = await db.execute(query)
        rows = result.all()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error querying appointments: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    return [
        {
            "id": str(apt.id),
            "lead_id": str(apt.lead_id),
            "lead_name": lead.name or "Não informado",
            "lead_phone": lead.whatsapp_number,
            "lead_profile": lead.profile_data or {},
            "property_id": apt.property_id,
            "property_title": apt.property_title,
            "scheduled_date": apt.scheduled_date.isoformat() if apt.scheduled_date else None,
            "status": apt.status,
            "notes": apt.notes,
            "broker_notified": apt.broker_notified,
            "created_at": apt.created_at.isoformat() if apt.created_at else None,
            "updated_at": apt.updated_at.isoformat() if apt.updated_at else None,
        }
        for apt, lead in rows
    ]


class AppointmentUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None
    scheduled_date: str | None = None


@router.patch("/appointments/{appointment_id}")
async def update_appointment(
    appointment_id: str,
    data: AppointmentUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update appointment status or notes."""
    stmt = select(Appointment).where(Appointment.id == uuid.UUID(appointment_id))
    result = await db.execute(stmt)
    apt = result.scalar_one_or_none()
    if not apt:
        raise HTTPException(status_code=404, detail="Appointment not found")

    valid_statuses = {"pending", "confirmed", "completed", "cancelled", "cancelled_by_client"}
    if data.status:
        if data.status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status: {data.status}")
        apt.status = data.status
    if data.notes is not None:
        apt.notes = data.notes
    if data.scheduled_date:
        try:
            from datetime import date as date_type
            apt.scheduled_date = date_type.fromisoformat(data.scheduled_date)
        except ValueError:
            pass

    apt.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {"success": True, "status": apt.status}


# ─────────────────────────────────────────────
# CRM49 Sync Endpoints (diagnostics + force-refresh)
# ─────────────────────────────────────────────


@router.get("/crm49/status")
async def crm49_sync_status(db: AsyncSession = Depends(get_db)):
    """Inspect CRM49 sync state per tenant without touching any logs."""
    stmt = select(Tenant).where(Tenant.active == True)  # noqa: E712
    result = await db.execute(stmt)
    tenants = list(result.scalars().all())

    cache = PropertyCache(redis_client)
    out: list[dict] = []
    for t in tenants:
        is_crm49 = is_crm49_tenant(t)
        cached = await cache.get_listing(str(t.id)) if is_crm49 else []
        last_sync = await cache.get_last_sync(str(t.id)) if is_crm49 else None
        out.append(
            {
                "slug": t.slug,
                "is_crm49": is_crm49,
                "has_api_base_url": bool(t.api_base_url),
                "has_api_key": bool(t.api_key),
                "provider": (t.api_config or {}).get("provider"),
                "cached_count": len(cached),
                "last_sync": last_sync.isoformat() if last_sync else None,
            }
        )
    return {"tenants": out}


@router.post("/crm49/sync")
async def crm49_force_sync():
    """Trigger an immediate CRM49 sync and return the per-tenant result."""
    summary = await sync_all_tenants_once()
    return summary


@router.get("/crm49/peek")
async def crm49_peek(
    q: str | None = None,
    tipo: str | None = None,
    transacao: str | None = None,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """Inspect cached listings — for debugging why a search returns zero.

    Filters the Redis cache by optional substring on bairro/titulo/descricao
    (`q`, case+diacritic insensitive), optional `tipo` (exact match on the
    normalized field — casa/apartamento/terreno/…), and optional `transacao`
    (venda/locacao). Deduplicates by id so we don't see the same property
    repeated when the cache has duplicates. Also reports total_unique so we
    can spot sync-level duplication.
    """
    from app.services.property_filters import _fold

    stmt = select(Tenant).where(Tenant.active == True)  # noqa: E712
    result = await db.execute(stmt)
    tenants = [t for t in result.scalars().all() if is_crm49_tenant(t)]

    cache = PropertyCache(redis_client)
    out: list[dict] = []
    needle = _fold(q) if q else ""
    for t in tenants:
        props = await cache.get_listing(str(t.id)) or []
        seen: set[str] = set()
        matched: list[dict] = []
        for p in props:
            pid = p.get("id") or ""
            if pid in seen:
                continue
            if tipo and p.get("tipo") != tipo:
                continue
            if transacao and p.get("transacao") != transacao:
                continue
            if needle:
                haystack = " ".join(
                    _fold(p.get(f))
                    for f in ("bairro", "titulo", "descricao")
                )
                if needle not in haystack:
                    continue
            seen.add(pid)
            matched.append(
                {
                    "id": pid,
                    "codigo": p.get("codigo"),
                    "tipo": p.get("tipo"),
                    "_raw_tipo": p.get("_raw_tipo"),
                    "transacao": p.get("transacao"),
                    "_raw_transacoes": p.get("_raw_transacoes"),
                    "bairro": p.get("bairro"),
                    "cidade": p.get("cidade"),
                    "titulo": p.get("titulo"),
                    "preco": p.get("preco"),
                }
            )
            if len(matched) >= limit:
                break
        unique_ids = {p.get("id") for p in props}
        out.append(
            {
                "tenant": t.slug,
                "total_cached": len(props),
                "total_unique": len(unique_ids),
                "matched": len(matched),
                "sample": matched,
            }
        )
    return {
        "query": q,
        "filter_tipo": tipo,
        "filter_transacao": transacao,
        "tenants": out,
    }
