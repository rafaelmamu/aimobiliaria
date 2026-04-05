import logging
import uuid
from datetime import date, datetime, timezone

from app.models.appointment import Appointment

logger = logging.getLogger(__name__)


async def handle_schedule_visit(
    params: dict, db_session=None, lead_id: str = None, tenant_id: str = None
) -> dict:
    """Handle the agendar_visita tool call from Claude.

    Creates an appointment record in PostgreSQL.
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

    # Save to database
    if db_session and lead_id and tenant_id:
        try:
            appointment = Appointment(
                lead_id=uuid.UUID(lead_id),
                tenant_id=uuid.UUID(tenant_id),
                property_id=imovel_id,
                property_title=titulo_imovel,
                scheduled_date=scheduled_date,
                status="pending",
                notes=f"Período: {periodo}. {observacoes}".strip(),
                broker_notified=False,
            )
            db_session.add(appointment)
            await db_session.commit()
            logger.info(f"Appointment saved: {protocol} for lead {lead_id}")
        except Exception as e:
            logger.error(f"Error saving appointment: {e}")

    return {
        "success": True,
        "protocolo": protocol,
        "imovel_codigo": imovel_id,
        "data_solicitada": data_preferencia,
        "periodo": periodo,
        "observacoes": observacoes,
        "message": (
            f"Visita registrada com sucesso! Protocolo: {protocol}. "
            f"Um corretor entrará em contato para confirmar o horário."
        ),
    }
