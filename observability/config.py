"""
ClickHouse settings
"""

import logging
import os
import socket
from dataclasses import dataclass, field

_log = logging.getLogger("GGE_Bot")


def _env_str(key: str, default: str = "") -> str:
    value = os.getenv(key)
    return value.strip() if value and value.strip() else default


def _env_int(key: str, default: int) -> int:
    val_str = _env_str(key, str(default))
    try:
        return int(val_str)
    except (TypeError, ValueError) as e:
        _log.warning(
            f"⚠️ [Metrics] Valeur invalide pour '{key}' ('{val_str}'). Utilisation de la valeur par défaut : {default}"
        )
        return default


def _env_bool(key: str, default: bool) -> bool:
    value = _env_str(key, "").lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on", "oui")


@dataclass
class ClickHouseSettings:
    """Connection and buffering settings"""

    enabled: bool = True
    url: str = ""
    database: str = "gge_assistant_bot"
    user: str = ""
    password: str = ""
    timeout_s: int = 10

    flush_interval_s: float = 5.0
    batch_size: int = 3000
    max_entries: int = 20000
    max_bytes: int = 8 * 1024 * 1024
    max_retries: int = 3
    retry_base_ms: int = 200

    probe_interval_s: int = 60

    trace_http: bool = True
    trace_discord_api: bool = False
    heartbeat_s: int = 60
    log_level: str = "INFO"
    async_insert: bool = True

    instance: str = ""
    bot_version: str = ""
    tables: frozenset = field(default_factory=frozenset)

    @classmethod
    def from_env(cls) -> "ClickHouseSettings":
        url = _env_str("CLICKHOUSE_URL")
        if not url:
            host = _env_str("CLICKHOUSE_HOST")
            if host:
                scheme = _env_str("CLICKHOUSE_SCHEME", "http")
                port = _env_int("CLICKHOUSE_PORT", 8123)
                url = f"{scheme}://{host}:{port}"
        url = url.rstrip("/")

        # No host configured: telemetry off and no error raised
        enabled = _env_bool("CLICKHOUSE_ENABLED", True) and bool(url)

        try:
            hostname = socket.gethostname()
        except Exception as e:
            _log.error(f"❌ [Metrics] Impossible de récupérer le nom d'hôte local : {e}")
            hostname = "unknown"

        return cls(
            enabled=enabled,
            url=url,
            database=_env_str("CLICKHOUSE_DATABASE", "gge_assistant_bot"),
            user=_env_str("CLICKHOUSE_USER", "default"),
            password=_env_str("CLICKHOUSE_PASSWORD", ""),
            timeout_s=_env_int("CLICKHOUSE_TIMEOUT_S", 10),
            flush_interval_s=max(1.0, float(_env_int("CLICKHOUSE_FLUSH_INTERVAL_S", 5))),
            batch_size=max(1, _env_int("CLICKHOUSE_BATCH_SIZE", 3000)),
            max_entries=max(100, _env_int("CLICKHOUSE_MAX_ENTRIES", 20000)),
            max_bytes=max(64 * 1024, _env_int("CLICKHOUSE_MAX_BYTES", 8 * 1024 * 1024)),
            max_retries=max(1, _env_int("CLICKHOUSE_MAX_RETRIES", 3)),
            retry_base_ms=max(50, _env_int("CLICKHOUSE_RETRY_BASE_MS", 200)),
            probe_interval_s=max(10, _env_int("CLICKHOUSE_PROBE_INTERVAL_S", 60)),
            trace_http=_env_bool("CLICKHOUSE_TRACE_HTTP", True),
            trace_discord_api=_env_bool("CLICKHOUSE_TRACE_DISCORD_API", False),
            heartbeat_s=max(15, _env_int("CLICKHOUSE_HEARTBEAT_S", 60)),
            log_level=_env_str("CLICKHOUSE_LOG_LEVEL", "INFO").upper(),
            async_insert=_env_bool("CLICKHOUSE_ASYNC_INSERT", True),
            instance=_env_str("CLICKHOUSE_INSTANCE", hostname),
        )

    def masked_target(self) -> str:
        return f"{self.url}/{self.database} (user={self.user or 'default'})"
