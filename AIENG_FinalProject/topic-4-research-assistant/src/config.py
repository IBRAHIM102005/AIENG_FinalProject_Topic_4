"""
Async Research Assistant — Configuration Layer
AIENG Final Project / topic-4-research-assistant

Central configuration system for the entire application.
Handles environment loading, validation, and runtime-safe settings access.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application-wide configuration schema.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # =========================
    # LLM CONFIGURATION
    # =========================
    llm_provider: Literal["anthropic", "openai", "gemini"] = "anthropic"
    llm_model: str = "claude-sonnet-4-6"

    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    google_api_key: Optional[str] = None

    # =========================
    # WEB SEARCH CONFIGURATION
    # =========================
    web_search_provider: Literal["tavily", "serper", "duckduckgo"] = "tavily"

    tavily_api_key: Optional[str] = None
    serper_api_key: Optional[str] = None

    # =========================
    # RUNTIME CONFIG
    # =========================
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    cache_dir: Path = Path(".cache")
    cache_ttl_seconds: int = Field(default=86400, ge=0)

    per_source_timeout_seconds: float = Field(default=10.0, gt=0)
    max_sources_per_query: int = Field(default=3, ge=1, le=10)
    max_question_length: int = Field(default=1000, gt=0)

    # =========================
    # RETRY POLICY
    # =========================
    retry_max_attempts: int = Field(default=3, ge=1)
    retry_min_wait_seconds: float = Field(default=1.0, ge=0)
    retry_max_wait_seconds: float = Field(default=30.0, ge=1)

    # =========================
    # CONCURRENCY
    # =========================
    concurrency_semaphore_limit: int = Field(default=5, ge=1)

    # =========================
    # VALIDATORS
    # =========================
    @field_validator("cache_dir", mode="before")
    @classmethod
    def _coerce_cache_dir(cls, v: Any) -> Path:
        return Path(v)

    @field_validator("llm_model")
    @classmethod
    def _validate_model(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("llm_model cannot be empty")
        return v

    # =========================
    # RUNTIME HELPERS
    # =========================
    def ensure_cache_dir(self) -> None:
        """
        Create cache directory if it does not exist.
        Explicit call only (test-safe).
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # =========================
    # API KEY RESOLUTION
    # =========================
    def active_llm_api_key(self) -> str:
        """
        Returns active LLM API key based on provider.
        """
        if self.llm_provider == "anthropic":
            key = self.anthropic_api_key
        elif self.llm_provider == "openai":
            key = self.openai_api_key
        elif self.llm_provider == "gemini":
            key = self.google_api_key
        else:
            raise RuntimeError(f"Unsupported LLM provider: {self.llm_provider}")

        if not key:
            raise RuntimeError(
                f"Missing API key for LLM provider: {self.llm_provider}"
            )

        return key

    def active_search_api_key(self) -> str:
        """
        Returns API key for search provider.
        DuckDuckGo does not require a key.
        """
        if self.web_search_provider == "tavily":
            key = self.tavily_api_key
        elif self.web_search_provider == "serper":
            key = self.serper_api_key
        elif self.web_search_provider == "duckduckgo":
            return ""
        else:
            raise RuntimeError(
                f"Unsupported search provider: {self.web_search_provider}"
            )

        if not key:
            raise RuntimeError(
                f"Missing API key for search provider: {self.web_search_provider}"
            )

        return key

    def active_web_search_api_key(self) -> str | None:
        key_map = {
            "tavily": self.tavily_api_key,
            "serper": self.serper_api_key,
            "duckduckgo": None,
        }
        return key_map.get(self.web_search_provider)

    # =========================
    # ENVIRON BRIDGE
    # =========================
    def export_to_environ(self) -> None:
        """
        Export settings values into os.environ so that the ai/ package layer
        (which calls os.getenv() directly) can read them.

        pydantic-settings reads .env into this Settings object but does NOT
        write those values back to os.environ.  The ai/ providers — which we
        must not modify — rely on os.getenv("OPENAI_API_KEY") etc., so we
        bridge the gap here.  Only sets keys that are not already present so
        that real environment variables always take precedence.
        """
        bridge: dict[str, str | None] = {
            "LLM_PROVIDER":          self.llm_provider,
            "LLM_MODEL":             self.llm_model,
            "OPENAI_API_KEY":        self.openai_api_key,
            "ANTHROPIC_API_KEY":     self.anthropic_api_key,
            "GOOGLE_API_KEY":        self.google_api_key,
            "WEB_SEARCH_PROVIDER":   self.web_search_provider,
            "TAVILY_API_KEY":        self.tavily_api_key,
            "SERPER_API_KEY":        self.serper_api_key,
        }
        for key, value in bridge.items():
            if value is not None and key not in os.environ:
                os.environ[key] = value


# =========================
# SINGLETON ACCESS
# =========================
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns cached application settings instance.
    Also bridges .env values into os.environ for the ai/ package layer.
    """
    settings = Settings()
    settings.export_to_environ()
    return settings