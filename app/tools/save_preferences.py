import logging

logger = logging.getLogger(__name__)


def _prune_none(value):
    """Recursively drop None / empty-dict values so we don't overwrite real
    data with nulls when Claude passes a partial qualification block."""
    if isinstance(value, dict):
        cleaned = {}
        for k, v in value.items():
            pruned = _prune_none(v)
            if pruned is None:
                continue
            if isinstance(pruned, dict) and not pruned:
                continue
            cleaned[k] = pruned
        return cleaned
    return value


async def handle_save_preferences(params: dict, lead_manager=None, lead_id: str = None) -> dict:
    """Handle the salvar_preferencias tool call from Claude.

    Saves extracted preferences to the lead's profile in PostgreSQL. Top-level
    fields (interesse, tipo_imovel, cidade, etc.) feed `buscar_imoveis`. The
    `qualification` block holds the 7 dimensions of the Upside methodology and
    is deep-merged across turns by `lead_manager.update_lead_profile`.
    """
    logger.info(f"Saving preferences for lead {lead_id}: {params}")

    if not (lead_manager and lead_id):
        return {"success": True, "message": "Preferências registradas."}

    # Pull modo_detectado out — it doesn't belong as a top-level profile_data
    # key; it lives in qualification._meta.
    modo_detectado = params.pop("modo_detectado", None) if isinstance(params, dict) else None
    qualification = params.pop("qualification", None) if isinstance(params, dict) else None

    # Drop None values from the legacy flat keys (same as the previous handler).
    preferences = {k: v for k, v in params.items() if v is not None}

    # Clean up the qualification tree before merging.
    if isinstance(qualification, dict):
        cleaned = _prune_none(qualification)
        if cleaned:
            preferences["qualification"] = cleaned

    if modo_detectado:
        preferences.setdefault("qualification", {})
        preferences["qualification"].setdefault("_meta", {})
        preferences["qualification"]["_meta"]["modo_atual"] = modo_detectado

    try:
        await lead_manager.update_lead_profile(lead_id, preferences)
        logger.info(f"Preferences saved for lead {lead_id}")
    except Exception as e:
        logger.error(f"Error saving preferences for lead {lead_id}: {e}")

    return {
        "success": True,
        "message": "Preferências registradas.",
    }
