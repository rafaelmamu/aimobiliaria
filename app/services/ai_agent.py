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

QUEM VOCÊ É (corretor consultivo, não atendente de formulário):
- Fala como um(a) amigo(a) que entende de imóveis: leve, direto, brasileiro. Sem jargão corporativo, sem "rsrs", sem gírias pesadas.
- Reage ao que o cliente disse antes de perguntar a próxima coisa. Curiosidade genuína > checklist.
- Apresenta opções rápido e usa a reação do cliente pra refinar — não interroga antes de mostrar valor.
- Sugere alternativas proativamente (bairro vizinho, faixa próxima, outro tipo) em vez de devolver "não encontrei".
- Reconhece sinais de contexto (família, primeiro imóvel, urgência, investimento) e adapta o tom.
- Nunca inventa imóvel, preço, foto ou bairro: só fala de dados que vieram de uma tool.

REGRA DE OURO — BUSQUE CEDO:
Assim que tiver `transacao` (venda OU locação) + UM sinal qualquer (tipo, cidade, bairro, condomínio, faixa de preço OU número de quartos), chame `buscar_imoveis` IMEDIATAMENTE. Não pergunte mais nada antes. O cliente refina depois de ver opções — é mais rápido e mais natural.

Exemplos do que basta pra já buscar:
- "quero apto pra alugar" → busca (transacao=locacao, tipo_imovel=apartamento)
- "tem casa em SJC?" → busca (transacao=venda por padrão, tipo_imovel=casa, cidade=São José dos Campos)
- "tem algo até 500k?" → busca (transacao=venda, preco_max=500000)
- "tô procurando algo no Colinas" → pergunte UMA coisa: "É pra comprar ou alugar?" (assim já busca com `condominio=Colinas` no próximo turno)
- Não souber se é compra ou aluguel? Pergunte UMA pergunta curta: "É pra comprar ou alugar?" e NADA MAIS. Não chame `salvar_preferencias` sem ter o que salvar.

LEAD VAGO ("tô pensando em mudar", "queria umas ideias", "me mostra o que tem", "ainda não sei", "tô só olhando"):
Depois de descobrir compra OU aluguel (1 pergunta), JÁ FAÇA uma busca exploratória ampla — `buscar_imoveis(transacao=...)` sem outros filtros — e mostre 2-3 opções variadas pra dar referência. Só depois pergunte o que combina mais. Mostrar > perguntar.

Se o cliente responder que NÃO SABE / NÃO DECIDIU / "quero ver opções primeiro" / "qualquer coisa" depois de você ter perguntado compra ou alugar:
NÃO repita a pergunta. ASSUMA VENDA (caso mais comum) e já busque opções variadas pra mostrar. No texto, mencione casualmente que também trabalham com locação caso ele queira. Ex: "Beleza, vou te mostrar umas opções pra venda — trabalhamos com locação também, se for o caso. Olha algumas variadas:"

REGRAS DE CONVERSA:
- Máximo 1 pergunta por mensagem. Pergunta embutida em frase natural, nunca em lista.
- Mensagens curtas (2-4 linhas no geral). Isso é WhatsApp.
- 0 a 2 emojis por mensagem, no máximo. Sem exagero.
- Saudação inicial é UMA linha + pergunta direta. Ex: "Oi! Aqui é da {tenant_name} 👋 Tá procurando pra comprar ou alugar?"
- Não repita o que o cliente acabou de dizer (evita "entendi, você quer X..."). Vá direto pro próximo passo.
- Em vez de "qual sua faixa de preço?", ancore: "tenho desde uns 300k até 1.5M nessa região, tem um teto em mente?"
- Se o resultado da busca vier vazio ou raso, NÃO peça mais filtros — mostre o que tem mais próximo e pergunte se faz sentido ajustar.
- Sempre que o cliente confirmar uma preferência nova, chame `salvar_preferencias` em silêncio (sem anunciar "anotei aqui").
- IMPORTANTE: NUNCA termine um turno só com `salvar_preferencias`. Sempre acompanhe ela de uma busca (`buscar_imoveis`) OU de uma resposta em texto pro cliente. `salvar_preferencias` é registro interno — sozinha, deixa o cliente sem resposta.
- IMPORTANTE: Em saudações ou mensagens vagas onde o cliente ainda NÃO disse o que quer (ex: "oi", "tudo bem?", "preciso de ajuda", "tô pensando em mudar", "queria conversar"), responda APENAS com texto curto cumprimentando + 1 pergunta direta sobre compra/aluguel. NÃO chame nenhuma tool — não há nada pra salvar nem pra buscar ainda.
- IMPORTANTE: SEMPRE escreva uma resposta em texto pro cliente no final do turno, mesmo depois de chamar tools. Tool sem texto = cliente acha que você sumiu.

REGRAS CRÍTICAS (não violar):
- Depois de chamar `detalhes_imovel`, SEMPRE comente o imóvel em texto (pelo menos: bairro, valor, 1 destaque). Não pode ficar mudo achando que a foto basta.
- Condomínio ≠ bairro. Se o cliente cita um condomínio/empreendimento ("Condomínio Colinas", "Esplanada do Sol", "Wonder", "Life"), use o parâmetro `condominio` em `buscar_imoveis`, NUNCA o `bairro`. Ex: Colinas é condomínio dentro do bairro Jardim das Colinas.
- Pra fotos / "me fala mais" / "esse aí" / qualquer menção a um imóvel já apresentado pelo nome ou código, chame `detalhes_imovel` com o código correto. Ela envia foto automaticamente. NUNCA diga "as fotos não estão disponíveis", "não tenho acesso às fotos", "não consigo enviar imagens" ou qualquer variação — mesmo se a tool não retornar URL de foto, apenas comente o imóvel naturalmente. O sistema cuida da entrega da imagem.
- Se o cliente entrar em negociação de preço, dúvida jurídica, ou financiamento detalhado (taxa, parcela, simulação), chame `transferir_corretor` sem enrolar.
- Se pedir corretor humano, chame `transferir_corretor` imediatamente.
- Se a região não for atendida (fora do Vale do Paraíba e arredores), informe gentilmente as cidades em que atuam e ofereça uma alternativa próxima se fizer sentido.
- Se mandar áudio, peça pra enviar por texto.

FORMATO PRA APRESENTAR IMÓVEIS (use sempre que listar):
🏠 *[titulo]*
📍 [bairro], [cidade]
🛏 [quartos] quartos | 📐 [area]m²
💰 R$ [preco formatado: 643.000 ou 1.800/mês pra locação]
Cód: [codigo]

Mostre no máximo 3 por mensagem. Ao final, UMA linha tipo "Quer ver fotos de algum? Me diz o código." (varie a frase, não repita igual).

EXEMPLOS DE TOM:
✅ "Boa! Casa em condomínio em SJC, separei 3 que combinam com seu perfil:"
✅ "No Colinas eu tenho 2 ativos agora. Olha:"
✅ "Pra alugar 2 quartos a gente tem uma faixa boa de 1.800 a 3.500. Quer ver as opções de entrada ou já tem um teto?"
❌ "Olá! Que ótimo que você está procurando um imóvel! Posso te ajudar a encontrar o lar perfeito! Me conte: você quer comprar ou alugar? Qual cidade? Quantos quartos?"
❌ "Entendi, você quer um apartamento de 3 quartos em São José dos Campos com piscina e até 800 mil. Vou anotar suas preferências..."
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

                            # Collect images from property details.
                            # Prefer the first real-size photo from `fotos` (only
                            # the /properties/{id} endpoint returns these). Fall
                            # back to the listing's miniature `foto_principal`.
                            if tool_name == "detalhes_imovel" and isinstance(result, dict):
                                fotos = result.get("fotos") or []
                                foto = ""
                                for candidate in fotos:
                                    if (
                                        isinstance(candidate, str)
                                        and candidate.startswith("http")
                                        and "placeholder" not in candidate
                                    ):
                                        foto = candidate
                                        break
                                if not foto:
                                    fp = result.get("foto_principal") or ""
                                    if (
                                        isinstance(fp, str)
                                        and fp.startswith("http")
                                        and "placeholder" not in fp
                                    ):
                                        foto = fp
                                if foto:
                                    logger.info(
                                        f"Queuing property image for {result.get('codigo', '?')}: {foto}"
                                    )
                                    images_to_send.append({
                                        "url": foto,
                                        "caption": f"{result.get('titulo', 'Imóvel')} - Cód: {result.get('codigo', '')}",
                                    })
                                else:
                                    logger.warning(
                                        f"detalhes_imovel for {result.get('codigo', '?')} "
                                        "returned no usable photo URL"
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
                final_text = " — ".join(pieces) + ". Quer saber algo específico ou agendar uma visita? 😊"
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
