"""Every setting must be documented in .env.example and passed to the containers by docker-compose.yml.

The TrueNAS app reads .env only to fill in the compose file, so a setting missing from the compose file's
x-env block silently can't be changed on the NAS.
"""

from pathlib import Path

import pytest
import yaml

from homecoach.config import Settings

ROOT = Path(__file__).resolve().parents[2]
# set by the compose file or the image itself, not by the user
INTERNAL = {"database_url", "data_dir", "web_dir"}
SETTINGS = sorted(f"HC_{name.upper()}" for name in Settings.model_fields if name not in INTERNAL)


@pytest.mark.parametrize("var", SETTINGS)
def test_setting_is_in_env_example(var):
    assert f"{var}=" in (ROOT / ".env.example").read_text(), f"document {var} in .env.example"


@pytest.mark.parametrize("var", SETTINGS)
def test_setting_reaches_the_containers(var):
    env = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["x-env"]
    assert var in env, f"add {var} to x-env in docker-compose.yml"
