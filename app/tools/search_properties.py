import logging

from app.services.property_api import PropertyAPIClient

logger = logging.getLogger(__name__)


async def handle_search_properties(
    params: dict, property_client: PropertyAPIClient
) -> dict:
    """Handle the buscar_imoveis tool call from Claude.

    Searches the real estate API and returns formatted results.
    """
    logger.info(f"Searching properties with params: {params}")

    results = await property_client.search_properties(params)
    logger.info(f"Search returned {len(results)} properties for params: {params}")

    if not results:
        return {
            "found": 0,
            "message": "Nenhum imóvel encontrado com esses critérios.",
            "suggestion": "Tente ampliar a busca: aumente a faixa de preço, mude o bairro ou reduza o número mínimo de quartos.",
        }

    # Format results for Claude to present naturally
    formatted = []
    for prop in results:
        formatted.append(
            {
                "codigo": prop["id"],
                "titulo": prop["titulo"],
                "tipo": prop["tipo"],
                "bairro": prop["bairro"],
                "cidade": prop["cidade"],
                "quartos": prop["quartos"],
                "suites": prop.get("suites", 0),
                "area_m2": prop["area_privativa"],
                "preco": prop["preco"],
                "vagas": prop.get("vagas", 0),
                "url": prop.get("url", ""),
            }
        )

    return {
        "found": len(formatted),
        "imoveis": formatted,
    }
