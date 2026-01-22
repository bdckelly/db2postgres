"""
Configuration management for PS82-to-Postgres migration tool.

Uses pydantic-settings for environment variable validation and type safety.
All settings can be overridden via environment variables or .env file.
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DB2Settings(BaseSettings):
    """DB2 z/OS connection settings."""

    model_config = SettingsConfigDict(env_prefix="DB2_", case_sensitive=False)

    database: str = Field(..., description="DB2 database name")
    hostname: str = Field(..., description="DB2 z/OS hostname or IP")
    port: int = Field(default=446, description="DB2 port (typically 446)")
    uid: str = Field(..., description="DB2 user ID")
    pwd: str = Field(..., description="DB2 password", repr=False)

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        return v


class PostgresSettings(BaseSettings):
    """PostgreSQL target database settings."""

    model_config = SettingsConfigDict(env_prefix="PG_", case_sensitive=False)

    host: str = Field(default="localhost", description="PostgreSQL hostname")
    port: int = Field(default=5432, description="PostgreSQL port")
    database: str = Field(..., description="Target database name")
    user: str = Field(..., description="PostgreSQL user")
    password: str = Field(..., description="PostgreSQL password", repr=False)

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        return v

    def connection_string(self) -> str:
        """Generate PostgreSQL connection string."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


class MigrationSettings(BaseSettings):
    """Migration execution settings."""

    model_config = SettingsConfigDict(case_sensitive=False)

    # Parallelism settings
    min_workers: int = Field(default=2, description="Minimum number of worker processes")
    max_workers: int = Field(default=20, description="Maximum number of worker processes")
    chunk_size: int = Field(default=500_000, description="Default rows per chunk for large tables")

    # Directory paths
    staging_dir: Path = Field(
        default=Path("data/staging"), description="Staging directory for CSV files"
    )
    checkpoint_dir: Path = Field(
        default=Path("data/checkpoints"), description="Checkpoint directory for restart state"
    )
    log_dir: Path = Field(default=Path("logs"), description="Log directory")

    # Logging configuration
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Logging level"
    )
    log_format: Literal["json", "console"] = Field(
        default="console", description="Log output format"
    )

    @field_validator("min_workers", "max_workers")
    @classmethod
    def validate_workers(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Worker count must be at least 1")
        if v > 50:
            raise ValueError("Worker count should not exceed 50")
        return v

    @field_validator("chunk_size")
    @classmethod
    def validate_chunk_size(cls, v: int) -> int:
        if v < 1000:
            raise ValueError("Chunk size must be at least 1,000 rows")
        if v > 10_000_000:
            raise ValueError("Chunk size should not exceed 10,000,000 rows")
        return v

    @field_validator("staging_dir", "checkpoint_dir", "log_dir")
    @classmethod
    def ensure_path(cls, v: Path) -> Path:
        """Ensure directory exists."""
        v.mkdir(parents=True, exist_ok=True)
        return v

    def model_post_init(self, __context):
        """Validate worker bounds after initialization."""
        if self.min_workers > self.max_workers:
            raise ValueError(
                f"min_workers ({self.min_workers}) must not exceed max_workers ({self.max_workers})"
            )


class Settings(BaseSettings):
    """
    Master settings class combining all configuration sections.

    Loads from environment variables and .env file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    db2: DB2Settings = Field(default_factory=DB2Settings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    migration: MigrationSettings = Field(default_factory=MigrationSettings)


# Global settings instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """
    Get or create global settings instance.

    Returns:
        Settings: Global settings object loaded from environment
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    """
    Reload settings from environment (useful for testing).

    Returns:
        Settings: Freshly loaded settings object
    """
    global _settings
    _settings = Settings()
    return _settings
