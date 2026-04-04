# 🏠 AImobiliarIA

Agente de atendimento inteligente para imobiliárias via WhatsApp. Recebe leads, conduz conversas naturais, busca imóveis em tempo real via API e oferece as melhores opções ao cliente.

## Stack

- **Backend**: Python 3.12 + FastAPI
- **LLM**: Claude API (Sonnet 4) com tool use
- **WhatsApp**: Meta Cloud API (oficial)
- **Banco**: PostgreSQL 16
- **Cache**: Redis 7
- **Infra**: Docker + Coolify (Hetzner VPS)

## Setup Rápido (Desenvolvimento Local)

### 1. Pré-requisitos
- Docker e Docker Compose instalados
- Chave API da Anthropic (Claude)

### 2. Configurar variáveis de ambiente
```bash
cp .env.example .env
# Edite .env e preencha pelo menos:
# - ANTHROPIC_API_KEY
# - POSTGRES_PASSWORD
```

### 3. Subir os serviços
```bash
docker-compose up -d
```

### 4. Criar tabelas e tenant inicial
```bash
docker-compose exec app python -m scripts.seed
```

### 5. Testar o agente localmente (sem WhatsApp)
```bash
export ANTHROPIC_API_KEY=sk-ant-...
python -m scripts.test_agent
```

### 6. Verificar se está rodando
```
http://localhost:8000/health
http://localhost:8000/docs
```

## Configurar WhatsApp (Meta Cloud API)

1. Criar app em [Meta Developers](https://developers.facebook.com/)
2. Ativar produto "WhatsApp Business"
3. Obter **Phone Number ID** e **Access Token**
4. Configurar webhook: `https://api.aimobiliaria.com.br/webhook/whatsapp/upside`
5. Token de verificação: `upside-verify-2026`
6. Assinar evento: `messages`

## Deploy (Produção)

### Hetzner VPS + Coolify

1. Criar VPS CPX21 na Hetzner (Ubuntu 24.04)
2. Instalar Coolify: `curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash`
3. Conectar repositório Git
4. Configurar variáveis de ambiente no Coolify
5. Deploy via `docker-compose.prod.yml`

## Estrutura do Projeto

```
aimobiliaria/
├── app/
│   ├── main.py              # FastAPI app
│   ├── config.py             # Settings
│   ├── database.py           # PostgreSQL
│   ├── redis_client.py       # Redis
│   ├── models/               # ORM models
│   ├── api/                  # Route handlers
│   │   ├── webhooks.py       # WhatsApp webhook
│   │   ├── admin.py          # Admin endpoints
│   │   └── health.py         # Health check
│   ├── services/             # Business logic
│   │   ├── whatsapp.py       # Meta API client
│   │   ├── ai_agent.py       # Claude + tools
│   │   ├── property_api.py   # API de imóveis
│   │   ├── session_manager.py # Redis sessions
│   │   └── lead_manager.py   # Lead CRUD
│   └── tools/                # Claude tool handlers
├── scripts/
│   ├── seed.py               # DB setup + first tenant
│   └── test_agent.py         # Test agent locally
├── alembic/                  # DB migrations
├── docker-compose.yml        # Development
├── docker-compose.prod.yml   # Production
└── PROJECT_BLUEPRINT.md      # Full project spec
```

## API Endpoints

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/health` | Health check |
| GET | `/webhook/whatsapp/{slug}` | Webhook verification (Meta) |
| POST | `/webhook/whatsapp/{slug}` | Receive messages |
| POST | `/admin/tenants` | Create tenant |
| GET | `/admin/tenants` | List tenants |
| GET | `/admin/tenants/{slug}/leads` | List leads |
| GET | `/admin/leads/{id}/messages` | Conversation history |
| GET | `/admin/tenants/{slug}/stats` | Stats |

## Roadmap

- [x] Fase 1: Backend + Claude Agent + WhatsApp webhook
- [ ] Fase 2: API real da imobiliária + fotos no WhatsApp
- [ ] Fase 3: Dashboard admin para a imobiliária
- [ ] Fase 4: Multi-tenant + billing

---

**AImobiliarIA** — Corretor virtual inteligente, 24 horas, para sua imobiliária.
