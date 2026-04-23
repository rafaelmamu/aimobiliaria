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
            if tc["name"] == "buscar_imoveis" and not searched:
                searched = True
                metrics["first_search_turn"] = turn_idx

        if not searched:
            metrics["questions_before_first_search"] += _count_questions(response)

        if result["tool_calls"]:
            print(f"   🔧 {', '.join(t['name'] for t in result['tool_calls'])}")
        print(f"🤖 {response}")
        if result["images_to_send"]:
            print(f"   📸 imagens: {len(result['images_to_send'])}")

    metrics["turns"] = len(scenario["turns"])
    print(f"\n📊 Métricas: perguntas até 1ª busca = {metrics['questions_before_first_search']}, "
          f"1ª busca no turno = {metrics['first_search_turn']}, "
          f"tools = {metrics['tools_called']}")
    return metrics


async def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("❌ ANTHROPIC_API_KEY não setada")
        return

    only = sys.argv[1].upper() if len(sys.argv) > 1 else None

    agent = AIAgent()
    property_client = MockPropertyAPIClient(base_url="http://mock")
    print(f"📂 catálogo carregado: {len(property_client.MOCK_PROPERTIES)} imóveis\n")

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
        print(f"  {letter}: 1ª busca @ turno {m['first_search_turn']}, "
              f"{m['questions_before_first_search']} perguntas antes, "
              f"tools={m['tools_called']}")


if __name__ == "__main__":
    asyncio.run(main())
