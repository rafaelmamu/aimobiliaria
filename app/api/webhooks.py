import logging
from functools import partial

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.tenant import Tenant
from app.redis_client import get_redis
from app.services.ai_agent import AIAgent
from app.services.lead_manager import LeadManager
from app.services.notifications import NotificationService
from app.services.property_api import MockPropertyAPIClient, PropertyAPIClient
from app.services.session_manager import SessionManager
from app.services.whatsapp import WhatsAppService
from app.tools.get_property_details import handle_get_property_details
from app.tools.save_preferences import handle_save_preferences
from app.tools.schedule_visit import handle_schedule_visit
from app.tools.search_properties import handle_search_properties
from app.tools.transfer_broker import handle_transfer_broker
from app.tools.cancel_visit import handle_cancel_visit

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()


# ─────────────────────────────────────────────
# Webhook Verification (GET)
# ─────────────────────────────────────────────


@router.get("/webhook/whatsapp/{tenant_slug}")
async def verify_webhook(
    tenant_slug: str,
    db: AsyncSession = Depends(get_db),
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    """WhatsApp webhook verification (called by Meta during setup)."""
    if hub_mode != "subscribe":
        return Response(status_code=403)

    stmt = select(Tenant).where(Tenant.slug == tenant_slug, Tenant.active == True)
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()

    if not tenant:
        logger.warning(f"Webhook verification for unknown tenant: {tenant_slug}")
        return Response(status_code=404)

    if hub_verify_token != tenant.whatsapp_verify_token:
        logger.warning(f"Invalid verify token for tenant: {tenant_slug}")
        return Response(status_code=403)

    logger.info(f"Webhook verified for tenant: {tenant_slug}")
    return Response(content=hub_challenge, media_type="text/plain")


# ─────────────────────────────────────────────
# Webhook Message Handler (POST)
# ─────────────────────────────────────────────


@router.post("/webhook/whatsapp/{tenant_slug}")
async def handle_webhook(
    tenant_slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Receive incoming WhatsApp messages and process them."""
    body = await request.json()

    stmt = select(Tenant).where(Tenant.slug == tenant_slug, Tenant.active == True)
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()

    if not tenant:
        logger.warning(f"Message for unknown tenant: {tenant_slug}")
        return {"status": "ignored"}

    message_data = WhatsAppService.parse_webhook_message(body)
    if not message_data:
        return {"status": "ok"}

    # Skip non-text messages but inform the user
    if message_data["message_type"] not in ("text", "interactive"):
        if message_data["message_type"] == "audio":
            # Let the user know we can't process audio
            background_tasks.add_task(
                _send_audio_notice,
                tenant=tenant,
                to=message_data["from_number"],
            )
        logger.info(f"Skipping {message_data['message_type']} message")
        return {"status": "ok"}

    background_tasks.add_task(
        process_incoming_message,
        tenant=tenant,
        message_data=message_data,
    )

    return {"status": "ok"}


async def _send_audio_notice(tenant: Tenant, to: str):
    """Send a notice that we can't process audio messages."""
    try:
        whatsapp = WhatsAppService(
            phone_number_id=tenant.whatsapp_phone_id,
            access_token=tenant.whatsapp_token,
        )
        await whatsapp.send_text_message(
            to=to,
            text="Desculpe, ainda não consigo ouvir áudios. 😅 Pode me enviar por texto?",
        )
    except Exception as e:
        logger.error(f"Failed to send audio notice: {e}")


# ─────────────────────────────────────────────
# Background Message Processing
# ─────────────────────────────────────────────


async def process_incoming_message(tenant: Tenant, message_data: dict):
    """Process an incoming message (runs in background).

    Full flow:
    1. Get/create lead in PostgreSQL
    2. Get/create session in Redis
    3. Save inbound message
    4. Send to Claude with tools
    5. Process tool calls (including saving preferences)
    6. Send response via WhatsApp (text + images)
    7. Save outbound message
    """
    from app.database import async_session
    from app.redis_client import redis_client

    async with async_session() as db:
        try:
            whatsapp = WhatsAppService(
                phone_number_id=tenant.whatsapp_phone_id,
                access_token=tenant.whatsapp_token,
            )
            lead_mgr = LeadManager(db)
            session_mgr = SessionManager(redis_client)
            ai_agent = AIAgent()

            from_number = message_data["from_number"]
            content = message_data["content"]
            sender_name = message_data.get("name")
            tenant_id = str(tenant.id)

            # Mark message as read
            await whatsapp.mark_as_read(message_data["message_id"])

            # 1. Get or create lead
            lead = await lead_mgr.get_or_create_lead(
                tenant_id=tenant_id,
                whatsapp_number=from_number,
                name=sender_name,
            )
            lead_id = str(lead.id)

            # 2. Get or create session
            session = await session_mgr.get_session(tenant_id, from_number)
            if not session:
                recent_messages = await lead_mgr.get_recent_messages(lead_id, limit=10)
                session = await session_mgr.create_session(
                    tenant_id, from_number, lead_id
                )
                if recent_messages:
                    session["conversation_history"] = recent_messages
                    await session_mgr.update_session(tenant_id, from_number, session)

            # 3. Save inbound message to PostgreSQL
            await lead_mgr.save_message(
                lead_id=lead_id,
                tenant_id=tenant_id,
                direction="inbound",
                content=content,
                whatsapp_message_id=message_data["message_id"],
            )

            # 4. Add to session history
            session = await session_mgr.add_message_to_history(
                tenant_id, from_number, "user", content
            )

            # 5. Create property API client
            if tenant.api_base_url:
                property_client = PropertyAPIClient(
                    base_url=tenant.api_base_url,
                    api_key=tenant.api_key,
                    config=tenant.api_config or {},
                )
            else:
                property_client = MockPropertyAPIClient(
                    base_url="http://mock", api_key=None
                )

            # 6. Build tool handlers with injected dependencies
            tool_handlers = {
                "buscar_imoveis": partial(
                    handle_search_properties, property_client=property_client
                ),
                "detalhes_imovel": partial(
                    handle_get_property_details, property_client=property_client
                ),
                "agendar_visita": partial(
                    handle_schedule_visit,
                    db_session=db,
                    lead_id=lead_id,
                    tenant_id=tenant_id,
                ),
                "transferir_corretor": partial(
                    handle_transfer_broker, tenant_config=tenant.config or {}
                ),
                "salvar_preferencias": partial(
                    handle_save_preferences,
                    lead_manager=lead_mgr,
                    lead_id=lead_id,
                ),
                "cancelar_visita": partial(
                    handle_cancel_visit,
                    db_session=db,
                    lead_id=lead_id,
                ),
            }

            # 7. Process with Claude AI
            ai_result = await ai_agent.process_message(
                conversation_history=session["conversation_history"],
                tenant_name=tenant.name,
                system_prompt=tenant.system_prompt,
                tool_handlers=tool_handlers,
            )

            response_text = ai_result["response"]

            # 8. Send response via WhatsApp (text first)
            await whatsapp.send_text_message(to=from_number, text=response_text)

            # 9. Send property images if any
            for image in ai_result.get("images_to_send", []):
                try:
                    await whatsapp.send_image_message(
                        to=from_number,
                        image_url=image["url"],
                        caption=image.get("caption", ""),
                    )
                    logger.info(f"Image sent to {from_number}: {image['url']}")
                except Exception as e:
                    logger.error(f"Failed to send image: {e}")

            # 10. Save outbound message
            await lead_mgr.save_message(
                lead_id=lead_id,
                tenant_id=tenant_id,
                direction="outbound",
                content=response_text,
                metadata={
                    "tool_calls": [
                        {"name": t["name"], "input": t["input"]}
                        for t in ai_result.get("tool_calls", [])
                    ],
                    "images_sent": len(ai_result.get("images_to_send", [])),
                },
            )

            # 11. Update session with assistant response
            await session_mgr.add_message_to_history(
                tenant_id, from_number, "assistant", response_text
            )

            # 12. Send broker notifications if needed
            broker_number = (tenant.config or {}).get("broker_notification_number")
            if broker_number:
                notifier = NotificationService(whatsapp, broker_number)
                tool_names = [t["name"] for t in ai_result.get("tool_calls", [])]

                # Notify on visit scheduled
                if "agendar_visita" in tool_names:
                    visit_call = next(
                        t for t in ai_result["tool_calls"]
                        if t["name"] == "agendar_visita"
                    )
                    visit_result = next(
                        (r["result"] for r in ai_result.get("tool_results", [])
                         if r["name"] == "agendar_visita"),
                        {}
                    )
                    await notifier.notify_visit_scheduled(
                        lead_name=sender_name or "Não informado",
                        lead_phone=from_number,
                        property_title=visit_result.get("imovel_codigo", ""),
                        property_id=visit_call["input"].get("imovel_id", ""),
                        date=visit_call["input"].get("data_preferencia", "A combinar"),
                        period=visit_call["input"].get("periodo", "A combinar"),
                        notes=visit_call["input"].get("observacoes", ""),
                        protocol=visit_result.get("protocolo", ""),
                    )

                # Notify on human transfer
                if "transferir_corretor" in tool_names:
                    transfer_call = next(
                        t for t in ai_result["tool_calls"]
                        if t["name"] == "transferir_corretor"
                    )
                    # Build conversation summary from last messages
                    history = session.get("conversation_history", [])
                    summary_msgs = [
                        m["content"][:100] for m in history[-6:]
                        if m["role"] == "user"
                    ]
                    summary = " | ".join(summary_msgs) if summary_msgs else ""

                    await notifier.notify_transfer_requested(
                        lead_name=sender_name or "Não informado",
                        lead_phone=from_number,
                        reason=transfer_call["input"].get("motivo", ""),
                        urgency=transfer_call["input"].get("urgencia", "media"),
                        conversation_summary=summary,
                    )

            logger.info(
                f"Processed message from {from_number} for tenant {tenant.slug} "
                f"(tools: {[t['name'] for t in ai_result.get('tool_calls', [])]})"
            )

        except Exception as e:
            logger.error(
                f"Error processing message from {message_data.get('from_number', '?')}: {e}",
                exc_info=True,
            )
            try:
                whatsapp = WhatsAppService(
                    phone_number_id=tenant.whatsapp_phone_id,
                    access_token=tenant.whatsapp_token,
                )
                await whatsapp.send_text_message(
                    to=message_data["from_number"],
                    text="Desculpe, tive um probleminha técnico. Pode repetir sua mensagem? 😊",
                )
            except Exception:
                logger.error("Failed to send error message to user", exc_info=True)
