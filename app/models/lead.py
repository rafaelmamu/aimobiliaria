import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    whatsapp_number: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))

    # Dados extraídos da conversa pelo AI
    profile_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    status: Mapped[str] = mapped_column(String(50), default="new")
    assigned_broker: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str | None] = mapped_column(String(100))
    tags: Mapped[list] = mapped_column(ARRAY(Text), default=list)

    # Qualificação (metodologia Upside — apostila cap. 3 e 8). Recalculados
    # cada vez que o tool `salvar_preferencias` grava em profile_data.
    temperature: Mapped[str | None] = mapped_column(String(10))  # hot|warm|cold
    qualification_stage: Mapped[str | None] = mapped_column(String(30))
    temperature_reason: Mapped[str | None] = mapped_column(String(255))

    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="leads")
    messages = relationship("Message", back_populates="lead", lazy="selectin")

    __table_args__ = (
        # Um lead por número de WhatsApp por tenant
        {"info": {"unique_together": ("tenant_id", "whatsapp_number")}},
    )
