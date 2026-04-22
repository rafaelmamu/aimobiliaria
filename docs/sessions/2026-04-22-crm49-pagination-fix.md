# Checkpoint: CRM49 Pagination Fix (2026-04-22)

## Status: ✅ RESOLVIDO

Bloqueio de sincronização do catálogo CRM49 está resolvido. Cliente (Upside) agora tem **986 imóveis ativos únicos** cacheados (antes: 50 imóveis repetidos 26 vezes).

## Causa raiz

O backend Code49 serve silenciosamente um **"feed limitado" de 50 itens** (ignorando os parâmetros `page` e `per_page`) quando o request HTTP contém qualquer um destes headers:

- `Accept: application/json`
- `Content-Type: application/json`
- User-Agent default do aiohttp (`Python/3.x aiohttp/3.x`)

Quando o request chega só com `Authorization: Bearer {token}` e um User-Agent curl-like, a paginação funciona normalmente (1274 total, 13 páginas de 100).

Não documentado pelo suporte Code49 nem no OpenAPI spec.

## Fix aplicado

**PR #28 — commit `1611c2e`** (`fix/crm49-header-cap`):

```python
# app/services/crm49_client.py (CRM49Client.__init__)
self.headers = {
    "Authorization": f"Bearer {api_key}",
    "User-Agent": "curl/8.0.0",
}
```

Antes mandava também `Accept: application/json` e `Content-Type: application/json`.

## Jornada do diagnóstico

Matrix de PRs que isolou o bug (todos mergeados em `main`):

| PR | Branch | O que adicionou |
|---|---|---|
| #20 | `debug/crm49-fetch-page` | `/admin/crm49/fetch-page` |
| #23 | `debug/crm49-raw` | `/admin/crm49/raw` (passthrough) |
| #24 | `debug/crm49-raw-path-real` | `_path_suffix`, `_method` |
| #25 | `debug/crm49-raw-full` | `_path_override`, `_full`, `body_top_level_keys` |
| #26 | (continuação) | `_base_override` (host doc) |
| #27 | (continuação) | `_strip_extra=1`, `/admin/crm49/egress-ip` |
| **#28** | `fix/crm49-header-cap` | **Fix final** |

Hipóteses eliminadas no caminho:
- ❌ Paginação com `page`/`pagina`/`p`/`offset`/`cursor` diferentes (testado #23)
- ❌ POST vs GET, path alternativo (testado #24)
- ❌ Scope do token (testado com token recém-criado — mesmo cap)
- ❌ Cache CDN (upstream manda `cache-control: no-store`)
- ❌ IP rate-limiting (egress IP Coolify: `204.168.173.127` — token funciona do mesmo IP quando sem headers extras)
- ✅ Headers HTTP — confirmado via `/admin/crm49/raw?_strip_extra=1`

## Estado verificado após o fix

```
GET /admin/crm49/status
{
  "slug": "upside",
  "cached_count": 986,
  "last_sync": "2026-04-22T23:08:21Z"
}

GET /admin/crm49/peek
total_cached: 986, total_unique: 986    # sem duplicatas

GET /admin/crm49/peek?q=colinas&tipo=casa
matched: 10 casas reais do Jardim das Colinas e Condomínio Colinas do Paratehy
(CA0342, CA0291, CA0376, CA0385, CA0396, CA0398, CA0409, CA0420...)
```

Token rotacionado após o fix; token antigo revogado no painel Code49.

## Diferença 986 vs 1274

O total `1274` do spec é **todos os imóveis incluindo vendidos/inativos/locados**. Nosso sync passa `status=Ativo`, então cacheia apenas 986 ativos. Diferença esperada, não é bug.

## Endpoints de debug CRM49 (mantidos no `admin.py`)

Custo baixo, úteis pra diagnosticar novos clientes Code49 no futuro:

| Endpoint | Uso |
|---|---|
| `GET /admin/crm49/status` | Resumo do sync por tenant |
| `POST /admin/crm49/sync` | Força sync imediato |
| `GET /admin/crm49/peek?q=X&tipo=Y&transacao=Z` | Lê cache Redis com filtros, deduplicado |
| `GET /admin/crm49/fetch-page?page=N&per_page=M` | Chama API CRM49 numa página específica |
| `GET /admin/crm49/raw?<params>` | Passthrough completo (`_path_suffix`, `_path_override`, `_base_override`, `_method`, `_full`, `_strip_extra`) |
| `GET /admin/crm49/egress-ip` | IP público que CRM49 vê do server |

## Parâmetros válidos do CRM49 `/properties` (spec)

Pagination: `page` (min 1), `per_page` (max 100).

Filtros: `code`, `status`, `property_type`, `city`, `bairro` (requer city), `bedroom[_min|_max]`, `suite[_min|_max]`, `bathroom[_min|_max]`, `total_area[_min|_max]`, `useful_area[_min|_max]`, `built_area[_min|_max]`, `sale_value[_min|_max]`, `rent_value[_min|_max]`, `iptu_value[_min|_max]`, `community_value[_min|_max]`.

**Não existe como filtro de query:** `transacao`, `finalidade`, `sort`, `search`.

Spec completo em `.claude/crm49_spec.json` (local) ou `https://code49.com/docs/api_externa.json`.

## Pendências / próximos passos possíveis

Nenhum item crítico aberto. Ideias pra continuar:

- [ ] **Remover `status=Ativo` hardcoded** em `crm49_client.py:263` se quiser cachear imóveis em outros status (para relatórios de vendidos/locados, etc.).
- [ ] **Sincronizar `MockPropertyAPIClient`** (`property_api.py`) com a lógica real de filtros, pra `scripts/test_agent.py` não divergir.
- [ ] **Investigar por que o filtro `bairro` precisa de `city` junto** no upstream — pode ser necessário ajustar `_map_filters` quando o usuário só fornece o bairro.
- [ ] Considerar se faz sentido expor um filtro direto de "empreendimento"/condomínio no `buscar_imoveis` (tool do Claude), já que a maioria das perguntas reais é por condomínio (caso do Jardim das Colinas).

## Referências

- Memória local: `C:\Users\mucou\.claude\projects\G--Meu-Drive-DEV-AImobiliarIA\memory\crm49_api_blocker.md`
- Spec OpenAPI: `.claude/crm49_spec.json`
- Commit do fix: `1611c2e`
- PRs: #20, #23, #24, #25, #26, #27, #28
- Dashboard produção: `https://qzeg5r8p8o76wk8t2slr92oi.204.168.173.127.sslip.io/dashboard`
