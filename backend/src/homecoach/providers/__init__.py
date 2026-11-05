from homecoach.config import Settings
from homecoach.providers.base import Provider


def get_provider(settings: Settings, db=None) -> Provider | None:
    """Return the configured provider, or None for FIT-import-only setups. coros-mcp needs a db session
    (its OAuth tokens live in the database)."""
    if settings.provider == "coros":
        from homecoach.providers.coros import CorosProvider

        return CorosProvider.from_settings(settings)
    if settings.provider == "coros-mcp":
        from homecoach import coros_auth
        from homecoach.providers.coros_mcp import CorosMCPProvider

        return CorosMCPProvider(coros_auth.session(db), settings.timezone)
    return None
