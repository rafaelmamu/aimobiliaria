import logging
import uuid
from datetime import date, datetime, time, timezone

from app.models.appointment import Appointment

logger = logging.getLogger(__name__)


# Rough time-of-day mapping used when the client only says "manhã/tarde/noite".
# Stored in appointments.scheduled_time so the dashboard can render something
# useful; the broker still confirms the exact hour with the lead.
_PERIOD_TIMES: dict[str, time] = {
    "manha": time(9, 0),
    "manhã": time(9, 0),
    "tarde": time(14, 0),
    "noite": time(19, 0),
}


def _parse_period(periodo: str | None) -> time | None:
    if not periodo:
        return None
    return _PERIOD_TIMES.get(periodo.strip().lower())


async def _fetch_broker(property_client, imovel_id: str) -> tuple[str, str]:
    """Return (broker_name, broker_phone) from property details, or ("","")."""
    if not property_client or not imovel_id:
        return "", ""
    try:
        details = await property_client.get_property_details(imovel_id)
    except Exception as e:
        logger.warning(f"Failed to fetch details for broker lookup ({imovel_id}): {e}")
        return "", ""
    if not isinstance(details, dict):
        return "", ""
    cor = details.get("corretor") or {}
    nome = cor.get("nome") or ""
    telefones = cor.get("telefones") or []
    telefone = telefones[0] if telefones else ""
    return nome, telefone


async def handle_schedule_visit(
    params: dict,
    db_session=None,
    lead_id: str = None,
    tenant_id: str = None,
    property_client=None,
) -> dict:
    """Handle the agendar_visita tool call from Claude.

    Creates an appointment record in PostgreSQL, persisting the broker
    responsible for the listing (pulled from CRM49 via property_client)
    so the notification and dashboard can show who to contact.
    """
    imovel_id = params.get("imovel_id", "")
    titulo_imovel = params.get("titulo_imovel", imovel_id)
    data_preferencia = params.get("data_preferencia", "A combinar")
    periodo = params.get("periodo", "A combinar")
    observacoes = params.get("observacoes", "")

    logger.info(
        f"Scheduling visit for property {imovel_id}, date: {data_preferencia}, period: {periodo}"
    )

    protocol = str(uuid.uuid4())[:8].upper()

    # Parse date if provided
    scheduled_date = None
    if data_preferencia and data_preferencia != "A combinar":
        try:
            scheduled_date = date.fromisoformat(data_preferencia)
        except (ValueError, TypeError):
            pass

    scheduled_time = _parse_period(periodo)

    broker_name, broker_phone = await _fetch_broker(property_client, imovel_id)

    # Save to database
    if db_session and lead_id and tenant_id:
        try:
            appointment = Appointment(
                lead_id=uuid.UUID(lead_id),
                tenant_id=uuid.UUID(tenant_id),
                property_id=imovel_id,
                property_title=titulo_imovel,
                scheduled_date=scheduled_date,
                scheduled_time=scheduled_time,
                status="pending",
                notes=f"Período: {periodo}. {observacoes}".strip(),
                broker_notified=False,
                broker_name=broker_name or None,
                broker_phone=broker_phone or None,
            )
            db_session.add(appointment)
            await db_session.commit()
            logger.info(
                f"Appointment saved: {protocol} for lead {lead_id} (broker={broker_name or 'unknown'})"
            )
        except Exception as e:
            logger.error(f"Error saving appointment: {e}", exc_info=True)
            await db_session.rollback()
            return {
                "success": False,
                "message": "Erro ao registrar a visita. Por favor, tente novamente.",
            }
    else:
        logger.error("Missing db_session, lead_id, or tenant_id for scheduling")
        return {
            "success": False,
            "message": "Erro interno ao processar agendamento.",
        }

    return {
        "success": True,
        "protocolo": protocol,
        "appointment_id": str(appointment.id),
        "imovel_codigo": imovel_id,
        "data_solicitada": data_preferencia,
        "periodo": periodo,
        "observacoes": observacoes,
        "broker_name": broker_name,
        "broker_phone": broker_phone,
        "message": (
            f"Visita registrada com sucesso! Protocolo: {protocol}. "
            f"Um corretor entrará em contato para confirmar o horário."
        ),
    }
