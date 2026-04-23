import asyncio
import logging
import re
import socket
from typing import Any

import aiohttp
import dns.asyncresolver
import dns.resolver
from aiohttp.abc import AbstractResolver
import redis.asyncio as redis

from app.services.property_cache import PropertyCache
from app.services.property_filters import apply_filters

logger = logging.getLogger(__name__)


# External DNS servers used to bypass Coolify's embedded Docker
# resolver, which fails on www.upsideimoveis.com.br with EAI_AGAIN.
# Order matters: Quad9 (9.9.9.9) currently returns a clean answer for
# upsideimoveis.com.br while Cloudflare and Google return SERVFAIL,
# most likely due to DNSSEC validation on the domain's authoritative
# servers. We try each in order and fall back on SERVFAIL.
_EXTERNAL_NAMESERVERS = ["9.9.9.9", "149.112.112.112", "1.1.1.1", "8.8.8.8"]


class _CRM49Resolver(AbstractResolver):
    """aiohttp-compatible resolver using dnspython.

    dnspython is pure-Python (no pycares C-ABI to misalign with the
    installed wheel). The explicit nameservers sidestep /etc/resolv.conf.
    On SERVFAIL from one server we try the next — `Resolver.nameservers`
    doesn't automatically fall through when the server returns a
    definitive error, so we iterate manually.
    """

    def __init__(self, nameservers: list[str]):
        self._nameservers = list(nameservers)

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict]:
        last_exc: Exception | None = None
        for ns in self._nameservers:
            r = dns.asyncresolver.Resolver(configure=False)
            r.nameservers = [ns]
            r.timeout = 3.0
            r.lifetime = 4.0
            try:
                answer = await r.resolve(host, "A")
                addrs = [str(record.address) for record in answer]
                logger.info(f"DNS {ns} resolved {host} -> {addrs}")
                return [
                    {
                        "hostname": host,
                        "host": addr,
                        "port": port,
                        "family": socket.AF_INET,
                        "proto": 0,
                        "flags": 0,
                    }
                    for addr in addrs
                ]
            except Exception as e:
                logger.warning(
                    f"DNS {ns} failed for {host}: {type(e).__name__}: {e}"
                )
                last_exc = e
        raise last_exc or RuntimeError(f"All nameservers failed for {host}")

    async def close(self) -> None:
        return None


def _make_session(timeout_seconds: float, headers: dict[str, str]) -> aiohttp.ClientSession:
    """Build an aiohttp session with our explicit external DNS resolver."""
    connector = aiohttp.TCPConnector(
        resolver=_CRM49Resolver(_EXTERNAL_NAMESERVERS),
        family=socket.AF_INET,
        ttl_dns_cache=300,
    )
    return aiohttp.ClientSession(
        connector=connector,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout_seconds),
    )


def _proxy_url() -> str | None:
    """Optional HTTPS proxy (e.g. Oracle SP) for CRM49 calls when the
    upstream firewall blocks the app server's datacenter IP."""
    from app.config import get_settings
    return get_settings().crm49_http_proxy or None


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
        # Minimal headers only. The Code49 backend silently caps /properties
        # to 50 items (ignoring page/per_page) when the request includes
        # Accept/Content-Type: application/json or the aiohttp default
        # User-Agent. Confirmed via /admin/crm49/raw?_strip_extra=1 on
        # 2026-04-22. Match curl's surface so the upstream treats us as
        # an "integrator" rather than an "automated scraper".
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "curl/8.0.0",
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
            async with _make_session(30.0, self.headers) as session:
                async with session.get(
                    f"{self.base_url}/properties",
                    params=params,
                    proxy=_proxy_url(),
                ) as r:
                    r.raise_for_status()
                    return (await r.json()) or {}
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(
                f"CRM49 list page {page} error: {type(e).__name__}: {e!r}"
            )
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
            async with _make_session(15.0, self.headers) as session:
                async with session.get(
                    f"{self.base_url}/properties/{property_id}",
                    proxy=_proxy_url(),
                ) as r:
                    r.raise_for_status()
                    raw = await r.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(
                f"CRM49 details error for {property_id}: {type(e).__name__}: {e!r}"
            )
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

        cor = raw.get("corretor") or {}
        corretor = {
            "nome": cor.get("nome") or "",
            "telefones": cor.get("telefones") or [],
            "emails": cor.get("emails") or [],
        }

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
                "corretor": corretor,
            }
        )
        return base
