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

    model_config = SettingsConfigDict(
        env_prefix="DB2_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database: str = Field(..., description="DB2 database name")
    hostname: str = Field(..., description="DB2 z/OS hostname or IP")
    port: int = Field(default=446, description="DB2 port (typically 446)")
    uid: str = Field(..., description="DB2 user ID")
    pwd: str = Field(..., description="DB2 password", repr=False)
    jdbc_driver_path: str | None = Field(
        default=None, description="Path to DB2 JDBC driver JAR file (for JDBC connections)"
    )
    use_mock: bool = Field(
        default=False, description="Force mock DB2 connection for testing (no real database required)"
    )

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        return v


class SSHTunnelSettings(BaseSettings):
    """SSH tunnel settings for PostgreSQL access."""

    model_config = SettingsConfigDict(
        env_prefix="SSH_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    tunnel_enabled: bool = Field(default=False, description="Enable SSH tunnel for PostgreSQL")
    host: str = Field(default="localhost", description="SSH server hostname")
    port: int = Field(default=22, description="SSH server port")
    user: str = Field(default="", description="SSH username")
    password: str | None = Field(default=None, description="SSH password", repr=False)
    key_file: str | None = Field(default=None, description="Path to SSH private key file")

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        return v


class PostgresSettings(BaseSettings):
    """PostgreSQL target database settings."""

    model_config = SettingsConfigDict(
        env_prefix="PG_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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

    def connection_string(self, host: str | None = None, port: int | None = None) -> str:
        """
        Generate PostgreSQL connection string.

        Args:
            host: Override host (useful for SSH tunnel local endpoint)
            port: Override port (useful for SSH tunnel local port)
        """
        conn_host = host or self.host
        conn_port = port or self.port
        return f"postgresql://{self.user}:{self.password}@{conn_host}:{conn_port}/{self.database}"


class MigrationSettings(BaseSettings):
    """Migration execution settings."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment mode - controls default behaviors for safety
    mode: Literal["production", "development"] = Field(
        default="development",
        description="Environment mode: 'production' (safe defaults) or 'development' (convenient defaults)"
    )

    # Parallelism settings
    min_workers: int = Field(default=2, description="Minimum number of worker processes")
    max_workers: int = Field(default=20, description="Maximum number of worker processes")
    chunk_size: int = Field(default=500_000, description="Default rows per chunk for large tables")

    # Loading settings
    use_streaming: bool = Field(
        default=True, description="Stream data directly from DB2 to PostgreSQL (no staging files)"
    )
    drop_indexes_threshold: int = Field(
        default=1, description="Drop indexes if table has more than this many CSV chunks (file mode only)"
    )
    if_exists: Literal["truncate", "error", "append"] | None = Field(
        default=None,
        description="How to handle existing data. If not set, defaults based on mode: production='error', development='truncate'"
    )
    skip_loading: bool = Field(
        default=False, description="Skip loading phase (extraction only, file mode only)"
    )

    @property
    def effective_if_exists(self) -> Literal["truncate", "error", "append"]:
        """Get the effective if_exists value based on mode if not explicitly set."""
        if self.if_exists is not None:
            return self.if_exists
        # Default based on mode: production is safe (error), development is convenient (truncate)
        return "error" if self.mode == "production" else "truncate"

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
    ssh_tunnel: SSHTunnelSettings = Field(default_factory=SSHTunnelSettings)
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
