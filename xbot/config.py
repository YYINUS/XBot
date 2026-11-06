"""Configuration loading: settings.yaml + sources.yaml + persona.md + environment."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("XBOT_CONFIG_DIR", ROOT / "config"))
PROMPTS_DIR = Path(os.environ.get("XBOT_PROMPTS_DIR", ROOT / "prompts"))


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #")[0].strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


class AgentModel(BaseModel):
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_tokens: int = 16000


class Cadence(BaseModel):
    collect: int = 120
    scout: int = 360
    metrics: int = 720


class PipelineCfg(BaseModel):
    item_max_age_hours: int = 48
    max_items_to_curator: int = 60
    picks_per_run: int = 3
    min_score: int = 65
    variants_per_pick: int = 2
    auto_post_min_score: int = 80


class PostingCfg(BaseModel):
    max_posts_per_day: int = 6
    min_minutes_between_posts: int = 75
    windows: list[str] = Field(default_factory=lambda: ["08:00-23:00"])
    max_post_chars: int = 280
    link_strategy: str = "reply"  # inline | reply | none
    allow_threads: bool = True
    max_thread_len: int = 5


class RepliesCfg(BaseModel):
    enabled: bool = True
    max_per_run: int = 2


class ComplianceCfg(BaseModel):
    affiliations: list[str] = Field(default_factory=list)
    avoid_tokens: list[str] = Field(default_factory=list)
    banned_phrases: list[str] = Field(default_factory=list)
    max_hashtags: int = 1
    max_emojis: int = 1


class TelegramCfg(BaseModel):
    enabled: bool = True


class Settings(BaseModel):
    handle: str = "0xYYcn"
    timezone: str = "UTC"
    language: str = "en"
    mode: str = "approve"  # draft | approve | auto
    models: dict[str, AgentModel] = Field(default_factory=dict)
    use_fallbacks: bool = True
    cadence: Cadence = Field(default_factory=Cadence)
    pipeline: PipelineCfg = Field(default_factory=PipelineCfg)
    posting: PostingCfg = Field(default_factory=PostingCfg)
    replies: RepliesCfg = Field(default_factory=RepliesCfg)
    compliance: ComplianceCfg = Field(default_factory=ComplianceCfg)
    telegram: TelegramCfg = Field(default_factory=TelegramCfg)

    def model_for(self, agent: str) -> AgentModel:
        return self.models.get(agent, AgentModel())


class Config:
    """Everything an agent or collector needs, loaded once per process."""

    def __init__(self, config_dir: Path | None = None):
        self.config_dir = Path(config_dir or CONFIG_DIR)
        _load_dotenv(ROOT / ".env")
        self.settings = Settings(**self._yaml("settings.yaml"))
        self.sources: dict[str, Any] = self._yaml("sources.yaml")
        self.state_dir = Path(os.environ.get("XBOT_STATE_DIR", ROOT / "state"))
        if not self.state_dir.is_absolute():
            self.state_dir = ROOT / self.state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _yaml(self, name: str) -> dict:
        path = self.config_dir / name
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text()) or {}

    @property
    def persona_path(self) -> Path:
        return self.config_dir / "persona.md"

    @property
    def exemplars_path(self) -> Path:
        return self.config_dir / "exemplars.json"

    def persona(self) -> str:
        return self.persona_path.read_text() if self.persona_path.exists() else ""

    @property
    def db_path(self) -> Path:
        return self.state_dir / "xbot.db"

    @staticmethod
    def env(name: str) -> str | None:
        value = os.environ.get(name)
        return value or None
