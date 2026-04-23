import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from app.services.property_filters import _fold

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


_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "fixtures"
    / "upside_properties.json"
)


def _load_fixture() -> list[dict] | None:
    """Load real-property fixture if present. Returns normalized list or None."""
    if not _FIXTURE_PATH.exists():
        return None
    try:
        raw = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Failed to load fixture {_FIXTURE_PATH}: {e}")
        return None

    # /admin/crm49/peek shape: {"tenants": [{"slug":..., "items":[...]}], ...}
    # or it might be a flat list — handle both.
    items: list[dict] = []
    if isinstance(raw, dict):
        for tenant in raw.get("tenants", []):
            items.extend(
                tenant.get("sample", [])
                or tenant.get("items", [])
                or tenant.get("properties", [])
                or []
            )
        if not items:
            items = raw.get("items", []) or raw.get("results", []) or raw.get("sample", []) or []
    elif isinstance(raw, list):
        items = raw
    return items or None


class MockPropertyAPIClient(PropertyAPIClient):
    """Mock client with static data for testing before real API is available.

    If `scripts/fixtures/upside_properties.json` exists, loads from it;
    otherwise falls back to the small hardcoded MOCK_PROPERTIES list.
    """

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
            "foto_principal": "https://images.unsplash.com/photo-1545324418-cc1a3fa10c00?w=800&q=80",
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
            "foto_principal": "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?w=800&q=80",
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
            "foto_principal": "https://images.unsplash.com/photo-1564013799919-ab600027ffc6?w=800&q=80",
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
            "foto_principal": "https://images.unsplash.com/photo-1502672260266-1c1ef2d93688?w=800&q=80",
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
            "foto_principal": "https://images.unsplash.com/photo-1600596542815-ffad4c1539a9?w=800&q=80",
            "url": "#",
            "descricao": "Casa em condomínio fechado, acabamento premium, quintal amplo.",
            "caracteristicas": ["Condomínio fechado", "Quintal", "Churrasqueira"],
            "lazer": ["Piscina do condomínio", "Quadra", "Portaria 24h"],
            "aceita_financiamento": True,
        },
    ]

    def __init__(self, base_url: str = "http://mock", api_key: str | None = None, config: dict = None):
        super().__init__(base_url=base_url, api_key=api_key, config=config)
        fixture = _load_fixture()
        if fixture:
            normalized: list[dict] = []
            for raw in fixture:
                base = self._normalize_property(raw)
                # carry extras useful for details/search
                base["descricao"] = raw.get("descricao", "")
                base["fotos"] = raw.get("fotos", raw.get("galeria", []))
                base["caracteristicas"] = raw.get("caracteristicas", [])
                base["lazer"] = raw.get("lazer", [])
                base["aceita_financiamento"] = raw.get("aceita_financiamento")
                normalized.append(base)
            self.MOCK_PROPERTIES = normalized
            logger.info(f"MockPropertyAPIClient loaded {len(normalized)} properties from fixture")

    async def search_properties(self, filters: dict) -> list[dict]:
        """Filter mock properties based on criteria."""
        results = []
        for prop in self.MOCK_PROPERTIES:
            match = True

            if "transacao" in filters and filters["transacao"]:
                if prop.get("transacao") != filters["transacao"]:
                    match = False

            if "tipo_imovel" in filters and filters["tipo_imovel"]:
                if prop.get("tipo") != filters["tipo_imovel"]:
                    match = False

            if "cidade" in filters and filters["cidade"]:
                if filters["cidade"].lower() not in (prop.get("cidade") or "").lower():
                    match = False

            if "bairro" in filters and filters["bairro"]:
                needle = _fold(filters["bairro"])
                haystacks = (
                    _fold(prop.get("bairro", "")),
                    _fold(prop.get("titulo", "")),
                    _fold(prop.get("descricao", "")),
                )
                if not any(needle in h for h in haystacks):
                    match = False

            if "condominio" in filters and filters["condominio"]:
                needle = _fold(filters["condominio"])
                if (
                    needle not in _fold(prop.get("titulo", ""))
                    and needle not in _fold(prop.get("descricao", ""))
                ):
                    match = False

            if "quartos_min" in filters and filters["quartos_min"]:
                if (prop.get("quartos") or 0) < filters["quartos_min"]:
                    match = False

            if "preco_max" in filters and filters["preco_max"]:
                if (prop.get("preco") or 0) > filters["preco_max"]:
                    match = False

            if "preco_min" in filters and filters["preco_min"]:
                if (prop.get("preco") or 0) < filters["preco_min"]:
                    match = False

            if match:
                results.append(prop)

        return results[:10]

    async def get_property_details(self, property_id: str) -> dict | None:
        """Get mock property details."""
        for prop in self.MOCK_PROPERTIES:
            if str(prop.get("id")) == str(property_id):
                return prop
        return None