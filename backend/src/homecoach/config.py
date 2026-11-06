from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration comes from environment variables (prefix HC_)."""

    model_config = SettingsConfigDict(env_prefix="HC_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://homecoach:homecoach@db:5432/homecoach"
    athlete_name: str = "Athlete"
    timezone: str = "Europe/Lisbon"

    #   coros-mcp = official COROS MCP server (recommended; connect once in the UI, no password stored)
    #   coros     = unofficial Training Hub web API (needs HC_COROS_EMAIL / HC_COROS_PASSWORD)
    #   none      = FIT inbox only
    provider: Literal["coros-mcp", "coros", "none"] = "coros-mcp"
    coros_mcp_url: str = "https://mcp.coros.com/mcp"
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

    # Optional LLM for the race assistant and the coach. "none" switches both off.
    #   openai    = any OpenAI-compatible server: Ollama (default URL), llama.cpp, LM Studio, vLLM, OpenRouter...
    #   anthropic = Claude API (needs HC_LLM_API_KEY)
    llm_provider: Literal["none", "openai", "anthropic"] = "none"
    llm_base_url: str = "http://ollama:11434/v1"
    llm_model: str = "qwen2.5:3b"
    llm_api_key: SecretStr | None = None
    llm_timeout_s: int = 600
    # How much the model may "think" before answering. Thinking costs CPU time on every request:
    #   low     = gpt-oss: "Reasoning: low"; Qwen3: thinking off (/no_think). Recommended on a CPU.
    #   default = leave it to the model
    llm_reasoning: Literal["low", "default"] = "low"
    # Self-hosted web search used by the race assistant (SearXNG with the JSON format enabled)
    searxng_url: str = "http://searxng:8080"
    # Weekly coach review (local time): day of week 0=Mon..6=Sun and hour
    coach_review_dow: int = 6
    coach_review_hour: int = 19

    # Optional HTTP basic auth for the web app and API (recommended if reachable beyond your LAN).
    auth_user: str | None = None
    auth_password: SecretStr | None = None

    data_dir: Path = Path("/data")

    @model_validator(mode="after")
    def complete_auth(self) -> "Settings":
        user = bool(self.auth_user)
        password = bool(self.auth_password and self.auth_password.get_secret_value())
        if user != password:
            raise ValueError("set both HC_AUTH_USER and HC_AUTH_PASSWORD, or leave both empty")
        return self

    @property
    def fit_dir(self) -> Path:
        return self.data_dir / "fit"

    @property
    def fit_inbox(self) -> Path:
        return self.data_dir / "fit-inbox"


@lru_cache
def get_settings() -> Settings:
    return Settings()
