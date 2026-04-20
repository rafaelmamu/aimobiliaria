import unicodedata


def _fold(s: str | None) -> str:
    """Lowercase and strip diacritics so "condominio" matches "Condomínio"."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def apply_filters(properties: list[dict], filters: dict) -> list[dict]:
    """Apply local filters to a cached property list.

    Mirrors the filter logic used by MockPropertyAPIClient.search_properties.
    The CRM49 API only supports server-side filtering by status/cidade,
    so every other criterion from the `buscar_imoveis` tool is applied here.

    Results are sorted by `_ultima_alteracao` desc (most recent first).
    """
    transacao = filters.get("transacao")
    tipo_imovel = filters.get("tipo_imovel")
    cidade = filters.get("cidade")
    bairro = filters.get("bairro")
    condominio = filters.get("condominio")
    quartos_min = filters.get("quartos_min")
    preco_min = filters.get("preco_min")
    preco_max = filters.get("preco_max")
    area_min = filters.get("area_min")

    results: list[dict] = []
    for prop in properties:
        if transacao and prop.get("transacao") != transacao:
            continue
        if tipo_imovel and prop.get("tipo") != tipo_imovel:
            continue
        if cidade and _fold(cidade) not in _fold(prop.get("cidade")):
            continue
        if bairro:
            # Condomínios/empreendimentos muitas vezes não batem com o campo
            # bairro oficial da CRM49 (ex: "Condomínio Colinas" vive no título,
            # não no bairro "Jardim das Colinas"). Cai em título/descrição
            # como fallback pra não perder esses casos. _fold ignora acentos
            # porque clientes frequentemente digitam sem eles no WhatsApp.
            needle = _fold(bairro)
            haystacks = (
                _fold(prop.get("bairro")),
                _fold(prop.get("titulo")),
                _fold(prop.get("descricao")),
            )
            if not any(needle in h for h in haystacks):
                continue
        if condominio:
            needle = _fold(condominio)
            titulo = _fold(prop.get("titulo"))
            descricao = _fold(prop.get("descricao"))
            if needle not in titulo and needle not in descricao:
                continue
        if quartos_min and (prop.get("quartos") or 0) < quartos_min:
            continue
        preco = prop.get("preco") or 0
        if preco_max and preco > preco_max:
            continue
        if preco_min and preco < preco_min:
            continue
        if area_min and (prop.get("area_privativa") or 0) < area_min:
            continue
        results.append(prop)

    results.sort(key=lambda p: p.get("_ultima_alteracao") or "", reverse=True)
    return results
