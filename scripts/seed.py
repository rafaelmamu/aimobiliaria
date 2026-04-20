"""
Seed script — Creates tables and the first tenant (Upside Imóveis).

Usage:
    python -m scripts.seed

Run this after starting PostgreSQL for the first time.
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings
from app.database import engine, async_session, Base
from app.models import Tenant, Lead, Message, PropertySearch, Appointment


# CRM49 / Upside Imóveis external API
CRM49_BASE_URL = "https://www.upsideimoveis.com.br/crm/api/v1"
CRM49_API_KEY = "by0p9r6zlqagys05euye55y0daigkgm3s1fw77qifes8wq7k718ljouaoi8qfui4"


UPSIDE_SYSTEM_PROMPT = """Você é o assistente virtual da Upside Imóveis Exclusivos, especialista em ajudar clientes a encontrar o imóvel ideal na região do Vale do Paraíba (São José dos Campos, Jacareí, Santa Branca e região).

PERSONALIDADE:
- Cordial, profissional e acolhedor
- Linguagem natural e brasileira (nunca robótica)
- Objetivo: entender a necessidade do cliente e apresentar opções relevantes
- Nunca invente informações sobre imóveis — só apresente dados reais retornados pelas tools
- Você representa a Upside Imóveis, uma imobiliária premium com foco em atendimento personalizado

FLUXO DE ATENDIMENTO:
1. Cumprimente de forma breve e pergunte como pode ajudar
2. Descubra: compra ou aluguel?
3. Descubra gradualmente (sem bombardear perguntas):
   - Tipo de imóvel (apartamento, casa, terreno, chácara, comercial)
   - Região/bairro de interesse
   - Número de quartos
   - Faixa de preço
   - Outras preferências (suíte, vaga, condomínio, etc)
4. Quando tiver informações suficientes, use a tool buscar_imoveis
5. Apresente as opções de forma resumida e atraente
6. Se houver interesse, ofereça detalhes ou agende visita
7. Se não encontrar opções, sugira ajustar critérios

REGRAS IMPORTANTES:
- Máximo 2 perguntas por mensagem
- Respostas CURTAS — isso é WhatsApp, não email
- Use emojis com moderação (1-2 por mensagem no máximo)
- Ao apresentar imóveis, use o formato abaixo
- Sempre pergunte se quer ver mais detalhes ou outras opções
- Se pedir corretor humano, use transferir_corretor imediatamente
- Nunca pressione o cliente — seja consultivo
- Se o cliente mandar mensagem fora do contexto imobiliário, redirecione gentilmente
- Quando mencionar preços de venda, formate como "R$ 640.000" (sem centavos)
- Quando mencionar preços de aluguel, formate como "R$ 1.800/mês"

FORMATO DE APRESENTAÇÃO DE IMÓVEIS:
🏠 *[título do imóvel]*
📍 [bairro], [cidade]
🛏 [quartos] quartos | 📐 [area]m²
💰 R$ [preço]
Cód: [codigo]

Se apresentar mais de um imóvel, separe com uma linha em branco entre eles.
Ao final, pergunte: "Quer saber mais sobre algum desses? É só me dizer o código! 😊"
"""


async def seed():
    settings = get_settings()
    print("🌱 AImobiliarIA — Database Seed")
    print(f"   Database: {settings.database_url.split('@')[1] if '@' in settings.database_url else 'local'}")
    print()

    # Create all tables
    print("📦 Creating tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("   ✅ Tables created!")

    # Create Upside tenant
    print()
    print("🏢 Creating Upside Imóveis tenant...")

    async with async_session() as db:
        from sqlalchemy import select

        # Check if already exists
        stmt = select(Tenant).where(Tenant.slug == "upside")
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            # Backfill CRM49 credentials on existing tenants that still point to mock
            updated_fields = []
            if not existing.api_base_url:
                existing.api_base_url = CRM49_BASE_URL
                updated_fields.append("api_base_url")
            if not existing.api_key:
                existing.api_key = CRM49_API_KEY
                updated_fields.append("api_key")

            api_config = dict(existing.api_config or {})
            if api_config.get("provider") != "crm49":
                api_config["provider"] = "crm49"
                existing.api_config = api_config
                updated_fields.append("api_config.provider")

            if updated_fields:
                await db.commit()
                print(f"   ✅ Tenant 'upside' updated: {', '.join(updated_fields)}")
            else:
                print("   ⚠️  Tenant 'upside' already exists and has CRM49 wired up.")
        else:
            tenant = Tenant(
                name="Upside Imóveis Exclusivos",
                slug="upside",
                api_base_url=CRM49_BASE_URL,
                api_key=CRM49_API_KEY,
                api_config={"provider": "crm49"},
                whatsapp_phone_id="REPLACE_WITH_PHONE_NUMBER_ID",
                whatsapp_token="REPLACE_WITH_ACCESS_TOKEN",
                whatsapp_verify_token="upside-verify-2026",
                system_prompt=UPSIDE_SYSTEM_PROMPT,
                business_hours={
                    "monday": {"open": "08:00", "close": "18:00"},
                    "tuesday": {"open": "08:00", "close": "18:00"},
                    "wednesday": {"open": "08:00", "close": "18:00"},
                    "thursday": {"open": "08:00", "close": "18:00"},
                    "friday": {"open": "08:00", "close": "18:00"},
                    "saturday": {"open": "09:00", "close": "13:00"},
                    "sunday": None,
                },
                config={
                    "regioes_atendidas": [
                        "São José dos Campos",
                        "Jacareí",
                        "Santa Branca",
                        "Caçapava",
                        "Taubaté",
                    ],
                    "broker_notification_number": "REPLACE_WITH_BROKER_NUMBER",
                    "max_properties_per_search": 5,
                },
                active=True,
            )
            db.add(tenant)
            await db.commit()
            print(f"   ✅ Tenant created! ID: {tenant.id}")
            print(f"   📱 Webhook URL: https://api.aimobiliaria.com.br/webhook/whatsapp/upside")
            print(f"   🔌 CRM49 API: {CRM49_BASE_URL}")
            print()
            print("   ⚠️  Don't forget to update:")
            print("      - whatsapp_phone_id (from Meta Business)")
            print("      - whatsapp_token (from Meta Business)")
            print("      - broker_notification_number (in config)")

    print()
    print("🎉 Seed complete!")


if __name__ == "__main__":
    asyncio.run(seed())
