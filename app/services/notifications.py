import logging
from datetime import datetime, timezone, timedelta

from app.services.whatsapp import WhatsAppService

logger = logging.getLogger(__name__)

BR_TZ = timezone(timedelta(hours=-3))


class NotificationService:
    """Sends notifications to brokers via WhatsApp."""

    def __init__(self, whatsapp: WhatsAppService, broker_number: str):
        self.whatsapp = whatsapp
        self.broker_number = broker_number

    async def notify_new_lead(self, lead_name: str, lead_phone: str):
        """Notify broker about a new lead."""
        now = datetime.now(BR_TZ).strftime("%d/%m/%Y %H:%M")
        message = (
            f"🆕 *Novo lead no AImobiliarIA*\n\n"
            f"👤 {lead_name or 'Não informado'}\n"
            f"📱 {lead_phone}\n"
            f"🕐 {now}\n\n"
            f"O atendimento automático já iniciou."
        )
        await self._send(message)

    async def notify_visit_scheduled(
        self,
        lead_name: str,
        lead_phone: str,
        property_title: str,
        property_id: str,
        date: str = "A combinar",
        period: str = "A combinar",
        notes: str = "",
        protocol: str = "",
    ):
        """Notify broker about a scheduled visit."""
        now = datetime.now(BR_TZ).strftime("%d/%m/%Y %H:%M")
        message = (
            f"📅 *Visita Agendada!*\n\n"
            f"👤 *Lead:* {lead_name or 'Não informado'}\n"
            f"📱 *WhatsApp:* {lead_phone}\n"
            f"🏠 *Imóvel:* {property_title} (Cód: {property_id})\n"
            f"📆 *Data:* {date}\n"
            f"🕐 *Período:* {period}\n"
        )
        if notes:
            message += f"📝 *Obs:* {notes}\n"
        if protocol:
            message += f"🔖 *Protocolo:* {protocol}\n"
        message += (
            f"\n⏰ Solicitado em: {now}\n\n"
            f"Entre em contato com o lead para confirmar!"
        )
        await self._send(message)

    async def notify_transfer_requested(
        self,
        lead_name: str,
        lead_phone: str,
        reason: str,
        urgency: str = "media",
        conversation_summary: str = "",
    ):
        """Notify broker about a human transfer request."""
        now = datetime.now(BR_TZ).strftime("%d/%m/%Y %H:%M")
        urgency_emoji = {"baixa": "🟢", "media": "🟡", "alta": "🔴"}.get(urgency, "🟡")
        message = (
            f"🙋 *Corretor Solicitado!*\n\n"
            f"👤 *Lead:* {lead_name or 'Não informado'}\n"
            f"📱 *WhatsApp:* {lead_phone}\n"
            f"{urgency_emoji} *Urgência:* {urgency}\n"
            f"💬 *Motivo:* {reason}\n"
        )
        if conversation_summary:
            message += f"\n📋 *Resumo:* {conversation_summary}\n"
        message += (
            f"\n⏰ Solicitado em: {now}\n\n"
            f"Por favor, entre em contato o mais rápido possível!"
        )
        await self._send(message)

    async def notify_visit_cancelled(
        self,
        lead_name: str,
        lead_phone: str,
        property_title: str,
        property_id: str,
    ):
        """Notify broker about a visit cancelled by client."""
        now = datetime.now(BR_TZ).strftime("%d/%m/%Y %H:%M")
        message = (
            f"❌ *Visita Cancelada pelo Cliente*\n\n"
            f"👤 *Lead:* {lead_name or 'Não informado'}\n"
            f"📱 *WhatsApp:* {lead_phone}\n"
            f"🏠 *Imóvel:* {property_title} (Cód: {property_id})\n"
            f"\n⏰ Cancelado em: {now}\n\n"
            f"O cliente desmarcou o agendamento."
        )
        await self._send(message)

    async def _send(self, message: str):
        """Send notification message to broker."""
        try:
            await self.whatsapp.send_text_message(
                to=self.broker_number,
                text=message,
            )
            logger.info(f"Broker notification sent to {self.broker_number}")
        except Exception as e:
            logger.error(f"Failed to send broker notification: {e}")
