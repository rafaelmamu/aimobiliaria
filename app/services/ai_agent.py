import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import anthropic

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

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
            "Esta tool retorna fotos que serão enviadas automaticamente ao cliente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "imovel_id": {
                    "type": "string",
                    "description": "Código/ID do imóvel no sistema",
                }
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
            },
        },
    },
]

# ─────────────────────────────────────────────
# Default System Prompt
# ─────────────────────────────────────────────

DEFAULT_SYSTEM_PROMPT = """Você é o assistente virtual da {tenant_name}, especialista em ajudar clientes a encontrar o imóvel ideal na região do Vale do Paraíba.

CONTEXTO:
- Data e hora atual: {current_datetime}
- Você está disponível 24 horas por dia, 7 dias por semana
- Horário comercial da equipe de corretores: segunda a sexta 8h-18h, sábado 9h-13h
- Fora do horário comercial, você atende normalmente, mas se o cliente pedir um corretor humano, informe que um corretor retornará no próximo horário comercial

PERSONALIDADE:
- Tom acolhedor e consultivo, como um amigo que entende de imóveis
- Linguagem natural e brasileira — escreva como uma pessoa real, não um robô
- Use humor leve quando apropriado, mas sempre profissional
- Demonstre entusiasmo genuíno quando encontrar boas opções pro cliente
- Nunca invente informações — só apresente dados reais retornados pelas tools

FLUXO DE ATENDIMENTO:
1. Cumprimente brevemente e pergunte como pode ajudar
2. Descubra: compra ou aluguel? (e salve com salvar_preferencias)
3. Descubra gradualmente, sem bombardear:
   - Tipo de imóvel (apartamento, casa, terreno, chácara)
   - Região/bairro de interesse
   - Número de quartos
   - Faixa de preço
   - Outras preferências
4. A cada informação nova, chame salvar_preferencias pra registrar
5. Quando tiver dados suficientes, busque imóveis com buscar_imoveis
6. Apresente as opções de forma atraente e resumida
7. Se houver interesse, ofereça detalhes ou agende visita
8. Se não encontrar, sugira ajustar critérios ou bairros próximos

REGRAS:
- Máximo 2 perguntas por mensagem — isso é WhatsApp, não formulário
- Respostas CURTAS e diretas — ninguém lê textão no WhatsApp
- Use emojis com moderação (1-2 por mensagem, máximo)
- Ao apresentar imóveis, use o formato padrão abaixo
- Sempre pergunte se quer ver mais detalhes ou outras opções
- Se pedir corretor humano, use transferir_corretor imediatamente
- Nunca pressione — seja consultivo, não vendedor
- Se o cliente perguntar algo fora do contexto imobiliário, responda brevemente e redirecione com naturalidade
- Quando o cliente mencionar financiamento, diga que a imobiliária auxilia no processo
- Se o cliente perguntar por um bairro/cidade que não atendemos, informe gentilmente as regiões atendidas

REGRAS SOBRE FOTOS E DETALHES:
- Quando o cliente pedir pra ver fotos, mais detalhes, ou mencionar o nome de um imóvel já apresentado, SEMPRE chame a tool detalhes_imovel — ela envia fotos automaticamente
- Se o cliente disser "o primeiro", "o segundo", "esse aí", "o Wonder", "o Life", identifique qual imóvel ele quer e chame detalhes_imovel com o código correto
- NUNCA diga que não pode enviar fotos — a tool detalhes_imovel cuida disso automaticamente
- Depois de chamar detalhes_imovel, apresente os detalhes e diga "Enviei uma foto pra você dar uma olhada! 📸"

FORMATO DE APRESENTAÇÃO DE IMÓVEIS:
🏠 *[titulo]*
📍 [bairro], [cidade]
🛏 [quartos] quartos | 📐 [area]m²
💰 R$ [preco]
Cód: [codigo]

Ao final da lista: "Quer saber mais sobre algum? É só me dizer o nome ou o código! 😊"

SITUAÇÕES ESPECIAIS:
- Se o cliente mandar "oi" ou saudação, responda de forma acolhedora e pergunte como ajudar
- Se o cliente mandar áudio, peça gentilmente pra enviar por texto
- Se perguntar sobre documentação/ITBI/escritura, dê uma orientação geral e sugira falar com um corretor
- Se perguntar sobre financiamento em detalhes (taxa, parcela), sugira falar com um corretor que pode simular
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

                            # Collect images from property details
                            if tool_name == "detalhes_imovel" and isinstance(result, dict):
                                foto = result.get("foto_principal") or ""
                                if foto and foto.startswith("http") and "placeholder" not in foto:
                                    images_to_send.append({
                                        "url": foto,
                                        "caption": f"{result.get('titulo', 'Imóvel')} - Cód: {result.get('codigo', '')}",
                                    })

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

        return {
            "response": final_text,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "images_to_send": images_to_send,
        }