import logging

logger = logging.getLogger(__name__)


async def handle_save_preferences(params: dict, lead_manager=None, lead_id: str = None) -> dict:
    """Handle the salvar_preferencias tool call from Claude.

    Saves extracted preferences to the lead's profile in PostgreSQL.
    """
    logger.info(f"Saving preferences for lead {lead_id}: {params}")

    if lead_manager and lead_id:
        # Remove None values
        preferences = {k: v for k, v in params.items() if v is not None}
        try:
            await lead_manager.update_lead_profile(lead_id, preferences)
            logger.info(f"Preferences saved for lead {lead_id}")
        except Exception as e:
            logger.error(f"Error saving preferences: {e}")

    return {
        "success": True,
        "message": "Preferências registradas.",
    }
