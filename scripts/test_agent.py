"""
Test the AI agent locally via terminal — no WhatsApp needed.

Usage:
    python -m scripts.test_agent

This simulates a conversation with the Claude agent using mock property data.
Great for testing the system prompt, tool calls, and conversation flow.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ai_agent import AIAgent
from app.services.property_api import MockPropertyAPIClient
from app.tools.search_properties import handle_search_properties
from app.tools.get_property_details import handle_get_property_details
from app.tools.schedule_visit import handle_schedule_visit
from app.tools.transfer_broker import handle_transfer_broker
from functools import partial


async def main():
    # Check for API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY não encontrada!")
        print("   Defina: export ANTHROPIC_API_KEY=sk-ant-...")
        return

    print("=" * 60)
    print("🏠 AImobiliarIA — Teste Local do Agente")
    print("=" * 60)
    print()
    print("Converse com o agente como se fosse um lead no WhatsApp.")
    print("Os imóveis são dados mock da Upside Imóveis.")
    print("Digite 'sair' para encerrar.")
    print()
    print("-" * 60)

    agent = AIAgent()
    property_client = MockPropertyAPIClient(base_url="http://mock")

    # Tool handlers with mock client
    tool_handlers = {
        "buscar_imoveis": partial(
            handle_search_properties, property_client=property_client
        ),
        "detalhes_imovel": partial(
            handle_get_property_details, property_client=property_client
        ),
        "agendar_visita": handle_schedule_visit,
        "transferir_corretor": handle_transfer_broker,
    }

    conversation_history = []

    while True:
        try:
            user_input = input("\n👤 Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n👋 Até mais!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("sair", "exit", "quit"):
            print("\n👋 Até mais!")
            break

        conversation_history.append({"role": "user", "content": user_input})

        print("\n⏳ Processando...", end="", flush=True)

        result = await agent.process_message(
            conversation_history=conversation_history,
            tenant_name="Upside Imóveis Exclusivos",
            tool_handlers=tool_handlers,
        )

        print("\r" + " " * 20 + "\r", end="")  # Clear "Processando..."

        response = result["response"]
        conversation_history.append({"role": "assistant", "content": response})

        # Show tool calls if any
        if result["tool_calls"]:
            print(f"   🔧 Tools usadas: {', '.join(t['name'] for t in result['tool_calls'])}")

        print(f"\n🤖 Agente: {response}")


if __name__ == "__main__":
    asyncio.run(main())
