# AImobiliarIA — Project Blueprint

## Visão Geral

AImobiliarIA é um agente de atendimento inteligente que recebe leads via WhatsApp (API Oficial Meta) e conduz conversas naturais para entender necessidades de compra ou aluguel de imóveis, buscando opções em tempo real via API da imobiliária parceira e oferecendo ao cliente as melhores opções.

**Arquitetura**: Backend Python (FastAPI) + Claude API (tool use) + Meta WhatsApp Cloud API + PostgreSQL + Redis

**Primeiro tenant**: Upside Imóveis Exclusivos (São José dos Campos, SP) — upsideimoveis.com.br

---

## Stack Tecnológico

| Componente       | Tecnologia                     | Justificativa                                      |
|------------------|--------------------------------|-----------------------------------------------------|
| Runtime          | Python 3.12 + FastAPI          | Melhor ecossistema AI, async nativo, fácil de manter |
| LLM              | Claude API (Sonnet 4)          | Tool use nativo, qualidade de conversa superior      |
| WhatsApp         | Meta Cloud API (oficial)       | Estável, sem servidor próprio, escalável             |
| Banco de dados   | PostgreSQL 16                  | Robusto, JSON support, já familiar                   |
| Cache/Sessão     | Redis                          | Sessões de conversa rápidas, TTL automático          |
| Infra            | Hetzner VPS + Coolify          | Custo-benefício, deploy fácil, SSL automático        |
| Containerização  | Docker + Docker Compose        | Portabilidade, ambientes consistentes                |

---

## Arquitetura de Alto Nível

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────┐
│   WhatsApp   │────▶│   Meta Cloud API  │────▶│   Webhook    │
│   (Lead)     │◀────│   (Oficial)       │◀────│   Endpoint   │
└─────────────┘     └──────────────────┘     └──────┬───────┘
                                                     │
                                              ┌──────▼───────┐
                                              │   FastAPI     │
                                              │   Backend     │
                                              └──────┬───────┘
                                                     │
                    ┌────────────────┬────────────────┼────────────────┐
                    │                │                │                │
             ┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐
             │  Claude API  │ │ PostgreSQL  │ │   Redis     │ │  API Imóveis │
             │  (tool use)  │ │  (dados)    │ │  (sessão)   │ │  (catálogo)  │
             └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘
```

---

## Fluxo de Conversa

```
1. Lead envia mensagem no WhatsApp
2. Meta Cloud API envia webhook POST para nosso endpoint
3. Backend identifica o lead (novo ou existente) via PostgreSQL
4. Backend carrega contexto da sessão ativa do Redis
5. Backend monta o histórico de mensagens e envia para Claude API
6. Claude decide:
   a. Continuar conversando (extrair mais informações)
   b. Chamar tool "buscar_imoveis" (tem dados suficientes)
   c. Chamar tool "detalhes_imovel" (lead quer saber mais de um imóvel)
   d. Chamar tool "agendar_visita" (lead quer visitar)
   e. Chamar tool "transferir_corretor" (lead quer falar com humano)
7. Backend processa a resposta e tools calls do Claude
8. Backend envia resposta formatada via Meta Cloud API
9. Backend salva mensagens no PostgreSQL e atualiza sessão no Redis
```

---

## Modelo de Dados (PostgreSQL)

### tenants (imobiliárias)
```sql
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,                    -- "Upside Imóveis Exclusivos"
    slug VARCHAR(100) UNIQUE NOT NULL,             -- "upside"
    api_base_url VARCHAR(500),                     -- URL base da API de imóveis
    api_key VARCHAR(500),                          -- Chave de acesso à API
    whatsapp_phone_id VARCHAR(50) NOT NULL,        -- Phone Number ID do WhatsApp Business
    whatsapp_token TEXT NOT NULL,                   -- Token de acesso Meta
    whatsapp_verify_token VARCHAR(255) NOT NULL,   -- Token de verificação do webhook
    system_prompt TEXT,                             -- Prompt personalizado do agente
    business_hours JSONB,                          -- Horário de funcionamento
    config JSONB DEFAULT '{}',                     -- Configurações extras
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### leads (contatos)
```sql
CREATE TABLE leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    whatsapp_number VARCHAR(20) NOT NULL,           -- Número do lead
    name VARCHAR(255),                              -- Nome (quando informado)
    email VARCHAR(255),                             -- Email (quando informado)
    profile_data JSONB DEFAULT '{}',                -- Dados coletados na conversa
    -- profile_data exemplo:
    -- {
    --   "interesse": "compra",
    --   "tipo_imovel": "apartamento",
    --   "cidade": "São José dos Campos",
    --   "bairros_interesse": ["Jardim Satélite", "Urbanova"],
    --   "quartos_min": 2,
    --   "preco_max": 500000,
    --   "tem_financiamento": true,
    --   "prazo_mudanca": "6 meses"
    -- }
    status VARCHAR(50) DEFAULT 'new',              -- new, active, qualified, converted, lost
    assigned_broker VARCHAR(255),                   -- Corretor responsável
    source VARCHAR(100),                            -- Origem do lead
    tags TEXT[] DEFAULT '{}',                       -- Tags para segmentação
    last_message_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, whatsapp_number)
);
```

### messages (histórico completo)
```sql
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    direction VARCHAR(10) NOT NULL,                 -- 'inbound' ou 'outbound'
    content TEXT NOT NULL,                          -- Conteúdo da mensagem
    message_type VARCHAR(20) DEFAULT 'text',        -- text, image, audio, document, template
    whatsapp_message_id VARCHAR(100),               -- ID da mensagem no WhatsApp
    metadata JSONB DEFAULT '{}',                    -- Dados extras (tool calls, etc)
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_messages_lead_id ON messages(lead_id);
CREATE INDEX idx_messages_created_at ON messages(created_at DESC);
```

### property_searches (log de buscas — analytics)
```sql
CREATE TABLE property_searches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    search_params JSONB NOT NULL,                   -- Parâmetros usados na busca
    results_count INTEGER,                          -- Quantos resultados retornaram
    properties_shown JSONB,                         -- IDs dos imóveis mostrados
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### appointments (visitas agendadas)
```sql
CREATE TABLE appointments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    property_id VARCHAR(50),                        -- ID do imóvel no sistema da imobiliária
    property_title VARCHAR(500),                    -- Título do imóvel
    scheduled_date DATE,
    scheduled_time TIME,
    status VARCHAR(50) DEFAULT 'pending',           -- pending, confirmed, completed, cancelled
    notes TEXT,
    broker_notified BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Redis — Estrutura de Sessão

```
Key: session:{tenant_id}:{whatsapp_number}
TTL: 24 horas (renova a cada mensagem)

Value (JSON):
{
    "lead_id": "uuid",
    "conversation_history": [
        {"role": "user", "content": "Oi, estou procurando um apartamento"},
        {"role": "assistant", "content": "Olá! Que bom..."},
        ...
    ],
    "extracted_preferences": {
        "interesse": "compra",
        "tipo_imovel": "apartamento",
        "cidade": "São José dos Campos"
    },
    "last_properties_shown": ["id1", "id2"],
    "state": "searching",
    "last_activity": "2026-04-04T10:30:00Z"
}
```

Quando a sessão expira (24h sem mensagem), o histórico completo já está no PostgreSQL. Na próxima mensagem, o sistema cria uma nova sessão mas pode carregar contexto relevante do banco.

---

## Claude API — System Prompt do Agente

```
Você é o assistente virtual da {nome_imobiliaria}, especialista em ajudar
clientes a encontrar o imóvel ideal na região de {regioes_atendidas}.

PERSONALIDADE:
- Cordial, profissional e acolhedor
- Usa linguagem natural e brasileira (não robótica)
- Objetivo: entender a necessidade e apresentar opções relevantes
- Nunca inventa informações sobre imóveis — só apresenta dados reais da API

FLUXO DE ATENDIMENTO:
1. Cumprimente e pergunte como pode ajudar
2. Descubra: compra ou aluguel?
3. Descubra gradualmente (sem bombardear de perguntas):
   - Tipo de imóvel (apartamento, casa, terreno, etc)
   - Região/bairro de interesse
   - Número de quartos desejado
   - Faixa de preço
   - Outras preferências relevantes
4. Quando tiver informações suficientes, busque imóveis usando a tool buscar_imoveis
5. Apresente as opções de forma resumida e atraente
6. Se o cliente se interessar, ofereça mais detalhes ou agende uma visita
7. Se não encontrar opções, sugira alternativas ou ajuste os critérios

REGRAS:
- Máximo 3 perguntas por mensagem
- Respostas curtas e diretas (WhatsApp não é email)
- Use emojis com moderação (1-2 por mensagem, máximo)
- Quando apresentar imóveis, inclua: nome, bairro, quartos, área, preço
- Sempre pergunte se o cliente quer ver mais detalhes ou outras opções
- Se o cliente pedir para falar com um corretor humano, transfira imediatamente
- Fora do horário comercial, informe e diga que um corretor retornará
```

---

## Claude API — Tools (Tool Use)

### buscar_imoveis
```json
{
    "name": "buscar_imoveis",
    "description": "Busca imóveis disponíveis no catálogo da imobiliária com base nos critérios do cliente",
    "input_schema": {
        "type": "object",
        "properties": {
            "transacao": {
                "type": "string",
                "enum": ["venda", "locacao"],
                "description": "Tipo de transação desejada"
            },
            "tipo_imovel": {
                "type": "string",
                "enum": ["apartamento", "casa", "terreno", "comercial", "rural", "chacara"],
                "description": "Tipo do imóvel"
            },
            "cidade": {
                "type": "string",
                "description": "Cidade desejada"
            },
            "bairro": {
                "type": "string",
                "description": "Bairro desejado (opcional)"
            },
            "quartos_min": {
                "type": "integer",
                "description": "Número mínimo de quartos"
            },
            "preco_min": {
                "type": "number",
                "description": "Preço mínimo em reais"
            },
            "preco_max": {
                "type": "number",
                "description": "Preço máximo em reais"
            },
            "area_min": {
                "type": "number",
                "description": "Área mínima em m²"
            }
        },
        "required": ["transacao"]
    }
}
```

### detalhes_imovel
```json
{
    "name": "detalhes_imovel",
    "description": "Obtém detalhes completos de um imóvel específico, incluindo fotos e características",
    "input_schema": {
        "type": "object",
        "properties": {
            "imovel_id": {
                "type": "string",
                "description": "Código/ID do imóvel no sistema"
            }
        },
        "required": ["imovel_id"]
    }
}
```

### agendar_visita
```json
{
    "name": "agendar_visita",
    "description": "Agenda uma visita para o cliente conhecer o imóvel pessoalmente",
    "input_schema": {
        "type": "object",
        "properties": {
            "imovel_id": {
                "type": "string",
                "description": "Código do imóvel"
            },
            "data_preferencia": {
                "type": "string",
                "description": "Data preferida pelo cliente (formato: YYYY-MM-DD)"
            },
            "periodo": {
                "type": "string",
                "enum": ["manha", "tarde", "noite"],
                "description": "Período preferido"
            },
            "observacoes": {
                "type": "string",
                "description": "Observações adicionais do cliente"
            }
        },
        "required": ["imovel_id"]
    }
}
```

### transferir_corretor
```json
{
    "name": "transferir_corretor",
    "description": "Transfere o atendimento para um corretor humano quando o cliente solicita ou quando necessário",
    "input_schema": {
        "type": "object",
        "properties": {
            "motivo": {
                "type": "string",
                "description": "Motivo da transferência"
            },
            "urgencia": {
                "type": "string",
                "enum": ["baixa", "media", "alta"],
                "description": "Nível de urgência"
            }
        },
        "required": ["motivo"]
    }
}
```

---

## Meta WhatsApp Cloud API — Configuração

### Pré-requisitos
1. Conta Meta Business verificada
2. App criado no Meta Developers (developers.facebook.com)
3. WhatsApp Business API ativada no app
4. Número de telefone dedicado registrado
5. Token de acesso permanente gerado

### Webhook
- **URL**: `https://api.aimobiliaria.com.br/webhook/whatsapp/{tenant_slug}`
- **Verify Token**: definido por tenant
- **Eventos assinados**: `messages`

### Envio de Mensagens
```
POST https://graph.facebook.com/v21.0/{phone_number_id}/messages
Authorization: Bearer {access_token}
Content-Type: application/json

{
    "messaging_product": "whatsapp",
    "to": "5512999999999",
    "type": "text",
    "text": {"body": "Olá! Como posso ajudar?"}
}
```

### Envio de Imagens (fotos de imóveis)
```
POST https://graph.facebook.com/v21.0/{phone_number_id}/messages

{
    "messaging_product": "whatsapp",
    "to": "5512999999999",
    "type": "image",
    "image": {
        "link": "https://url-da-foto-do-imovel.jpg",
        "caption": "Apartamento 3 quartos - Jardim Satélite - R$ 640.000"
    }
}
```

---

## Estrutura do Projeto

```
aimobiliaria/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app + startup/shutdown
│   ├── config.py                # Settings (pydantic-settings)
│   ├── database.py              # SQLAlchemy async engine + session
│   ├── redis_client.py          # Redis connection
│   │
│   ├── models/                  # SQLAlchemy ORM models
│   │   ├── __init__.py
│   │   ├── tenant.py
│   │   ├── lead.py
│   │   ├── message.py
│   │   ├── property_search.py
│   │   └── appointment.py
│   │
│   ├── schemas/                 # Pydantic schemas (request/response)
│   │   ├── __init__.py
│   │   ├── whatsapp.py          # WhatsApp webhook payload schemas
│   │   └── api.py               # Admin API schemas
│   │
│   ├── api/                     # Route handlers
│   │   ├── __init__.py
│   │   ├── webhooks.py          # POST /webhook/whatsapp/{slug}
│   │   ├── admin.py             # Admin endpoints (leads, analytics)
│   │   └── health.py            # Health check
│   │
│   ├── services/                # Business logic
│   │   ├── __init__.py
│   │   ├── whatsapp.py          # Meta Cloud API client
│   │   ├── ai_agent.py          # Claude API + tool use orchestration
│   │   ├── property_api.py      # Client da API de imóveis (por tenant)
│   │   ├── session_manager.py   # Redis session management
│   │   └── lead_manager.py      # Lead CRUD + status management
│   │
│   └── tools/                   # Tool handlers (chamados pelo Claude)
│       ├── __init__.py
│       ├── search_properties.py
│       ├── get_property_details.py
│       ├── schedule_visit.py
│       └── transfer_broker.py
│
├── alembic/                     # Database migrations
│   ├── env.py
│   └── versions/
│
├── tests/                       # Testes
│   ├── test_webhook.py
│   ├── test_ai_agent.py
│   └── test_property_api.py
│
├── docker-compose.yml           # PostgreSQL + Redis + App
├── docker-compose.prod.yml      # Produção (para Coolify)
├── Dockerfile
├── requirements.txt
├── alembic.ini
├── .env.example
└── README.md
```

---

## Variáveis de Ambiente (.env)

```env
# App
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8000
APP_SECRET_KEY=generate-a-strong-secret-key

# Database
DATABASE_URL=postgresql+asyncpg://aimobiliaria:password@db:5432/aimobiliaria

# Redis
REDIS_URL=redis://redis:6379/0

# Claude API
ANTHROPIC_API_KEY=sk-ant-...

# Meta WhatsApp (default — pode ser overridden por tenant)
META_WHATSAPP_VERIFY_TOKEN=aimobiliaria-verify-token-2026

# Logging
LOG_LEVEL=INFO
```

---

## Infraestrutura — Setup Completo

### 1. VPS (Hetzner)
- **Plano**: CPX21 (3 vCPU, 4GB RAM, 80GB SSD) — €7,50/mês
- **OS**: Ubuntu 24.04
- **Localização**: Nuremberg ou Helsinki
- **Domínio**: aimobiliaria.com.br (ou similar)

### 2. Coolify
- Instalação one-line na VPS
- Conecta ao repositório Git (GitHub)
- Deploy automático em push
- SSL via Let's Encrypt
- Gerencia containers (app + db + redis)
- Backups agendados do PostgreSQL

### 3. DNS
- `aimobiliaria.com.br` → VPS IP (landing page futura)
- `api.aimobiliaria.com.br` → VPS IP (backend)
- `admin.aimobiliaria.com.br` → VPS IP (dashboard futuro)

---

## Custos Estimados (Fase 1)

| Item                        | Custo Mensal      |
|-----------------------------|-------------------|
| Hetzner CPX21               | ~R$ 45            |
| Domínio .com.br             | ~R$ 5             |
| Claude API (Sonnet 4)       | R$ 50-200*        |
| WhatsApp (Meta Cloud API)   | R$ 30-100*        |
| **Total**                   | **R$ 130-350**    |

*Variável conforme volume de leads

---

## Roadmap de Desenvolvimento

### Fase 1 — MVP (Semanas 1-3)
- [ ] Setup VPS + Coolify + PostgreSQL + Redis
- [ ] Backend FastAPI com webhook WhatsApp
- [ ] Integração Claude API com tool use
- [ ] Mock da API de imóveis (dados estáticos para teste)
- [ ] Fluxo completo: receber mensagem → Claude → responder
- [ ] Deploy em produção

### Fase 2 — Integração Real (Semanas 4-5)
- [ ] Conectar API real da Upside Imóveis
- [ ] Envio de fotos de imóveis no WhatsApp
- [ ] Agendamento de visitas com notificação ao corretor
- [ ] Templates de mensagem (saudação, follow-up)

### Fase 3 — Dashboard Admin (Semanas 6-8)
- [ ] Painel web para a imobiliária
- [ ] Lista de leads com status e histórico
- [ ] Analytics: buscas mais comuns, imóveis mais vistos
- [ ] Gestão de conversas (intervir quando necessário)

### Fase 4 — Multi-Tenant (Semanas 9-12)
- [ ] Onboarding de nova imobiliária
- [ ] System prompt customizável por tenant
- [ ] Configuração de API de imóveis por tenant
- [ ] Billing por tenant

---

## Dados da Upside Imóveis (Primeiro Tenant)

**Nome**: Upside Imóveis Exclusivos
**CRECI**: 39.910-J
**Região principal**: São José dos Campos, SP
**Regiões atendidas**: SJC, Jacareí, Santa Branca, região do Vale do Paraíba
**Site**: upsideimoveis.com.br

**Tipos de imóvel no catálogo**:
- Apartamento
- Casa
- Chácara
- Comercial
- Rural
- Terreno

**Campos observados nos anúncios**:
- Código (ID interno)
- Transação (venda, locação, lançamento)
- Tipo de imóvel
- Cidade
- Região (Zona Sul, Zona Sudeste, etc)
- Bairro
- Área privativa (m²)
- Área total (m²)
- Quartos (dormitórios)
- Suítes
- Banheiros
- Vagas (coberta/descoberta)
- Preço
- Preço por m²
- Aceita financiamento
- Exclusivo (sim/não)
- Características do imóvel (armários, piscina, etc)
- Lazer do condomínio
- Descrição
- Fotos
- Vídeos
- Referência do empreendimento

---

## Notas de Implementação

### Segurança
- Tokens da Meta e Claude NUNCA no código — sempre env vars
- Validação de assinatura do webhook do WhatsApp (X-Hub-Signature-256)
- Rate limiting no webhook (evitar abuse)
- Sanitização de input do usuário antes de enviar ao Claude

### Performance
- Processamento de mensagem deve ser async (não bloquear o webhook)
- Redis para sessões ativas (evitar query no banco a cada mensagem)
- Connection pooling no PostgreSQL
- Timeout de 30s na Claude API (mensagens longas)

### Resiliência
- Retry com backoff na Claude API (pode ter rate limit)
- Queue de mensagens pendentes se Claude estiver indisponível
- Log de todas as interações para debug
- Health check endpoint para monitoramento

### WhatsApp Específico
- Mensagens do WhatsApp têm limite de ~4096 caracteres
- Se a resposta do Claude for muito longa, quebrar em múltiplas mensagens
- Marcar mensagens como "lidas" (read receipts)
- Respeitar janela de 24h para mensagens de resposta
