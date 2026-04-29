"""Qualification scoring derived from the Upside methodology.

Pure functions only — no I/O, no DB calls. They take a `profile_data` dict
(the JSONB column on Lead) and return signals the dashboard can index and
the bot can react to.

Source of truth for the rules: the Upside training material (Apostila de
Qualificação Upside, chapters 3 and 9).
"""

from __future__ import annotations

from typing import Literal

Temperature = Literal["hot", "warm", "cold"]
Status = Literal["complete", "partial", "missing"]

DIMENSION_KEYS = (
    "motivacao",
    "perfil_vida",
    "localizacao",
    "tipologia",
    "financeiro",
    "urgencia",
    "decisores",
)

# Fields that, when present, mark a dimension as "complete enough" for
# downstream gates (e.g., qualification_stage moving forward). These are the
# core fields the apostila treats as obrigatórios (*) in the Ficha de
# Qualificação (chapter 8).
_COMPLETE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "motivacao": ("gatilho", "situacao_atual", "proposito"),
    "perfil_vida": ("moradores",),
    "localizacao": ("indispensavel_perto",),  # bairros_preferidos vive na raiz como `bairros`
    "tipologia": ("necessidades", "aceita_reforma"),
    "financeiro": ("modalidade",),  # teto vive na raiz como `preco_max`
    "urgencia": ("prazo", "disponibilidade_visita"),
    "decisores": ("outros",),
}


def _has_value(value) -> bool:
    """Treat None / empty string / empty list / empty dict as missing."""
    if value is None:
        return False
    if isinstance(value, (str, list, dict)) and not value:
        return False
    return True


def _q(profile_data: dict | None) -> dict:
    return (profile_data or {}).get("qualification") or {}


def dimensions_status(profile_data: dict | None) -> dict[str, Status]:
    """Return one of complete/partial/missing for each of the 7 dimensions."""
    q = _q(profile_data)
    out: dict[str, Status] = {}
    for dim in DIMENSION_KEYS:
        block = q.get(dim) or {}
        if not isinstance(block, dict) or not any(_has_value(v) for v in block.values()):
            out[dim] = "missing"
            continue
        required = _COMPLETE_REQUIREMENTS.get(dim, ())
        if required and all(_has_value(block.get(field)) for field in required):
            out[dim] = "complete"
        else:
            out[dim] = "partial"
    return out


def dimensions_filled_count(profile_data: dict | None) -> int:
    """Number of dimensions with at least 1 field populated (partial+complete)."""
    statuses = dimensions_status(profile_data)
    return sum(1 for s in statuses.values() if s != "missing")


# ─────────────────────────────────────────────
# Temperature
# ─────────────────────────────────────────────
# REGRA 005 (apostila) — disponibilidade para visitar > 3 dias rebaixa para
# morno mesmo com outros sinais de calor. Implementado como override no fim.

_HOT_RULES: list[tuple[str, callable]] = [
    ("prazo_imediato", lambda q: (q.get("urgencia") or {}).get("prazo") in ("imediato", "60_180_dias")),
    ("disponibilidade_3d", lambda q: (q.get("urgencia") or {}).get("disponibilidade_visita") == "ate_3_dias"),
    ("credito_aprovado", lambda q: (q.get("financeiro") or {}).get("credito_status") == "aprovado"),
    ("evento_ancora", lambda q: _has_value((q.get("urgencia") or {}).get("evento_ancora"))),
    ("outros_corretores", lambda q: (q.get("urgencia") or {}).get("outros_corretores") is True),
]

_COLD_RULES: list[tuple[str, callable]] = [
    ("sem_prazo", lambda q: (q.get("urgencia") or {}).get("prazo") == "indefinido"),
    ("precisa_vender", lambda q: (q.get("financeiro") or {}).get("imovel_para_venda") is True),
    ("indisponivel", lambda q: (q.get("urgencia") or {}).get("disponibilidade_visita") == "sem_disponibilidade"),
]


def compute_temperature(profile_data: dict | None) -> tuple[Temperature, str]:
    """Return (temperature, reason) using deterministic rules from the apostila.

    - cold beats hot when both fire (a blocker like "preciso vender antes" is
      decisive even if the customer talks about urgency).
    - warm is the default when nothing fires or nothing is known.
    - REGRA 005 override: disponibilidade pra visitar > 3 dias caps at warm.
    """
    q = _q(profile_data)

    cold_signals = [name for name, fn in _COLD_RULES if _safe(fn, q)]
    if cold_signals:
        return "cold", ", ".join(cold_signals)

    hot_signals = [name for name, fn in _HOT_RULES if _safe(fn, q)]
    if not hot_signals:
        return "warm", "default"

    disponibilidade = (q.get("urgencia") or {}).get("disponibilidade_visita")
    if disponibilidade in ("ate_1_semana", "sem_disponibilidade"):
        # REGRA 005 — sem janela de 3 dias, rebaixa.
        return "warm", "regra_005_disponibilidade_>_3d"

    return "hot", ", ".join(hot_signals)


def _safe(fn, q) -> bool:
    try:
        return bool(fn(q))
    except Exception:
        return False


# ─────────────────────────────────────────────
# Stage
# ─────────────────────────────────────────────

_STAGE_ORDER = (
    "abertura",
    "motivacao",
    "perfil_localizacao",
    "tipologia",
    "financeiro",
    "decisores_urgencia",
    "pronto_para_curadoria",
    "pronto_para_visita",
)


def qualification_stage(profile_data: dict | None) -> str:
    """Coarse pipeline position based on which dimensions have data.

    The bot doesn't strictly traverse these in order — it follows the modo
    detection in the prompt. But the dashboard wants a single label to show
    progress, so we infer from which dimensions are filled.
    """
    statuses = dimensions_status(profile_data)
    filled = {k for k, v in statuses.items() if v != "missing"}
    completed = {k for k, v in statuses.items() if v == "complete"}

    if not filled:
        return "abertura"

    # Pré-visita: as 3 portas obrigatórias da apostila (decisores + financeiro
    # + urgência) cumpridas com dados completos.
    pre_visit_keys = {"decisores", "financeiro", "urgencia"}
    if pre_visit_keys.issubset(completed):
        return "pronto_para_visita"

    # Pronto pra curadoria: as 7 dimensões com pelo menos algum dado.
    if len(filled) >= 6:
        return "pronto_para_curadoria"

    # Resto: nomeie pelo grupo mais avançado em coleta.
    if "decisores" in filled or "urgencia" in filled:
        return "decisores_urgencia"
    if "financeiro" in filled:
        return "financeiro"
    if "tipologia" in filled:
        return "tipologia"
    if "perfil_vida" in filled or "localizacao" in filled:
        return "perfil_localizacao"
    if "motivacao" in filled:
        return "motivacao"
    return "abertura"


# ─────────────────────────────────────────────
# Self-test (run with `python -m app.services.qualification`)
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # Cold takes precedence even with hot signals.
    pd = {"qualification": {
        "urgencia": {"prazo": "imediato", "disponibilidade_visita": "ate_3_dias"},
        "financeiro": {"imovel_para_venda": True},
    }}
    assert compute_temperature(pd)[0] == "cold", compute_temperature(pd)

    # Hot — prazo concreto + disponibilidade <=3d.
    pd = {"qualification": {
        "urgencia": {"prazo": "imediato", "disponibilidade_visita": "ate_3_dias"},
    }}
    assert compute_temperature(pd)[0] == "hot", compute_temperature(pd)

    # REGRA 005 override — prazo concreto mas disponibilidade > 3 dias rebaixa.
    pd = {"qualification": {
        "urgencia": {"prazo": "imediato", "disponibilidade_visita": "ate_1_semana"},
    }}
    assert compute_temperature(pd)[0] == "warm", compute_temperature(pd)

    # Default (sem dados) -> warm.
    assert compute_temperature({})[0] == "warm"
    assert compute_temperature(None)[0] == "warm"

    # Cold puro.
    pd = {"qualification": {"urgencia": {"prazo": "indefinido"}}}
    assert compute_temperature(pd)[0] == "cold"

    # Stage progression.
    assert qualification_stage(None) == "abertura"
    assert qualification_stage({"qualification": {"motivacao": {"gatilho": "x"}}}) == "motivacao"

    # dimensions_status partial vs complete.
    pd = {"qualification": {"motivacao": {"gatilho": "x"}}}
    assert dimensions_status(pd)["motivacao"] == "partial"
    pd = {"qualification": {"motivacao": {
        "gatilho": "x", "situacao_atual": "aluguel", "proposito": "moradia",
    }}}
    assert dimensions_status(pd)["motivacao"] == "complete"

    # Filled count.
    assert dimensions_filled_count({}) == 0
    assert dimensions_filled_count({"qualification": {
        "motivacao": {"gatilho": "x"},
        "tipologia": {"necessidades": ["3q"]},
    }}) == 2

    print("OK qualification.py self-tests passed")
