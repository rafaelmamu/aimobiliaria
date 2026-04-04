import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class PropertyAPIClient:
    """Client for the real estate agency's property API.

    This is designed to be tenant-agnostic. Each tenant provides
    their own API base URL and credentials.

    NOTE: The actual API endpoints and response format will need to be
    adjusted once the Upside Imóveis API documentation is available.
    The current implementation uses a reasonable assumed structure
    based on common Brazilian real estate API patterns.
    """

    def __init__(self, base_url: str, api_key: str | None = None, config: dict = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.config = config or {}
        self.headers = {"Accept": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

    async def search_properties(self, filters: dict) -> list[dict]:
        """Search properties with the given filters.

        Args:
            filters: Dict with keys like transacao, tipo_imovel, cidade,
                     bairro, quartos_min, preco_min, preco_max, area_min

        Returns:
            List of property dicts with standardized fields.
        """
        # Build query params from filters
        params = {}
        field_mapping = {
            "transacao": "transacao",
            "tipo_imovel": "tipo",
            "cidade": "cidade",
            "bairro": "bairro",
            "quartos_min": "quartos_min",
            "preco_min": "preco_min",
            "preco_max": "preco_max",
            "area_min": "area_min",
        }

        for our_field, api_field in field_mapping.items():
            if our_field in filters and filters[our_field] is not None:
                params[api_field] = filters[our_field]

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self.base_url}/api/imoveis",
                    params=params,
                    headers=self.headers,
                )
                response.raise_for_status()
                data = response.json()

                # Normalize response to our standard format
                properties = data if isinstance(data, list) else data.get("results", data.get("data", []))
                return [self._normalize_property(p) for p in properties[:10]]  # Max 10 results

        except httpx.HTTPError as e:
            logger.error(f"Property API error: {e}")
            return []

    async def get_property_details(self, property_id: str) -> dict | None:
        """Get detailed information about a specific property."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self.base_url}/api/imoveis/{property_id}",
                    headers=self.headers,
                )
                response.raise_for_status()
                data = response.json()
                return self._normalize_property_details(data)

        except httpx.HTTPError as e:
            logger.error(f"Property details API error: {e}")
            return None

    def _normalize_property(self, raw: dict) -> dict:
        """Normalize property data to a standard format.

        This method should be adjusted based on the actual API response format.
        """
        return {
            "id": str(raw.get("id", raw.get("codigo", ""))),
            "titulo": raw.get("titulo", raw.get("nome", "")),
            "transacao": raw.get("transacao", ""),
            "tipo": raw.get("tipo", raw.get("tipo_imovel", "")),
            "cidade": raw.get("cidade", ""),
            "bairro": raw.get("bairro", ""),
            "quartos": raw.get("quartos", raw.get("dormitorios", 0)),
            "suites": raw.get("suites", 0),
            "banheiros": raw.get("banheiros", 0),
            "vagas": raw.get("vagas", 0),
            "area_privativa": raw.get("area_privativa", raw.get("area", 0)),
            "preco": raw.get("preco", raw.get("valor", 0)),
            "foto_principal": raw.get("foto_principal", raw.get("foto", "")),
            "url": raw.get("url", ""),
        }

    def _normalize_property_details(self, raw: dict) -> dict:
        """Normalize detailed property data."""
        base = self._normalize_property(raw)
        base.update(
            {
                "descricao": raw.get("descricao", ""),
                "area_total": raw.get("area_total", 0),
                "caracteristicas": raw.get("caracteristicas", []),
                "lazer": raw.get("lazer", []),
                "aceita_financiamento": raw.get("aceita_financiamento", None),
                "fotos": raw.get("fotos", raw.get("galeria", [])),
                "endereco": raw.get("endereco", ""),
                "condominio": raw.get("condominio", raw.get("valor_condominio", 0)),
                "iptu": raw.get("iptu", raw.get("valor_iptu", 0)),
            }
        )
        return base


# ─────────────────────────────────────────────
# Mock Client for Development/Testing
# ─────────────────────────────────────────────


class MockPropertyAPIClient(PropertyAPIClient):
    """Mock client with static data for testing before real API is available."""

    MOCK_PROPERTIES = [
        {
            "id": "1225",
            "titulo": "Wonder - 3 Dorms - Andar Alto - Vista Livre",
            "transacao": "venda",
            "tipo": "apartamento",
            "cidade": "São José dos Campos",
            "bairro": "Jardim Satélite",
            "quartos": 3,
            "suites": 1,
            "banheiros": 2,
            "vagas": 1,
            "area_privativa": 76,
            "preco": 643000,
            "foto_principal": "https://via.placeholder.com/400x300?text=Wonder+Satelite",
            "url": "https://www.upsideimoveis.com.br/1225",
            "descricao": "Wonder na Av. Cidade Jardim, ao lado do Shopping Vale Sul. Lazer completo.",
            "caracteristicas": ["Varanda", "Elevador"],
            "lazer": ["Piscina", "Academia", "Salão de Festas", "Playground"],
            "aceita_financiamento": True,
        },
        {
            "id": "1082",
            "titulo": "Life São José - 2 Dorms com Suíte - Flamboyant",
            "transacao": "venda",
            "tipo": "apartamento",
            "cidade": "São José dos Campos",
            "bairro": "Parque Residencial Flamboyant",
            "quartos": 2,
            "suites": 1,
            "banheiros": 2,
            "vagas": 1,
            "area_privativa": 51.48,
            "preco": 264000,
            "foto_principal": "https://via.placeholder.com/400x300?text=Life+SJC",
            "url": "https://www.upsideimoveis.com.br/1082",
            "descricao": "Life São José, no Bairro Flamboyant. Entrega prevista Nov/2026.",
            "caracteristicas": ["Varanda", "Elevador", "Infra para AC"],
            "lazer": ["Piscina", "Área Gourmet", "Quadra", "Pet Place"],
            "aceita_financiamento": True,
        },
        {
            "id": "CH0019",
            "titulo": "Chácara Santa Branca - Área Urbana",
            "transacao": "venda",
            "tipo": "chacara",
            "cidade": "Santa Branca",
            "bairro": "Parque Cambuci",
            "quartos": 4,
            "suites": 4,
            "banheiros": 5,
            "vagas": 2,
            "area_privativa": 17293,
            "preco": 2200000,
            "foto_principal": "https://via.placeholder.com/400x300?text=Chacara+SB",
            "url": "https://www.upsideimoveis.com.br/902",
            "descricao": "Chácara com casa sede, casa de visitas, pomar, horta. Área total 17.293m².",
            "caracteristicas": ["Mobiliado", "Pomar", "Horta", "Casa de caseiro"],
            "lazer": ["Lago", "Jardim"],
            "aceita_financiamento": True,
        },
        {
            "id": "AP042",
            "titulo": "Apartamento 2 Dorms - Jardim América",
            "transacao": "locacao",
            "tipo": "apartamento",
            "cidade": "São José dos Campos",
            "bairro": "Jardim América",
            "quartos": 2,
            "suites": 0,
            "banheiros": 1,
            "vagas": 1,
            "area_privativa": 58,
            "preco": 1800,
            "foto_principal": "https://via.placeholder.com/400x300?text=Apto+Jd+America",
            "url": "#",
            "descricao": "Apartamento amplo, bem localizado, próximo ao centro.",
            "caracteristicas": ["Armários", "Área de serviço"],
            "lazer": [],
            "aceita_financiamento": False,
        },
        {
            "id": "CS015",
            "titulo": "Casa 3 Dorms - Urbanova",
            "transacao": "venda",
            "tipo": "casa",
            "cidade": "São José dos Campos",
            "bairro": "Urbanova",
            "quartos": 3,
            "suites": 1,
            "banheiros": 2,
            "vagas": 2,
            "area_privativa": 150,
            "preco": 890000,
            "foto_principal": "https://via.placeholder.com/400x300?text=Casa+Urbanova",
            "url": "#",
            "descricao": "Casa em condomínio fechado, acabamento premium, quintal amplo.",
            "caracteristicas": ["Condomínio fechado", "Quintal", "Churrasqueira"],
            "lazer": ["Piscina do condomínio", "Quadra", "Portaria 24h"],
            "aceita_financiamento": True,
        },
    ]

    async def search_properties(self, filters: dict) -> list[dict]:
        """Filter mock properties based on criteria."""
        results = []
        for prop in self.MOCK_PROPERTIES:
            match = True

            if "transacao" in filters and filters["transacao"]:
                if prop["transacao"] != filters["transacao"]:
                    match = False

            if "tipo_imovel" in filters and filters["tipo_imovel"]:
                if prop["tipo"] != filters["tipo_imovel"]:
                    match = False

            if "cidade" in filters and filters["cidade"]:
                if filters["cidade"].lower() not in prop["cidade"].lower():
                    match = False

            if "bairro" in filters and filters["bairro"]:
                if filters["bairro"].lower() not in prop["bairro"].lower():
                    match = False

            if "quartos_min" in filters and filters["quartos_min"]:
                if prop["quartos"] < filters["quartos_min"]:
                    match = False

            if "preco_max" in filters and filters["preco_max"]:
                if prop["preco"] > filters["preco_max"]:
                    match = False

            if "preco_min" in filters and filters["preco_min"]:
                if prop["preco"] < filters["preco_min"]:
                    match = False

            if match:
                results.append(prop)

        return results

    async def get_property_details(self, property_id: str) -> dict | None:
        """Get mock property details."""
        for prop in self.MOCK_PROPERTIES:
            if prop["id"] == property_id:
                return prop
        return None
