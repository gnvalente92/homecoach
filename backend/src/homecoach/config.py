from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration comes from environment variables (prefix HC_)."""

    model_config = SettingsConfigDict(env_prefix="HC_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://homecoach:homecoach@db:5432/homecoach"
    athlete_name: str = "Athlete"
    timezone: str = "Europe/Lisbon"

    # Which provider the collector pulls from: "coros" or "none" (FIT import only)
    provider: Literal["coros", "none"] = "coros"
    coros_email: str | None = None
    coros_password: SecretStr | None = None
    coros_region: Literal["eu", "en", "cn"] = "eu"

    sync_interval_minutes: int = 10
    full_sync_hour: int = 3
    backfill_days: int = 730
    download_fit: bool = True
    # Optional override, e.g. "4:50" (min:sec per km). Otherwise taken from the provider or estimated.
    threshold_pace: str | None = None

    web_dir: Path = Path("/app/web")

    # Optional HTTP basic auth for the web app and API (recommended if reachable beyond your LAN).
    auth_user: str | None = None
    auth_password: SecretStr | None = None

    data_dir: Path = Path("/data")

    @property
    def fit_dir(self) -> Path:
        return self.data_dir / "fit"

    @property
    def fit_inbox(self) -> Path:
        return self.data_dir / "fit-inbox"


@lru_cache
def get_settings() -> Settings:
    return Settings()
