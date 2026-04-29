import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import anthropic

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# Defensive: Claude sometimes echoes raw tool-call markup as text
# (e.g. <invoke name="..."><parameter ...>...</parameter></invoke>) when
# the model gets confused. Strip that out before sending to the user so
# WhatsApp never sees XML.
_TOOL_TAG_NAMES = ("invoke", "parameter", "function_calls", "tool_use")
# Match a paired tag of one of the names above (with its content) — non-greedy
# but anchored to the same tag name so nested tags don't cross-match.
_TOOL_PAIRED_RES = [
    re.compile(rf"<\s*{name}\b[^>]*>.*?<\s*/\s*{name}\s*>", re.DOTALL | re.IGNORECASE)
    for name in _TOOL_TAG_NAMES
]
# Match orphan opening/closing/self-closing tags left after paired removal.
_TOOL_ORPHAN_RE = re.compile(
    rf"<\s*/?\s*(?:{'|'.join(_TOOL_TAG_NAMES)}|antml:[^>\s]+)\b[^>]*/?>",
    re.IGNORECASE,
)


def _sanitize_text(text: str) -> str:
    """Remove any raw tool-call markup the model may have leaked into text."""
    cleaned = text
    # Run paired removal a few times to handle nesting (parameter inside invoke).
    for _ in range(3):
        before = cleaned
        for pattern in _TOOL_PAIRED_RES:
            cleaned = pattern.sub("", cleaned)
        if cleaned == before:
            break
    cleaned = _TOOL_ORPHAN_RE.sub("", cleaned)
    return cleaned.strip()

# Timezone for São Paulo
BR_TZ = timezone(timedelta(hours=-3))

# ─────────────────────────────────────────────
# Tool Definitions for Claude
# ─────────────────────────────────────────────

TOOLS = [
    {
        "name": "buscar_imoveis",
        "description": (
            "Busca imóveis disponíveis no catálogo da imobiliária com base nos "
            "critérios do cliente. Use quando tiver informações suficientes sobre "
            "o que o cliente procura (pelo menos o tipo de transação)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "transacao": {
                    "type": "string",
                    "enum": ["venda", "locacao"],
                    "description": "Tipo de transação: venda ou locação",
                },
                "tipo_imovel": {
                    "type": "string",
                    "enum": [
                        "apartamento",
                        "casa",
                        "terreno",
                        "comercial",
                        "rural",
                        "chacara",
                    ],
                    "description": "Tipo do imóvel desejado",
                },
                "cidade": {
                    "type": "string",
                    "description": "Cidade desejada (ex: São José dos Campos)",
                },
                "bairro": {
                    "type": "string",
                    "description": "Bairro desejado (opcional)",
                },
                "condominio": {
                    "type": "string",
                    "description": (
                        "Nome do condomínio ou empreendimento mencionado pelo "
                        "cliente (ex: 'Condomínio Colinas', 'Esplanada do Sol'). "
                        "Use isto, NÃO o campo bairro, quando o cliente citar um "
                        "condomínio — condomínio não é bairro. Busca no título "
                        "e descrição do imóvel."
                    ),
                },
                "quartos_min": {
                    "type": "integer",
                    "description": "Número mínimo de quartos/dormitórios",
                },
                "preco_min": {
                    "type": "number",
                    "description": "Preço mínimo em reais",
                },
                "preco_max": {
                    "type": "number",
                    "description": "Preço máximo em reais",
                },
                "area_min": {
                    "type": "number",
                    "description": "Área mínima em m²",
                },
            },
            "required": ["transacao"],
        },
    },
    {
        "name": "detalhes_imovel",
        "description": (
            "Obtém detalhes completos e fotos de um imóvel específico. "
            "SEMPRE use esta tool quando o cliente: pedir mais detalhes, "
            "quiser ver fotos, mencionar o nome ou código de um imóvel já "
            "apresentado, disser coisas como 'me fale mais', 'quero saber mais', "
            "'mostra esse', 'me conta sobre o primeiro/segundo', 'quero ver as fotos', "
            "'tem foto?', 'como é esse imóvel?', ou qualquer variação. "
            "Por padrão envia 1 foto chamariz; use `quantidade_fotos` maior "
            "apenas quando o cliente pedir explicitamente mais imagens "
            "('tem mais foto?', 'foto da cozinha', 'manda mais umas')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "imovel_id": {
                    "type": "string",
                    "description": "Código/ID do imóvel no sistema",
                },
                "quantidade_fotos": {
                    "type": "integer",
                    "description": (
                        "Quantas fotos enviar nesta resposta. Default = 1 "
                        "(foto chamariz com legenda do título+código). Use "
                        "valores maiores (até 5) APENAS quando o cliente "
                        "pediu explicitamente mais imagens. Se o cliente "
                        "está apenas perguntando sobre o imóvel pela primeira "
                        "vez, use 1."
                    ),
                    "minimum": 0,
                    "maximum": 5,
                },
            },
            "required": ["imovel_id"],
        },
    },
    {
        "name": "agendar_visita",
        "description": (
            "Agenda uma visita para o cliente conhecer o imóvel. "
            "Use quando o cliente demonstrar interesse em visitar um imóvel."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "imovel_id": {
                    "type": "string",
                    "description": "Código do imóvel a ser visitado",
                },
                "titulo_imovel": {
                    "type": "string",
                    "description": "Nome/título do imóvel (ex: Wonder - 3 Dorms)",
                },
                "data_preferencia": {
                    "type": "string",
                    "description": "Data preferida (formato: YYYY-MM-DD)",
                },
                "periodo": {
                    "type": "string",
                    "enum": ["manha", "tarde", "noite"],
                    "description": "Período preferido para a visita",
                },
                "observacoes": {
                    "type": "string",
                    "description": "Observações adicionais do cliente",
                },
            },
            "required": ["imovel_id"],
        },
    },
    {
        "name": "transferir_corretor",
        "description": (
            "Transfere o atendimento para um corretor humano. "
            "Use quando o cliente pedir explicitamente para falar com uma pessoa, "
            "ou quando a situação exigir atendimento humano (negociação de preço, "
            "dúvidas jurídicas, financiamento detalhado)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motivo": {
                    "type": "string",
                    "description": "Motivo da transferência",
                },
                "urgencia": {
                    "type": "string",
                    "enum": ["baixa", "media", "alta"],
                    "description": "Nível de urgência",
                },
            },
            "required": ["motivo"],
        },
    },
    {
        "name": "salvar_preferencias",
        "description": (
            "Salva ou atualiza as preferências do cliente conforme você descobre "
            "durante a conversa. Chame esta tool SEMPRE que o cliente informar: "
            "se quer comprar ou alugar, tipo de imóvel, cidade, bairro, número de "
            "quartos, faixa de preço, se tem financiamento, prazo pra mudar, ou "
            "qualquer outra preferência relevante. Pode chamar múltiplas vezes "
            "conforme descobre novas informações."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "interesse": {
                    "type": "string",
                    "enum": ["compra", "aluguel"],
                    "description": "Se o cliente quer comprar ou alugar",
                },
                "tipo_imovel": {
                    "type": "string",
                    "description": "Tipo de imóvel desejado (apartamento, casa, etc)",
                },
                "cidade": {
                    "type": "string",
                    "description": "Cidade de interesse",
                },
                "bairros": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Bairros de interesse",
                },
                "condominio": {
                    "type": "string",
                    "description": (
                        "Condomínio ou empreendimento específico de interesse "
                        "(ex: 'Condomínio Colinas'). Separado de bairro porque "
                        "condomínio não é bairro."
                    ),
                },
                "quartos_min": {
                    "type": "integer",
                    "description": "Número mínimo de quartos",
                },
                "preco_min": {
                    "type": "number",
                    "description": "Preço mínimo em reais",
                },
                "preco_max": {
                    "type": "number",
                    "description": "Preço máximo em reais",
                },
                "area_min": {
                    "type": "number",
                    "description": "Área mínima desejada em m²",
                },
                "tem_financiamento": {
                    "type": "boolean",
                    "description": "Se o cliente pretende financiar",
                },
                "prazo_mudanca": {
                    "type": "string",
                    "description": "Prazo pra mudança (ex: imediato, 3 meses, 6 meses)",
                },
                "observacoes": {
                    "type": "string",
                    "description": "Outras preferências mencionadas pelo cliente",
                },
                "modo_detectado": {
                    "type": "string",
                    "enum": ["modo_1", "modo_2"],
                    "description": (
                        "Modo de qualificação ativo neste turno. modo_1 = "
                        "qualificar antes de mostrar imóvel (cliente escreve "
                        "longo, faz perguntas detalhadas). modo_2 = mostrar "
                        "âncora cedo e refinar por reação (cliente direto, "
                        "respostas curtas). Padrão modo_2 em caso de dúvida."
                    ),
                },
                "qualification": {
                    "type": "object",
                    "description": (
                        "Dados das 7 dimensões da metodologia Upside. "
                        "Preencha SOMENTE o que o cliente disse — não invente. "
                        "Acumula a cada turno (não precisa reenviar dimensões "
                        "antigas)."
                    ),
                    "properties": {
                        "motivacao": {
                            "type": "object",
                            "description": "Por que o cliente está comprando agora.",
                            "properties": {
                                "gatilho": {
                                    "type": "string",
                                    "description": (
                                        "Evento que gerou a busca: casamento, "
                                        "filho, fim de aluguel, herança, etc."
                                    ),
                                },
                                "situacao_atual": {
                                    "type": "string",
                                    "enum": [
                                        "aluguel",
                                        "imovel_proprio_para_vender",
                                        "com_familia",
                                        "outro",
                                    ],
                                },
                                "proposito": {
                                    "type": "string",
                                    "enum": [
                                        "moradia",
                                        "investimento_renda",
                                        "investimento_valorizacao",
                                        "comercial",
                                    ],
                                },
                                "objecao_latente": {
                                    "type": "string",
                                    "description": (
                                        "O que o impediria de fechar mesmo "
                                        "achando o imóvel ideal."
                                    ),
                                },
                            },
                        },
                        "perfil_vida": {
                            "type": "object",
                            "description": "Quem mora e como é a rotina.",
                            "properties": {
                                "moradores": {"type": "integer"},
                                "filhos_idades": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                },
                                "pet": {
                                    "type": "object",
                                    "properties": {
                                        "tem": {"type": "boolean"},
                                        "porte": {
                                            "type": "string",
                                            "enum": ["pequeno", "medio", "grande"],
                                        },
                                    },
                                },
                                "home_office": {
                                    "type": "string",
                                    "enum": ["nao", "esporadico", "frequente"],
                                },
                                "veiculos": {"type": "integer"},
                                "vagas_eliminatorias": {
                                    "type": "boolean",
                                    "description": (
                                        "True quando garagem coberta ou um "
                                        "número específico de vagas é "
                                        "critério eliminatório."
                                    ),
                                },
                                "estilo_vida": {
                                    "type": "string",
                                    "description": "Ativo, social, quieto, cultural, etc.",
                                },
                            },
                        },
                        "localizacao": {
                            "type": "object",
                            "description": "Onde precisa estar e o que precisa ter por perto.",
                            "properties": {
                                "polo_trabalho": {"type": "string"},
                                "escolas_filhos": {"type": "string"},
                                "bairros_descartados": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "bairro": {"type": "string"},
                                            "motivo": {"type": "string"},
                                        },
                                    },
                                },
                                "indispensavel_perto": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "O que não pode faltar a menos de 10 "
                                        "min de casa (escola, academia, etc)."
                                    ),
                                },
                                "tempo_max_commute": {
                                    "type": "integer",
                                    "description": "Minutos toleráveis no trajeto.",
                                },
                            },
                        },
                        "tipologia": {
                            "type": "object",
                            "description": (
                                "Atributos do imóvel. Separe necessidade "
                                "(eliminatório) de desejo (negociável)."
                            ),
                            "properties": {
                                "necessidades": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "Critérios eliminatórios — descartam "
                                        "imóvel se ausentes."
                                    ),
                                },
                                "desejos": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "Bônus negociáveis — cliente abriria "
                                        "exceção se outras condições forem boas."
                                    ),
                                },
                                "aceita_reforma": {
                                    "type": "string",
                                    "enum": ["sim", "nao", "cosmetica"],
                                },
                                "suites_min": {"type": "integer"},
                                "imoveis_visitados": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "O que já viu e gostou/não gostou.",
                                },
                            },
                        },
                        "financeiro": {
                            "type": "object",
                            "description": (
                                "Capacidade financeira. Aborde DEPOIS de "
                                "rapport — nunca como primeira pergunta."
                            ),
                            "properties": {
                                "modalidade": {
                                    "type": "string",
                                    "enum": [
                                        "a_vista",
                                        "financiamento",
                                        "fgts_financiamento",
                                        "permuta",
                                    ],
                                },
                                "credito_status": {
                                    "type": "string",
                                    "enum": ["nao_simulou", "simulou", "aprovado"],
                                },
                                "fgts_disponivel": {"type": "boolean"},
                                "imovel_para_venda": {
                                    "type": "boolean",
                                    "description": (
                                        "Cliente precisa vender outro imóvel "
                                        "antes — bloqueio típico."
                                    ),
                                },
                                "ciente_custos_acessorios": {
                                    "type": "boolean",
                                    "description": "ITBI/cartório/registro.",
                                },
                            },
                        },
                        "urgencia": {
                            "type": "object",
                            "description": "Quando precisa se mudar.",
                            "properties": {
                                "prazo": {
                                    "type": "string",
                                    "enum": ["imediato", "60_180_dias", "indefinido"],
                                },
                                "evento_ancora": {
                                    "type": "string",
                                    "description": (
                                        "O que ancora o prazo: fim de "
                                        "contrato, ano letivo, casamento."
                                    ),
                                },
                                "disponibilidade_visita": {
                                    "type": "string",
                                    "enum": [
                                        "ate_3_dias",
                                        "ate_1_semana",
                                        "sem_disponibilidade",
                                    ],
                                    "description": (
                                        "Termômetro mais confiável — cliente "
                                        "que não consegue visitar em 3 dias é "
                                        "morno mesmo declarando urgência."
                                    ),
                                },
                                "outros_corretores": {"type": "boolean"},
                            },
                        },
                        "decisores": {
                            "type": "object",
                            "description": (
                                "Quem mais participa da decisão — pergunte no "
                                "início e revalide antes da visita."
                            ),
                            "properties": {
                                "outros": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "relacao": {
                                                "type": "string",
                                                "description": "cônjuge, pai, sócio, etc.",
                                            },
                                            "ja_visitou": {"type": "boolean"},
                                        },
                                    },
                                },
                                "aprovador_oculto": {
                                    "type": "string",
                                    "description": (
                                        "Alguém que não visita mas tem poder "
                                        "de veto."
                                    ),
                                },
                                "influenciador_externo": {
                                    "type": "string",
                                    "description": (
                                        "Familiar/amigo que entende de "
                                        "imóveis e influencia."
                                    ),
                                },
                                "prioridades_decisor_2": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
    {
        "name": "cancelar_visita",
        "description": (
            "Cancela uma visita previamente agendada pelo cliente. "
            "Use quando o cliente pedir para cancelar, desistir ou desmarcar "
            "uma visita a um imóvel. Precisa do código do imóvel ou do protocolo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "imovel_id": {
                    "type": "string",
                    "description": "Código do imóvel cuja visita será cancelada",
                },
                "protocolo": {
                    "type": "string",
                    "description": "Protocolo do agendamento a ser cancelado",
                },
            },
        },
    },
]

# ─────────────────────────────────────────────
# Default System Prompt
# ─────────────────────────────────────────────

DEFAULT_SYSTEM_PROMPT = """Você é corretor(a) virtual da {tenant_name}, atendendo no WhatsApp na região do Vale do Paraíba (foco: São José dos Campos e cidades próximas).

CONTEXTO:
- Data e hora atual: {current_datetime}
- Atendimento 24/7. Equipe humana: seg-sex 8h-18h, sáb 9h-13h. Fora disso, avise que um corretor humano retorna no próximo horário comercial caso o cliente peça.

QUEM VOCÊ É (consultor, não atendente de formulário):
- Fala como amigo(a) que entende de imóveis: leve, direto, brasileiro. Sem jargão corporativo, sem "rsrs", sem gírias pesadas.
- Reage ao que o cliente disse antes de perguntar a próxima coisa. Curiosidade genuína > checklist.
- Sugere alternativas (bairro vizinho, faixa próxima, outro tipo) em vez de devolver "não encontrei".
- Nunca inventa imóvel, preço, foto ou bairro: só fala de dado que veio de uma tool.

═══════════════════════════════════════════
DETECTAR O MODO DE QUALIFICAÇÃO (importante)
═══════════════════════════════════════════
A cada turno, leia os SINAIS do cliente e escolha um dos 2 modos:

▸ MODO 2 — MOSTRA CEDO, REFINA POR REAÇÃO (padrão)
  Ative quando: mensagem inicial < 20 palavras E sem contexto rico, respostas de 1-3 palavras, cliente direto ("quero apto SJC até 600k"), pediu pra ver imóveis logo, ou já visitou várias casas e sabe o que quer.
  Como agir: complete a QMV (Qualificação Mínima Viável) — perfil/uso + localização + faixa de valor — em 3-4 turnos curtos, e SÓ ENTÃO chame `buscar_imoveis`. Use a REAÇÃO do cliente ao primeiro imóvel pra refinar dimensões mais profundas. No Modo 2, no máximo 2 perguntas por mensagem; se ele ignorar uma, siga adiante com o que respondeu.

▸ MODO 1 — QUALIFICA ANTES DE MOSTRAR
  Ative quando: mensagem inicial > 50 palavras, cliente explica histórico/critérios em parágrafos, MENCIONA CONTEXTO RICO (motivo: filho, casamento, fim de aluguel; histórico de buscas frustradas; situação familiar), faz perguntas sobre processo/financiamento/documentação, ou demonstra frustração com buscas anteriores. Mensagens curtas mas com contexto significativo ("estou cansado do aluguel, casei recentemente, queria um apto pros 3") TAMBÉM ativam Modo 1 — não deixa o tamanho cegar.
  Como agir: cubra motivação → perfil/localização → tipologia → financeiro → urgência/decisores ANTES de listar imóvel. Uma pergunta por turno, embutida em frase natural. Valida o que ouviu antes de avançar.

  REGRA OBRIGATÓRIA EM ABERTURA MODO 1: Quando o cliente abre o turno com mensagem rica (filha, casamento, home office, etc.), seu PRIMEIRO turno de resposta DEVE conter texto que: (a) referencia 1-2 detalhes específicos que ele compartilhou em tom caloroso ("Que bacana! Família com filha pequena e você trabalhando de casa..." ou similar) e (b) faz UMA pergunta de aprofundamento da dimensão mais relevante (motivação se for emocional, perfil/localização se já deu contexto familiar). NUNCA responda só com `salvar_preferencias` em silêncio nesse caso — gera sensação de bot frio. `salvar_preferencias` roda em paralelo com o texto.

▸ Em dúvida, vá de Modo 2. Mas TRANSICIONE pra Modo 1 quando: cliente engaja muito (respostas longas), pediu pra visitar, ou faltam dimensões críticas (decisores ou financeiro) após 3 rodadas.

Sempre que chamar `salvar_preferencias`, passe `modo_detectado=modo_1` ou `modo_detectado=modo_2` baseado no que está fazendo NESTE turno.

═══════════════════════════════════════════
QMV — QUALIFICAÇÃO MÍNIMA VIÁVEL (3 informações antes da 1ª busca)
═══════════════════════════════════════════
NUNCA chame `buscar_imoveis` pela PRIMEIRA vez sem ter coletado AS 3 INFORMAÇÕES da QMV:
  1) TIPO/TRANSAÇÃO: apto, casa, comercial, etc. + venda ou aluguel
  2) LOCALIZAÇÃO: cidade (e/ou bairro, condomínio, região — qualquer ancora local)
  3) FAIXA DE VALOR: teto que considera, faixa, ou pelo menos uma referência ("uns 500k", "até 1M")

⚠️ Quartos NÃO são parte da QMV — refina depois pela reação do cliente. O importante é não desperdiçar a busca trazendo valores fora do orçamento.

Se o cliente já passou as 3 informações numa única mensagem (ex.: "tem apto pra venda em SJC até 500k?" → tipo=apto, transação=venda, cidade=SJC, teto=500k), BUSQUE NO MESMO TURNO. Não pergunte mais nada antes — o teto está dito.

Sem 1 das 3 informações, FAÇA UMA pergunta natural pra completar (uma de cada vez, no Modo 2 pode agrupar 2 quando flui). Em vez de "qual sua faixa?", ancore: "tenho desde uns 300k até 1.5M nessa região, tem um teto em mente?"

Após a 1ª busca: aí sim "BUSQUE CEDO" — toda nova preferência (refinamento de bairro, número de quartos, etc.) dispara nova busca imediata.

═══════════════════════════════════════════
AS 7 DIMENSÕES (checklist mental, não formulário)
═══════════════════════════════════════════
Cubra progressivamente. Não decore. Nunca pergunte tudo de uma vez. Salve o que descobre em `salvar_preferencias.qualification.<dimensão>`.

1. MOTIVAÇÃO — por que tá comprando agora? Gatilho (filho, fim de aluguel, casamento), situação atual (aluguel/imóvel próprio), propósito (moradia/investimento).
2. PERFIL DE VIDA — quem mora, idades, pet, home office, quantos carros, estilo (ativo/quieto).
3. LOCALIZAÇÃO — onde trabalha (e parceiro), escola dos filhos, bairros descartados E POR QUÊ, o que precisa ter a 10 min de casa. NUNCA sugira bairro antes de ouvir o perfil de deslocamento completo.
4. TIPOLOGIA — tipo, quartos, suítes, aceita reforma. CRÍTICO: separe NECESSIDADE (eliminatório, ex.: "mín 3 quartos", "2 vagas") de DESEJO (negociável, ex.: "varanda gourmet", "andar alto"). Use a pergunta-chave: "Você abriria exceção nisso se tudo mais fosse exatamente o que você quer?" — se sim, é desejo. Pergunta sobre reforma é estratégica e abre portfólio: "Toparia uma reforma cosmética se localização e preço forem perfeitos?"
5. FINANCEIRO — modalidade (à vista, financiamento, FGTS, permuta), simulou crédito? FGTS disponível? imóvel pra vender antes? ciente dos custos acessórios (ITBI, cartório ~3-5%)? NUNCA é a primeira pergunta — só aborde após motivação + perfil + localização. Use abertura natural: "Pra eu filtrar bem sem te mandar nada fora, qual faixa você considera?"
6. URGÊNCIA — prazo, evento âncora (fim de contrato, ano letivo), disponibilidade pra visitar. ATENÇÃO: disponibilidade pra visitar em até 3 dias é o termômetro mais confiável. Se não consegue visitar nesse prazo, é morno mesmo dizendo que tem pressa.
7. DECISORES — quem mais decide? cônjuge/pais/sócios? aprovador oculto que não visita mas tem veto? influenciador externo (parente que entende)? Mapeie ANTES da visita. Imprescindível.

═══════════════════════════════════════════
GATE PRÉ-VISITA (não pule)
═══════════════════════════════════════════
ANTES de chamar `agendar_visita`, confirme nesta ordem natural (uma de cada vez se faltar):
1) Quem mais vem na visita (decisores)?
2) Faixa de orçamento confirmada?
3) Prazo estimado pra decisão?
Se algum dos 3 estiver faltando do `qualification`, pergunte ANTES de agendar. Visita sem esses 3 é desperdício.

═══════════════════════════════════════════
CURADORIA — A REGRA DOS 3 (apresentação de imóveis)
═══════════════════════════════════════════
Quando enviar lista de 3 imóveis, mire em uma combinação:
1ª — ÂNCORA: atende todos os critérios, no teto do orçamento. É o "sonho possível" — legitima o preço das outras.
2ª — RACIONAL: 10-20% abaixo do teto. Cede em 1 desejo (não em necessidade) mas oferece custo-benefício claro. Cliente sente "ganha mais por menos".
3ª — SURPRESA: fora do declarado mas alinhada com o perfil real (ex.: bairro que ele não listou mas atende rotina melhor). Demonstra que você ouviu além do explicitado.

QUANDO COMENTAR a lógica em texto: APENAS quando você tem qualificação suficiente pra justificar (motivação OU tipologia detalhada OU financeiro pelo menos parcial). Aí sim diga em UMA linha o porquê de cada uma — ex.: "a primeira é a mais completa pra família com filho; a segunda cede em andar mas é melhor custo; a terceira é uma surpresa porque o bairro tem ótima rotina pra você". NÃO force a lógica quando ainda só tem QMV — nesse momento, mostre 3 opções variadas e deixe a reação do cliente guiar.

Use no máximo 3 por mensagem. Mais opções paralisam.

═══════════════════════════════════════════
ENVIO DE FOTOS — UMA POR VEZ
═══════════════════════════════════════════
Por padrão, `detalhes_imovel` envia 1 FOTO CHAMARIZ (a principal, com legenda do título+código). NUNCA mande as 5 fotos de uma vez — a pergunta do final fica enterrada e o cliente não responde.

Quando chamar `detalhes_imovel`:
- 1ª vez que ele pergunta sobre um imóvel: NÃO passe `quantidade_fotos` (default = 1). No texto, MENCIONE que tem mais fotos disponíveis — ex.: "Tenho mais umas 4 fotos por aqui se quiser ver — só pedir."
- Cliente pediu mais ("tem mais foto?", "manda mais umas", "quero ver a cozinha"): aí sim chame `detalhes_imovel` de novo com `quantidade_fotos=4` (ou 5).
- A foto vai chegar ANTES do seu texto pro cliente (o sistema cuida disso). Sua mensagem de texto deve ser uma reação contextual ao imóvel + UMA pergunta — pergunta SEMPRE no final.

═══════════════════════════════════════════
NÃO COMETA OS 6 ERROS CLÁSSICOS
═══════════════════════════════════════════
1. Mostrar imóvel antes de qualificar nada (no Modo 1 ainda mais; mesmo no Modo 2, junte 1 sinal antes).
2. Perguntar orçamento como primeira ou segunda pergunta — gera desconforto, cliente subestima.
3. Não mapear decisores — depois aparece um cônjuge que veta tudo.
4. Não separar necessidade de desejo — restringe portfólio artificialmente.
5. Qualificar uma vez e nunca atualizar — em follow-up de 7+ dias, abra com "algo mudou desde nossa última conversa?".
6. Tratar qualificação como interrogatório — alterne perguntas com observações ("muita gente com seu perfil acaba descobrindo o bairro X…") e validações ("faz sentido").

═══════════════════════════════════════════
REGRAS DE CONVERSA
═══════════════════════════════════════════
- Máximo 1 pergunta por mensagem (no Modo 2 pode ser até 2 quando agrupadas naturalmente).
- Mensagens curtas (2-4 linhas no geral). É WhatsApp.
- 0 a 2 emojis por mensagem, sem exagero.
- Saudação inicial: UMA linha + 1 pergunta direta. Ex: "Oi! Aqui é da {tenant_name} 👋 Tá procurando pra comprar ou alugar?"
- Não repita o que o cliente disse ("entendi, você quer X…"). Vá direto pro próximo passo.
- Em vez de "qual sua faixa?", ancore: "tenho desde uns 300k até 1.5M nessa região, tem um teto em mente?"
- Resultado vazio: NÃO peça mais filtros. Mostre o mais próximo e pergunte se faz sentido ajustar.
- Sempre que descobrir uma preferência nova, chame `salvar_preferencias` em silêncio (sem anunciar). Inclua o `modo_detectado` e os campos da dimensão coberta naquele turno em `qualification`.
- NUNCA termine um turno só com `salvar_preferencias`. Sempre acompanhe de busca OU resposta em texto.
- Saudação ou mensagem vaga ("oi", "tudo bem?", "tô pensando em mudar"): apenas texto + 1 pergunta. NÃO chame tool — nada pra salvar ainda.
- SEMPRE escreva texto final pro cliente, mesmo após tools.
- Se cliente diz "tô só olhando" ou "não sei se vou comprar mesmo": registre em `qualification.urgencia.prazo=indefinido` — isso é frio.

LEAD VAGO ("queria ideias", "me mostra o que tem", "ainda não sei"):
Cubra a QMV mesmo assim — 3-4 turnos curtos pra ter perfil/uso, região e faixa de valor. Se ele insistir "qualquer coisa" sobre algum item, ancore com referência: "rodo entre 300k e 1.5M nessa região, dá um teto pra eu te trazer opções dentro do que faz sentido?". Se ainda assim recusar, faça uma busca ampla na faixa mediana (~600-900k) e na cidade mais provável, e use a reação aos imóveis pra qualificar o resto.

REGRAS CRÍTICAS (não violar):
- NUNCA escreva markup de tool no texto da resposta. As tools são acionadas pelo mecanismo da API — você não digita "<invoke name=…>". Cliente vê só texto natural.
- Depois de `detalhes_imovel`, SEMPRE escreva texto de retorno OBRIGATORIAMENTE — mesmo que tenha chamado outras tools no mesmo turno (`salvar_preferencias`, etc.). O texto deve conter: 1 destaque concreto do imóvel + se foi chamariz (1 foto) menção de "tem mais X fotos se quiser" + UMA pergunta natural pro próximo passo. Ficar mudo após detalhes_imovel quebra a conversa.
- Depois de `buscar_imoveis` que retornou 0-2 imóveis, MOSTRE no texto pelo menos 1 opção próxima (faixa um pouco diferente, bairro vizinho, tipologia parecida) ANTES de pedir refinamento. Não pergunte abstratamente "quer ampliar?" — mostre o que tem e pergunte "esse aqui em [bairro] por [preço] faz sentido ou prefere outra direção?".
- Condomínio ≠ bairro. "Condomínio Colinas", "Esplanada do Sol", "Wonder", "Life" → use o parâmetro `condominio`, NUNCA `bairro`.
- Pra fotos / "me fala mais" / "esse aí" / código de imóvel já apresentado → chame `detalhes_imovel`. Ela envia foto automaticamente. NUNCA diga "fotos não disponíveis" — apenas comente naturalmente. O sistema entrega a imagem.
- Negociação de preço, dúvida jurídica ou financiamento detalhado (taxa, parcela, simulação) → `transferir_corretor` direto.
- Se pedir corretor humano → `transferir_corretor` na hora.
- Região fora do Vale do Paraíba → informe gentilmente as cidades atendidas e ofereça alternativa próxima.
- Áudio do cliente → peça pra enviar por texto.

FORMATO PRA APRESENTAR IMÓVEIS (use sempre que listar):
🏠 *[titulo]*
📍 [bairro], [cidade]
🛏 [quartos] quartos | 📐 [area]m²
💰 R$ [preco formatado: 643.000 ou 1.800/mês pra locação]
Cód: [codigo]

Máximo 3 por mensagem. Ao final, UMA linha do tipo "Quer ver fotos de algum? Me diz o código." (varie, não repita igual).

EXEMPLOS DE TOM:
✅ "Boa! Casa em condomínio em SJC, separei 3 que combinam com seu perfil:"
✅ "No Colinas eu tenho 2 ativos agora. Olha:"
✅ "Pra alugar 2 quartos a gente tem uma faixa boa de 1.800 a 3.500. Tem um teto em mente?"
❌ "Olá! Que ótimo que você está procurando um imóvel! Posso te ajudar a encontrar o lar perfeito! Me conte: você quer comprar ou alugar? Qual cidade? Quantos quartos?"
❌ "Entendi, você quer um apartamento de 3 quartos em São José dos Campos com piscina e até 800 mil. Vou anotar suas preferências…"
"""


def _get_current_datetime() -> str:
    """Get current datetime formatted for Brazil."""
    now = datetime.now(BR_TZ)
    dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
            "sexta-feira", "sábado", "domingo"]
    dia_semana = dias[now.weekday()]
    return now.strftime(f"{dia_semana}, %d/%m/%Y às %H:%M")


class AIAgent:
    """Orchestrates Claude API with tool use for real estate conversations."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    async def process_message(
        self,
        conversation_history: list[dict],
        tenant_name: str,
        system_prompt: str | None = None,
        tool_handlers: dict[str, Any] | None = None,
    ) -> dict:
        """Process a message through Claude with tool use.

        Returns:
            dict with:
                - response: str (text to send to user)
                - tool_calls: list of tools that were called
                - tool_results: list of results from tool calls
                - images_to_send: list of image URLs to send after text
        """
        # Build system prompt with current datetime
        prompt = (system_prompt or DEFAULT_SYSTEM_PROMPT).format(
            tenant_name=tenant_name,
            current_datetime=_get_current_datetime(),
        )

        # Call Claude
        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                system=prompt,
                tools=TOOLS,
                messages=conversation_history,
            )
        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            return {
                "response": "Desculpe, estou com uma dificuldade técnica no momento. Um corretor entrará em contato em breve!",
                "tool_calls": [],
                "tool_results": [],
                "images_to_send": [],
            }

        # Process response - handle tool use loop
        tool_calls = []
        tool_results = []
        images_to_send = []
        final_text = ""

        current_response = response
        messages = list(conversation_history)

        while current_response.stop_reason == "tool_use":
            assistant_content = current_response.content
            messages.append({"role": "assistant", "content": assistant_content})

            tool_result_contents = []
            for block in assistant_content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input
                    tool_use_id = block.id

                    logger.info(f"Tool call: {tool_name} with input: {tool_input}")
                    tool_calls.append(
                        {"name": tool_name, "input": tool_input, "id": tool_use_id}
                    )

                    # Execute the tool handler
                    if tool_handlers and tool_name in tool_handlers:
                        try:
                            result = await tool_handlers[tool_name](tool_input)
                            tool_results.append(
                                {"name": tool_name, "result": result}
                            )

                            # Collect property photos. By default we only send
                            # 1 chamariz (so the customer's question stays as
                            # the last visible message in WhatsApp); the bot
                            # can request more by passing `quantidade_fotos` in
                            # detalhes_imovel when the customer asks for more
                            # images explicitly. Hard cap at 5 either way.
                            if tool_name == "detalhes_imovel" and isinstance(result, dict):
                                requested = tool_input.get("quantidade_fotos")
                                try:
                                    requested = int(requested) if requested is not None else 1
                                except (TypeError, ValueError):
                                    requested = 1
                                MAX_PHOTOS = max(0, min(requested, 5))
                                fotos = result.get("fotos") or []
                                titulo = result.get("titulo", "Imóvel")
                                codigo = result.get("codigo", "")
                                already = {img["url"] for img in images_to_send}
                                queued = 0
                                for candidate in fotos:
                                    if queued >= MAX_PHOTOS:
                                        break
                                    if not isinstance(candidate, str):
                                        continue
                                    if not candidate.startswith("http"):
                                        continue
                                    if "placeholder" in candidate:
                                        continue
                                    if candidate in already:
                                        continue
                                    images_to_send.append({
                                        "url": candidate,
                                        "caption": (
                                            f"{titulo} - Cód: {codigo}" if queued == 0 else ""
                                        ),
                                    })
                                    already.add(candidate)
                                    queued += 1

                                if queued == 0:
                                    fp = result.get("foto_principal") or ""
                                    if (
                                        isinstance(fp, str)
                                        and fp.startswith("http")
                                        and "placeholder" not in fp
                                        and fp not in already
                                    ):
                                        images_to_send.append({
                                            "url": fp,
                                            "caption": f"{titulo} - Cód: {codigo}",
                                        })
                                        queued = 1

                                if queued:
                                    logger.info(
                                        f"Queued {queued} photo(s) for property {codigo}"
                                    )
                                else:
                                    logger.warning(
                                        f"detalhes_imovel for {codigo} returned no usable photo URL"
                                    )

                        except Exception as e:
                            logger.error(f"Tool handler error for {tool_name}: {e}")
                            result = {"error": f"Erro ao executar {tool_name}: {str(e)}"}
                    else:
                        result = {"error": f"Tool {tool_name} não configurada"}

                    tool_result_contents.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )

            messages.append({"role": "user", "content": tool_result_contents})

            try:
                current_response = self.client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1024,
                    system=prompt,
                    tools=TOOLS,
                    messages=messages,
                )
            except anthropic.APIError as e:
                logger.error(f"Claude API error on tool result: {e}")
                break

        # Extract final text response
        for block in current_response.content:
            if hasattr(block, "text"):
                final_text += block.text

        # Strip any leaked tool-call markup before checking emptiness so
        # we fall through to the property-aware fallback if Claude only
        # emitted XML noise.
        sanitized = _sanitize_text(final_text)
        if sanitized != final_text:
            logger.warning(
                "Stripped leaked tool-call markup from Claude response"
            )
        final_text = sanitized

        # Meta rejects messages with empty `text.body` (error 100). If
        # Claude stopped after a tool call without saying anything (e.g.
        # after salvar_preferencias), give the user a short acknowledgement
        # so the turn doesn't look like the bot ghosted them.
        if not final_text.strip():
            called = [c["name"] for c in tool_calls]
            logger.warning(
                f"Claude returned no text after tools {called}; using fallback"
            )
            # Prefer a property-specific fallback when we just fetched details,
            # since the user is actively asking about a specific listing.
            details_result = next(
                (r["result"] for r in tool_results if r["name"] == "detalhes_imovel"),
                None,
            )
            if isinstance(details_result, dict) and details_result.get("titulo"):
                titulo = details_result.get("titulo", "")
                bairro = details_result.get("bairro", "")
                preco = details_result.get("preco")
                preco_str = f"R$ {preco:,.0f}".replace(",", ".") if isinstance(preco, (int, float)) and preco else ""
                pieces = [p for p in [titulo, bairro, preco_str] if p]

                # Inspect what we just queued: 1 photo means the chamariz
                # turn — invite the customer to ask for more. >1 means they
                # already asked for more, so close with a next-step question.
                photos_this_turn = sum(
                    1 for img in images_to_send
                    if (titulo and titulo in (img.get("caption") or ""))
                    or not img.get("caption")
                )
                gallery = details_result.get("fotos") or []
                remaining = max(0, len(gallery) - photos_this_turn)

                if photos_this_turn <= 1 and remaining >= 2:
                    suffix = (
                        f". Te mandei a foto chamariz aqui — tenho mais "
                        f"{remaining} fotos do imóvel se quiser ver. Curtiu?"
                    )
                else:
                    suffix = ". Curtiu? Quer agendar uma visita ou saber mais sobre o condomínio?"

                final_text = " — ".join(pieces) + suffix
            elif "detalhes_imovel" in called:
                final_text = "Te mando os detalhes aqui! Algo que queira saber especificamente? 😊"
            elif "buscar_imoveis" in called:
                final_text = "Achei algumas opções aqui — me diz se faz sentido pra você ou se quer ajustar algum critério!"
            elif "salvar_preferencias" in called:
                # Should be rare now: prompt forbids salvar_preferencias alone.
                final_text = "Beleza! Conta mais um pouquinho pra eu te mostrar boas opções."
            else:
                final_text = "Só um instante! 😊"

        return {
            "response": final_text,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "images_to_send": images_to_send,
        }
