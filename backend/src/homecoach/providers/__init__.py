from homecoach.config import Settings
from homecoach.providers.base import Provider


def get_provider(settings: Settings) -> Provider | None:
    """Return the configured provider, or None for FIT-import-only setups."""
    if settings.provider == "coros":
        from homecoach.providers.coros import CorosProvider

        return CorosProvider.from_settings(settings)
    return None
