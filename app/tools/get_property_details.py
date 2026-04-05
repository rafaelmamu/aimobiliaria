import logging

from app.services.property_api import PropertyAPIClient

logger = logging.getLogger(__name__)


async def handle_get_property_details(
    params: dict, property_client: PropertyAPIClient
) -> dict:
    """Handle the detalhes_imovel tool call from Claude."""
    imovel_id = params.get("imovel_id", "")
    logger.info(f"Getting details for property: {imovel_id}")

    details = await property_client.get_property_details(imovel_id)

    if not details:
        return {
            "error": f"Imóvel com código {imovel_id} não encontrado.",
        }

    return {
        "codigo": details["id"],
        "titulo": details["titulo"],
        "tipo": details["tipo"],
        "transacao": details["transacao"],
        "bairro": details["bairro"],
        "cidade": details["cidade"],
        "quartos": details["quartos"],
        "suites": details.get("suites", 0),
        "banheiros": details.get("banheiros", 0),
        "vagas": details.get("vagas", 0),
        "area_privativa_m2": details["area_privativa"],
        "area_total_m2": details.get("area_total", 0),
        "preco": details["preco"],
        "condominio": details.get("condominio", 0),
        "iptu": details.get("iptu", 0),
        "descricao": details.get("descricao", ""),
        "caracteristicas": details.get("caracteristicas", []),
        "lazer": details.get("lazer", []),
        "aceita_financiamento": details.get("aceita_financiamento"),
        "foto_principal": details.get("foto_principal", ""),
        "fotos": details.get("fotos", [])[:5],
        "url": details.get("url", ""),
    }
