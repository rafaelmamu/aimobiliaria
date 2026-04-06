import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment

logger = logging.getLogger(__name__)


async def handle_cancel_visit(
    params: dict, db_session: AsyncSession = None, lead_id: str = None
) -> dict:
    """Handle the cancelar_visita tool call from Claude.

    Cancels an existing appointment by property_id or protocol.
    """
    imovel_id = params.get("imovel_id", "")
    protocolo = params.get("protocolo", "")

    logger.info(
        f"Cancelling visit - imovel_id: {imovel_id}, protocolo: {protocolo}, lead: {lead_id}"
    )

    if not db_session or not lead_id:
        return {
            "success": False,
            "message": "Erro interno ao processar cancelamento.",
        }

    if not imovel_id and not protocolo:
        return {
            "success": False,
            "message": (
                "Preciso do código do imóvel ou do protocolo da visita "
                "para fazer o cancelamento."
            ),
        }

    # Build query to find the appointment
    query = select(Appointment).where(
        Appointment.lead_id == uuid.UUID(lead_id),
        Appointment.status.in_(["pending", "confirmed"]),
    )

    if imovel_id:
        query = query.where(Appointment.property_id == imovel_id)

    query = query.order_by(Appointment.created_at.desc())

    result = await db_session.execute(query)
    appointment = result.scalar_one_or_none()

    if not appointment:
        return {
            "success": False,
            "message": "Não encontrei nenhuma visita ativa para cancelar com esses dados.",
        }

    old_status = appointment.status
    appointment.status = "cancelled_by_client"

    try:
        await db_session.commit()
    except Exception as e:
        logger.error(f"Error cancelling appointment: {e}")
        await db_session.rollback()
        return {
            "success": False,
            "message": "Erro ao cancelar a visita. Por favor, tente novamente.",
        }

    logger.info(
        f"Appointment {appointment.id} cancelled by client "
        f"(was {old_status}, property {appointment.property_id})"
    )

    return {
        "success": True,
        "appointment_id": str(appointment.id),
        "imovel_codigo": appointment.property_id,
        "titulo_imovel": appointment.property_title or "",
        "status_anterior": old_status,
        "message": (
            f"Visita cancelada com sucesso! "
            f"O agendamento para o imóvel {appointment.property_title or appointment.property_id} "
            f"foi cancelado."
        ),
    }
