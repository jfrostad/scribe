"""Configuration loading for Scribe.

Loads from config.yaml with environment variable overrides via pydantic-settings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class AudioConfig(BaseModel):
    mic_device: int | str | None = None
    system_device: str | None = "BlackHole 2ch"
    sample_rate: int = 48000
    channels: int = 1


class WhisperConfig(BaseModel):
    model_size: str = "small"
    device: str = "auto"
    compute_type: str = "int8"
    language: str | None = "en"


class AnalysisConfig(BaseModel):
    model: str = "claude-sonnet-4-20250514"


class OutputConfig(BaseModel):
    base_dir: str = "recordings"

    @property
    def base_path(self) -> Path:
        p = Path(self.base_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p


class EnvConfig(BaseSettings):
    """Environment variables (secrets)."""

    anthropic_api_key: str = ""

    model_config = {"env_file": str(PROJECT_ROOT / ".env"), "extra": "ignore"}


class AppConfig(BaseModel):
    audio: AudioConfig = Field(default_factory=AudioConfig)
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    env: EnvConfig = Field(default_factory=EnvConfig)

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load config from YAML file + environment variables."""
    if config_path is None:
        config_path = PROJECT_ROOT / "config.yaml"

    yaml_data: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path) as f:
            yaml_data = yaml.safe_load(f) or {}

    env = EnvConfig()

    return AppConfig(**yaml_data, env=env)
