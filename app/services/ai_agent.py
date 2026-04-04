import json
import logging
from typing import Any

import anthropic

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

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
            "Obtém detalhes completos de um imóvel específico pelo código. "
            "Use quando o cliente pedir mais informações sobre um imóvel apresentado."
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
            "ou quando a situação exigir atendimento humano."
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
]

# ─────────────────────────────────────────────
# Default System Prompt
# ─────────────────────────────────────────────

DEFAULT_SYSTEM_PROMPT = """Você é o assistente virtual da {tenant_name}, especialista em ajudar clientes a encontrar o imóvel ideal.

PERSONALIDADE:
- Cordial, profissional e acolhedor
- Linguagem natural e brasileira (nunca robótica)
- Objetivo: entender a necessidade e apresentar opções relevantes
- Nunca invente informações — só apresente dados reais retornados pelas tools

FLUXO DE ATENDIMENTO:
1. Cumprimente de forma breve e pergunte como pode ajudar
2. Descubra: compra ou aluguel?
3. Descubra gradualmente (sem bombardear perguntas):
   - Tipo de imóvel (apartamento, casa, terreno, etc)
   - Região/bairro de interesse
   - Número de quartos
   - Faixa de preço
   - Outras preferências relevantes
4. Quando tiver informações suficientes, use a tool buscar_imoveis
5. Apresente as opções de forma resumida e atraente
6. Se houver interesse, ofereça detalhes ou agende visita
7. Se não encontrar opções, sugira ajustar critérios

REGRAS IMPORTANTES:
- Máximo 2-3 perguntas por mensagem
- Respostas CURTAS — isso é WhatsApp, não email
- Use emojis com moderação (1-2 por mensagem no máximo)
- Ao apresentar imóveis: nome, bairro, quartos, área, preço
- Sempre pergunte se quer ver mais ou outras opções
- Se pedir corretor humano, use a tool transferir_corretor imediatamente
- Nunca pressione o cliente — seja consultivo
- Se o cliente mandar mensagem fora do contexto imobiliário, redirecione gentilmente

FORMATO DE APRESENTAÇÃO DE IMÓVEIS:
🏠 *{nome}*
📍 {bairro}, {cidade}
🛏 {quartos} quartos | 📐 {area}m²
💰 R$ {preco}
Cód: {codigo}
"""


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

        Args:
            conversation_history: List of {"role": "user"|"assistant", "content": "..."}
            tenant_name: Name of the real estate agency
            system_prompt: Custom system prompt (or uses default)
            tool_handlers: Dict mapping tool names to handler functions

        Returns:
            dict with:
                - response: str (text to send to user)
                - tool_calls: list of tools that were called
                - tool_results: list of results from tool calls
        """
        # Build system prompt
        prompt = (system_prompt or DEFAULT_SYSTEM_PROMPT).format(
            tenant_name=tenant_name
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
            }

        # Process response - handle tool use loop
        tool_calls = []
        tool_results = []
        final_text = ""

        # Claude might need multiple rounds of tool use
        current_response = response
        messages = list(conversation_history)

        while current_response.stop_reason == "tool_use":
            # Collect all content blocks
            assistant_content = current_response.content
            messages.append({"role": "assistant", "content": assistant_content})

            # Process each tool use block
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

            # Send tool results back to Claude
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
        }
