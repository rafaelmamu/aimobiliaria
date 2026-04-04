import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    # API da imobiliária (catálogo de imóveis)
    api_base_url: Mapped[str | None] = mapped_column(String(500))
    api_key: Mapped[str | None] = mapped_column(String(500))
    api_config: Mapped[dict] = mapped_column(JSONB, default=dict)

    # WhatsApp Business API (Meta)
    whatsapp_phone_id: Mapped[str] = mapped_column(String(50), nullable=False)
    whatsapp_token: Mapped[str] = mapped_column(Text, nullable=False)
    whatsapp_verify_token: Mapped[str] = mapped_column(String(255), nullable=False)

    # Configuração do agente
    system_prompt: Mapped[str | None] = mapped_column(Text)
    business_hours: Mapped[dict | None] = mapped_column(JSONB)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    leads = relationship("Lead", back_populates="tenant", lazy="selectin")
