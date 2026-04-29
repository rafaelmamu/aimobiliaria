"""
Run scripted conversation scenarios against the AI agent and print transcripts.

Usage:
    ANTHROPIC_API_KEY=... python -m scripts.test_scenarios [scenario_letter]

Without an argument, runs all scenarios A–H. With "A", runs just scenario A.
"""

import asyncio
import os
import re
import sys
from functools import partial

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ai_agent import AIAgent
from app.services.property_api import MockPropertyAPIClient
from app.tools.search_properties import handle_search_properties
from app.tools.get_property_details import handle_get_property_details
from app.tools.schedule_visit import handle_schedule_visit
from app.tools.transfer_broker import handle_transfer_broker
from app.tools.save_preferences import handle_save_preferences


async def _stub_cancel_visit(params: dict) -> dict:
    imovel = params.get("imovel_id") or params.get("protocolo") or "—"
    return {"success": True, "imovel_codigo": imovel, "message": f"[stub] cancelada {imovel}"}


SCENARIOS = {
    "A": {
        "title": "Lead direto: apto SJC 3 dorms até 800k",
        "turns": [
            "oi",
            "quero comprar um apartamento em São José dos Campos, 3 dormitórios, até 800 mil",
        ],
    },
    "B": {
        "title": "Lead vago: 'tô pensando em mudar'",
        "turns": [
            "oi tudo bem? tô pensando em mudar",
            "ainda não sei direito, queria umas ideias",
        ],
    },
    "C": {
        "title": "Lead por condomínio (Colinas)",
        "turns": [
            "oi! tem algo no Condomínio Colinas?",
        ],
    },
    "D": {
        "title": "Locação 2 quartos",
        "turns": [
            "queria alugar um apartamento de 2 quartos",
            "São José dos Campos",
        ],
    },
    "E": {
        "title": "Interesse → fotos → visita",
        "turns": [
            "tem apto pra venda em SJC até 500k?",
            "manda foto do primeiro",
            "quero visitar esse, sábado de manhã pode?",
        ],
    },
    "F": {
        "title": "Negociação / financiamento detalhado",
        "turns": [
            "tem apto à venda no Aquarius?",
            "qual a parcela do financiamento desse de 850k em 360 meses?",
        ],
    },
    "G": {
        "title": "Fora de área (Campinas)",
        "turns": [
            "vocês tem imóveis em Campinas?",
        ],
    },
    "H": {
        "title": "Continuação: 'e o primeiro?'",
        "turns": [
            "quero ver apartamentos em SJC pra venda",
            "me fala mais sobre o primeiro",
        ],
    },
    # ─── Cenários novos: QMV + Modo 1/2 + foto progressiva ───
    "I": {
        "title": "Modo 1 longo — abertura rica deve adiar busca",
        "turns": [
            (
                "Boa tarde! Estamos pensando em sair do aluguel. Eu e minha "
                "esposa moramos hoje no Jardim Aquarius, temos uma filha de 5 "
                "anos. Trabalho de casa três dias por semana, ela trabalha no "
                "centro. Queríamos um apto de 3 quartos, em condomínio com "
                "lazer pra criança. Ainda estamos pesquisando bairros e "
                "tipologia, valor a gente vê depois."
            ),
            "podemos olhar opções entre 700 e 900",
            "preciso falar com minha esposa sobre bairro, ela quer ficar perto da escola",
        ],
    },
    "J": {
        "title": "QMV — bot deve pedir teto antes de buscar",
        "turns": [
            "oi",
            "tô procurando pra comprar",
            "apartamento, 3 quartos",
            "São José dos Campos",
        ],
    },
    "K": {
        "title": "Foto progressiva — 1 chamariz, mais fotos sob pedido",
        "turns": [
            "quero apto pra venda em SJC, 3 quartos, até 700k",
            "manda foto do primeiro",
            "tem mais foto?",
        ],
    },
    "L": {
        "title": "Modo 1 curto rico — contexto disparando Modo 1",
        "turns": [
            "oi",
            "casei recentemente, queria sair do aluguel e comprar nosso primeiro apto",
            "ainda não pensamos no orçamento, queria entender as opções",
        ],
    },
    # ─── Cenários para os bugs reportados na conversa do dono ───
    "M": {
        "title": "Filtro de preço — props sem preço NÃO devem aparecer com teto",
        "turns": [
            "quero apto pra venda em SJC, 3 quartos, até 650k",
        ],
    },
    "N": {
        "title": "Identificação por nome — bot deve usar código correto da última lista",
        "turns": [
            "quero apto pra venda em SJC até 700k",
            "manda fotos do Wonder",
        ],
    },
    "O": {
        "title": "Qualification preenchido — dimensões devem aparecer no profile",
        "turns": [
            "casei recentemente, somos eu e minha esposa, ela trabalha no centro de SJC e eu de casa",
            "preciso de 3 quartos com suíte e varanda gourmet seria ótimo",
            "vou financiar usando FGTS, teto uns 700k",
        ],
    },
}


def _strip_markdown(text: str) -> str:
    return re.sub(r"[*_`]", "", text)


def _count_questions(text: str) -> int:
    return text.count("?")


async def run_scenario(letter: str, scenario: dict, agent: AIAgent, tool_handlers: dict):
    print("=" * 70)
    print(f"Cenário {letter}: {scenario['title']}")
    print("=" * 70)

    history: list[dict] = []
    metrics = {
        "questions_before_first_search": 0,
        "first_search_turn": None,
        "tools_called": [],
        "turns": 0,
        "photos_per_turn": [],
        "modo_observed": [],
        "qualification_dims_seen": set(),  # dimensions populated across turns
        "search_filters": [],              # filters passed to buscar_imoveis
        "details_ids": [],                 # imovel_id passed to detalhes_imovel
    }
    searched = False

    for turn_idx, user_msg in enumerate(scenario["turns"], 1):
        history.append({"role": "user", "content": user_msg})
        print(f"\n👤 [{turn_idx}] {user_msg}")

        result = await agent.process_message(
            conversation_history=history,
            tenant_name="Upside Imóveis Exclusivos",
            tool_handlers=tool_handlers,
        )

        response = result["response"]
        history.append({"role": "assistant", "content": response})

        for tc in result["tool_calls"]:
            metrics["tools_called"].append(tc["name"])
            if tc["name"] == "buscar_imoveis":
                metrics["search_filters"].append(tc["input"])
                if not searched:
                    searched = True
                    metrics["first_search_turn"] = turn_idx
            if tc["name"] == "salvar_preferencias":
                modo = tc["input"].get("modo_detectado")
                if modo:
                    metrics["modo_observed"].append(modo)
                qual = tc["input"].get("qualification") or {}
                for dim_name, dim_data in qual.items():
                    if dim_name == "_meta":
                        continue
                    if isinstance(dim_data, dict) and any(
                        v is not None and v != [] and v != {}
                        for v in dim_data.values()
                    ):
                        metrics["qualification_dims_seen"].add(dim_name)
            if tc["name"] == "detalhes_imovel":
                metrics["details_ids"].append(tc["input"].get("imovel_id"))

        if not searched:
            metrics["questions_before_first_search"] += _count_questions(response)

        n_photos = len(result["images_to_send"])
        metrics["photos_per_turn"].append(n_photos)

        if result["tool_calls"]:
            print(f"   🔧 {', '.join(t['name'] for t in result['tool_calls'])}")
        print(f"🤖 {response}")
        if n_photos:
            print(f"   📸 imagens enfileiradas: {n_photos}")

    metrics["turns"] = len(scenario["turns"])
    print(
        f"\n📊 Métricas: 1ª busca @ turno {metrics['first_search_turn']}, "
        f"fotos/turno = {metrics['photos_per_turn']}, "
        f"modo = {metrics['modo_observed']}, "
        f"dimensões coletadas = {sorted(metrics['qualification_dims_seen']) or '∅'}, "
        f"detalhes_imovel ids = {metrics['details_ids']}, "
        f"buscar filtros = {metrics['search_filters']}"
    )
    return metrics


async def main():
    sys.stdout.reconfigure(encoding="utf-8")  # for Windows cp1252 consoles
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[fail] ANTHROPIC_API_KEY nao setada")
        return

    only = sys.argv[1].upper() if len(sys.argv) > 1 else None

    agent = AIAgent()
    property_client = MockPropertyAPIClient(base_url="http://mock")

    # The fixture (scripts/fixtures/upside_properties.json) is a /peek dump
    # that lacks `quartos`, `area_privativa`, `descricao` and `fotos`. Prepend
    # the original inline mocks (which have full fields) so test scenarios
    # like "3 quartos até 700k" actually return results and we can verify
    # photo pacing with multiple gallery URLs.
    SYNTHETIC = [
        "https://images.example.com/foto2.jpg",
        "https://images.example.com/foto3.jpg",
        "https://images.example.com/foto4.jpg",
        "https://images.example.com/foto5.jpg",
    ]
    inline = [dict(p) for p in MockPropertyAPIClient.MOCK_PROPERTIES]
    for prop in inline:
        if not prop.get("fotos"):
            principal = prop.get("foto_principal") or ""
            prop["fotos"] = ([principal] if principal else []) + SYNTHETIC

    property_client.MOCK_PROPERTIES = inline + list(property_client.MOCK_PROPERTIES)
    print(
        f"📂 catálogo: {len(inline)} inline (com fotos+quartos) + "
        f"{len(property_client.MOCK_PROPERTIES) - len(inline)} fixture\n"
    )

    tool_handlers = {
        "buscar_imoveis": partial(handle_search_properties, property_client=property_client),
        "detalhes_imovel": partial(handle_get_property_details, property_client=property_client),
        "agendar_visita": handle_schedule_visit,
        "transferir_corretor": handle_transfer_broker,
        "salvar_preferencias": handle_save_preferences,
        "cancelar_visita": _stub_cancel_visit,
    }

    results = {}
    for letter, scenario in SCENARIOS.items():
        if only and letter != only:
            continue
        results[letter] = await run_scenario(letter, scenario, agent, tool_handlers)
        print()

    print("=" * 70)
    print("RESUMO")
    print("=" * 70)
    for letter, m in results.items():
        dims = sorted(m["qualification_dims_seen"]) or "∅"
        print(
            f"  {letter}: 1ª busca @ {m['first_search_turn']}, "
            f"fotos={m['photos_per_turn']}, "
            f"modo={m['modo_observed']}, "
            f"dims={dims}, "
            f"details={m['details_ids']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
