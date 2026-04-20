import asyncio
import logging
import re
from typing import Any

import httpx
import redis.asyncio as redis

from app.services.property_cache import PropertyCache
from app.services.property_filters import apply_filters

logger = logging.getLogger(__name__)


# Retry knobs for transient DNS failures inside the container network.
# Coolify's embedded DNS occasionally returns EAI_AGAIN for specific
# domains; a short backoff almost always recovers.
_DNS_RETRY_ATTEMPTS = 4
_DNS_RETRY_BASE_DELAY = 1.0


def _is_dns_failure(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "temporary failure in name resolution" in msg
        or "name or service not known" in msg
        or "nodename nor servname" in msg
        or "getaddrinfo failed" in msg
    )


async def _get_with_retry(
    client: httpx.AsyncClient, url: str, *, params=None, headers=None
) -> httpx.Response:
    """GET that retries with exponential backoff on DNS resolution errors."""
    last_exc: Exception | None = None
    for attempt in range(_DNS_RETRY_ATTEMPTS):
        try:
            r = await client.get(url, params=params, headers=headers)
            r.raise_for_status()
            return r
        except httpx.HTTPError as e:
            last_exc = e
            if not _is_dns_failure(e) or attempt == _DNS_RETRY_ATTEMPTS - 1:
                raise
            delay = _DNS_RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                f"DNS failure on attempt {attempt + 1}/{_DNS_RETRY_ATTEMPTS} "
                f"for {url}: {e}. Retrying in {delay:.1f}s"
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


# Mapping from CRM49 `tipo_imovel` (free-text) to the enum used by the
# `buscar_imoveis` tool (apartamento/casa/terreno/comercial/rural/chacara).
TIPO_IMOVEL_MAP: dict[str, str] = {
    "apartamento": "apartamento",
    "cobertura": "apartamento",
    "flat": "apartamento",
    "studio": "apartamento",
    "kitnet": "apartamento",
    "casa": "casa",
    "casa em condomínio": "casa",
    "casa de condomínio": "casa",
    "sobrado": "casa",
    "terreno": "terreno",
    "terreno em condomínio": "terreno",
    "área": "terreno",
    "lote": "terreno",
    "sala": "comercial",
    "sala comercial": "comercial",
    "prédio comercial": "comercial",
    "galpão": "comercial",
    "loja": "comercial",
    "ponto comercial": "comercial",
    "sítio": "rural",
    "fazenda": "rural",
    "chácara": "chacara",
}

# Keywords used to split `caracteristicas` into lazer vs. general features
LAZER_KEYWORDS = (
    "piscina",
    "academia",
    "playground",
    "quadra",
    "salão",
    "gourmet",
    "churrasqueira",
    "sauna",
    "pet",
    "brinquedoteca",
    "spa",
)

HTML_TAG_RE = re.compile(r"<[^>]*>")


def is_crm49_tenant(tenant) -> bool:
    """Decide whether a tenant should use CRM49Client.

    A tenant qualifies when `api_base_url` and `api_key` are set AND either:
    - `api_config["provider"] == "crm49"`, or
    - `api_base_url` points to `upsideimoveis.com.br` (URL-based auto-detect,
      so a tenant seeded without `provider` still works).
    """
    base_url = getattr(tenant, "api_base_url", None)
    api_key = getattr(tenant, "api_key", None)
    if not base_url or not api_key:
        return False
    api_config = getattr(tenant, "api_config", None) or {}
    if api_config.get("provider") == "crm49":
        return True
    return "upsideimoveis.com.br" in base_url.lower()


def _limpar_html(s: str | None) -> str:
    if not s:
        return ""
    return HTML_TAG_RE.sub("", s).strip()


def _safe_int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _safe_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _map_tipo(tipo_imovel: str | None) -> str:
    if not tipo_imovel:
        return ""
    key = tipo_imovel.strip().lower()
    if key in TIPO_IMOVEL_MAP:
        return TIPO_IMOVEL_MAP[key]
    for raw, mapped in TIPO_IMOVEL_MAP.items():
        if raw in key:
            return mapped
    return key


def _map_transacao(transacoes: list[str] | None) -> str:
    if not transacoes:
        return ""
    for t in transacoes:
        tl = (t or "").strip().lower()
        if tl in ("locação", "locacao", "aluguel"):
            return "locacao"
        if tl in ("venda", "lançamento", "lancamento"):
            return "venda"
    return ""


def _pick_preco(valores: list[dict] | None, transacao: str) -> float:
    if not valores:
        return 0.0
    target = "venda" if transacao == "venda" else "locação"
    for v in valores:
        vt = (v.get("tipo") or "").strip().lower()
        if vt == target or (transacao == "venda" and vt == "lançamento"):
            return _safe_float(v.get("valor"))
    return _safe_float(valores[0].get("valor"))


def _pick_valor_by_tipo(valores: list[dict] | None, tipo_nome: str) -> float:
    if not valores:
        return 0.0
    for v in valores:
        if (v.get("tipo") or "").strip().lower() == tipo_nome.lower():
            return _safe_float(v.get("valor"))
    return 0.0


class CRM49Client:
    """Client for the CRM49 (Upside Imóveis) external API.

    Exposes the same interface as PropertyAPIClient:
    - `search_properties(filters)` reads from the Redis cache and applies
      filters locally (the CRM49 API only supports filtering by status/cidade).
    - `get_property_details(id)` hits the individual endpoint to get
      full-size `fotos` (the listing only has miniatures).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        tenant_id: str | None = None,
        redis_client: redis.Redis | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.tenant_id = tenant_id
        self.cache = PropertyCache(redis_client) if redis_client is not None else None
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    # ─────────────────────────────────────────────
    # Raw HTTP calls
    # ─────────────────────────────────────────────

    async def list_properties_page(
        self,
        page: int = 1,
        per_page: int = 100,
        status: str = "Ativo",
        cidade: str | None = None,
    ) -> dict:
        """Fetch a single page of properties from the CRM49 API."""
        params: dict[str, Any] = {"page": page, "per_page": per_page, "status": status}
        if cidade:
            params["cidade"] = cidade
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await _get_with_retry(
                    client,
                    f"{self.base_url}/properties",
                    params=params,
                    headers=self.headers,
                )
                return r.json() or {}
        except httpx.HTTPError as e:
            logger.error(f"CRM49 list page {page} error: {e}")
            return {"pagination": {}, "data": []}

    async def list_all_active(
        self, cidade: str | None = None, per_page: int = 100
    ) -> list[dict]:
        """Paginate through all active properties and return normalized list."""
        first = await self.list_properties_page(
            page=1, per_page=per_page, status="Ativo", cidade=cidade
        )
        pagination = first.get("pagination") or {}
        total_pages = int(pagination.get("total_pages") or 1)
        raw_items: list[dict] = list(first.get("data") or [])

        for page in range(2, total_pages + 1):
            resp = await self.list_properties_page(
                page=page, per_page=per_page, status="Ativo", cidade=cidade
            )
            raw_items.extend(resp.get("data") or [])

        return [self._normalize_listing(p) for p in raw_items]

    # ─────────────────────────────────────────────
    # Tool-facing interface (matches PropertyAPIClient)
    # ─────────────────────────────────────────────

    async def search_properties(self, filters: dict) -> list[dict]:
        """Return up to 10 filtered properties from the Redis cache."""
        if self.cache and self.tenant_id:
            properties = await self.cache.get_listing(self.tenant_id)
            if properties:
                return apply_filters(properties, filters)[:10]
            logger.warning(
                f"CRM49 cache empty for tenant {self.tenant_id}; falling back to direct API call"
            )

        # Fallback: hit API directly with whatever server-side filters exist
        cidade = filters.get("cidade")
        page = await self.list_properties_page(
            page=1, per_page=50, status="Ativo", cidade=cidade
        )
        raw_items = page.get("data") or []
        normalized = [self._normalize_listing(p) for p in raw_items]
        return apply_filters(normalized, filters)[:10]

    async def get_property_details(self, property_id: str) -> dict | None:
        """Fetch full details for a single property (with real-size photos)."""
        if self.cache and self.tenant_id:
            cached = await self.cache.get_details(self.tenant_id, property_id)
            if cached:
                return cached

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await _get_with_retry(
                    client,
                    f"{self.base_url}/properties/{property_id}",
                    headers=self.headers,
                )
                raw = r.json()
        except httpx.HTTPError as e:
            logger.error(f"CRM49 details error for {property_id}: {e}")
            return None

        details = self._normalize_details(raw)

        if self.cache and self.tenant_id:
            try:
                from app.config import get_settings
                ttl = get_settings().crm49_details_cache_ttl_seconds
                await self.cache.set_details(
                    self.tenant_id, property_id, details, ttl=ttl
                )
            except Exception as e:
                logger.warning(f"Failed to cache details for {property_id}: {e}")

        return details

    # ─────────────────────────────────────────────
    # Normalization
    # ─────────────────────────────────────────────

    def _normalize_listing(self, raw: dict) -> dict:
        loc = raw.get("localizacao") or {}
        transacao = _map_transacao(raw.get("transacoes"))
        valores = raw.get("valores") or []
        preco = _pick_preco(valores, transacao)

        return {
            "id": str(raw.get("id") or raw.get("codigo") or ""),
            "codigo": str(raw.get("codigo") or raw.get("id") or ""),
            "titulo": raw.get("titulo") or "",
            "transacao": transacao,
            "tipo": _map_tipo(raw.get("tipo_imovel")),
            "cidade": loc.get("cidade") or "",
            "bairro": loc.get("bairro") or "",
            "quartos": _safe_int(raw.get("dormitorio")),
            "suites": _safe_int(raw.get("suite")),
            "banheiros": _safe_int(raw.get("banheiro")),
            "vagas": _safe_int(raw.get("vagas")),
            "area_privativa": _safe_float(raw.get("area_util")),
            "preco": preco,
            "foto_principal": raw.get("foto_principal") or "",
            "url": raw.get("link_imovel") or "",
            "descricao": _limpar_html(raw.get("descricao")),
            "_raw_transacoes": raw.get("transacoes") or [],
            "_raw_tipo": raw.get("tipo_imovel") or "",
            "_ultima_alteracao": raw.get("ultimaalteracao") or "",
        }

    def _normalize_details(self, raw: dict) -> dict:
        base = self._normalize_listing(raw)
        loc = raw.get("localizacao") or {}
        caracteristicas = raw.get("caracteristicas") or []

        lazer: list[str] = []
        caract: list[str] = []
        for c in caracteristicas:
            if any(kw in (c or "").lower() for kw in LAZER_KEYWORDS):
                lazer.append(c)
            else:
                caract.append(c)

        endereco_parts = [
            loc.get("endereco") or "",
            loc.get("numero") or "",
            loc.get("bairro") or "",
            loc.get("cidade") or "",
        ]
        endereco = ", ".join(p for p in endereco_parts if p)

        valores = raw.get("valores") or []

        base.update(
            {
                "fotos": raw.get("fotos") or [],
                "caracteristicas": caract,
                "lazer": lazer,
                "area_total": _safe_float(raw.get("area_total")),
                "area_construida": _safe_float(raw.get("area_construida")),
                "endereco": endereco,
                "ano_construcao": _safe_int(raw.get("ano_construcao")),
                "garagem": _safe_int(raw.get("garagem")),
                "garagemcoberta": _safe_int(raw.get("garagemcoberta")),
                "condominio": _pick_valor_by_tipo(valores, "Condomínio"),
                "iptu": _pick_valor_by_tipo(valores, "IPTU"),
                "aceita_financiamento": None,
            }
        )
        return base
