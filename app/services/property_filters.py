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
        if cidade and cidade.lower() not in (prop.get("cidade") or "").lower():
            continue
        if bairro and bairro.lower() not in (prop.get("bairro") or "").lower():
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
