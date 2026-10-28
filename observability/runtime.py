"""Telemetry startup and shutdown, and the bot_status heartbeat loop"""

import asyncio
import logging
import os
import time

from . import recorders
from .client import INTERNAL_LOGGER_NAME, ClickHouseClient
from .config import ClickHouseSettings
from .handler import ClickHouseLogHandler
from .http_tracing import HttpTracer, instrument_aiohttp

_log = logging.getLogger(INTERNAL_LOGGER_NAME)

_client: ClickHouseClient | None = None
_settings: ClickHouseSettings | None = None
_handler: ClickHouseLogHandler | None = None
_heartbeat: asyncio.Task | None = None
_started_at = time.monotonic()


class _ProcessMetrics:
    def __init__(self):
        self._last_cpu = 0.0
        self._last_time = time.monotonic()
        try:
            self._ticks = os.sysconf("SC_CLK_TCK")
            self._page = os.sysconf("SC_PAGE_SIZE")
        except Exception:
            self._ticks, self._page = 100, 4096

    def mem_rss_mb(self) -> float:
        # Not implemented atm
        return 0.0

    def cpu_pct(self) -> float:
        # Not implemented atm
        return 0.0


_metrics = _ProcessMetrics()


async def _event_loop_lag_ms() -> float:
    """Measure asyncio loop lag, an indicator of saturation"""
    try:
        loop = asyncio.get_running_loop()
        started = loop.time()
        await asyncio.sleep(0.05)
        return round(max(0.0, (loop.time() - started - 0.05) * 1000), 1)
    except Exception:
        return 0.0


def setup(logger: logging.Logger, bot_version: str = "") -> ClickHouseSettings:
    """
    Call early, at discord_bot import: builds the client and attaches the handler
    """
    global _client, _settings, _handler
    if _settings is not None:
        return _settings

    _settings = ClickHouseSettings.from_env()
    _settings.bot_version = bot_version or ""
    _client = ClickHouseClient(_settings)
    recorders.bind(_client, _settings)

    # The internal logger must never reach the bot's own handlers
    _log.propagate = False
    if not _log.handlers:
        _log.addHandler(logging.StreamHandler())
    _log.setLevel(logging.INFO)

    if not _settings.enabled:
        _log.info("ClickHouse disabled (CLICKHOUSE_HOST is missing) : telemetry is off")
        return _settings

    level = getattr(logging, _settings.log_level, logging.INFO)
    _handler = ClickHouseLogHandler(_client, _settings, level=level)
    logger.addHandler(_handler)
    return _settings


async def start(bot=None) -> None:
    """Call from setup_hook: tests the server, starts the flusher and the heartbeat"""
    global _heartbeat
    if _client is None or _settings is None or not _settings.enabled:
        return
    try:
        if _settings.trace_http:
            instrument_aiohttp(HttpTracer(_client, _settings))
        await _client.start()
        if bot is not None and _heartbeat is None:
            _heartbeat = asyncio.create_task(_heartbeat_loop(bot), name="clickhouse_heartbeat")
        _log.info("ClickHouse telemetry started -> %s", _settings.masked_target())
    except Exception as error:
        _log.warning("Unable to start telemetry service (skipped) : %s", error)


async def stop() -> None:
    """Call from close(): flushes the buffer and shuts down cleanly"""
    global _heartbeat
    if _heartbeat is not None:
        _heartbeat.cancel()
        try:
            await _heartbeat
        except (asyncio.CancelledError, Exception):
            pass
        _heartbeat = None
    if _client is not None:
        try:
            recorders.sweep_pending()
            await _client.stop()
        except Exception:
            pass


def get_client() -> ClickHouseClient | None:
    return _client


def get_settings() -> ClickHouseSettings | None:
    return _settings


def collect_status(bot) -> dict:
    """Collect the bot_status gauges from the live bot state"""
    from utils import cache_mngr  # Remplacement de CACHE par cache_mngr

    try:
        latency = bot.latency
        latency_ms = round(latency * 1000, 1) if latency == latency else 0.0  # NaN-safe
    except Exception:
        latency_ms = 0.0

    tasks = {}
    for name in (
        "flag_watcher_task",
        "status_task",
        "sync_topgg_votes_task",
        "post_server_count_task",
        "update_servers_task",
    ):
        loop = getattr(bot, name, None)
        if loop is not None:
            try:
                tasks[name] = int(bool(loop.is_running()))
            except Exception:
                pass

    maintenance = bool(getattr(bot, "maintenance_mode", False))
    cached_players = 0
    try:
        # Adaptation pour utiliser cache_mngr.server_data
        cached_players = sum(len(v.get("players", [])) for v in cache_mngr.server_data.values())
    except Exception:
        pass

    return {
        "state": "maintenance" if maintenance else "online",
        "maintenance": int(maintenance),
        "ready": int(bool(bot.is_ready())),
        "guild_count": len(bot.guilds),
        "user_count": sum(g.member_count or 0 for g in bot.guilds),
        "channel_count": sum(len(g.channels) for g in bot.guilds),
        "gateway_latency_ms": latency_ms,
        "uptime_s": int(time.monotonic() - _started_at),
        "mem_rss_mb": _metrics.mem_rss_mb(),
        "cpu_pct": _metrics.cpu_pct(),
        "cogs_loaded": len(bot.cogs),
        "tasks_running": tasks,
        "cache_servers": len(cache_mngr.server_data),
        "cache_players": cached_players,
        "scan_flag_active": int(bool(getattr(bot, "scan_flag_detected", False))),
    }


async def _heartbeat_loop(bot) -> None:
    """Write one bot_status row at a regular interval"""
    await bot.wait_until_ready()
    while True:
        try:
            await asyncio.sleep(_settings.heartbeat_s)
            gauges = collect_status(bot)
            gauges["event_loop_lag_ms"] = await _event_loop_lag_ms()
            gauges.update(await _data_gauges())  # Rendu awaitable pour appeler les fonctions asynchrones
            recorders.record_bot_status(**gauges)
            recorders.sweep_pending()
        except asyncio.CancelledError:
            raise
        except Exception:
            # The heartbeat must never kill its own task
            pass


async def _data_gauges() -> dict:
    """Gauges taken from the JSON files: radar, fortresses, votes"""
    from datetime import datetime

    # Import des fonctions asynchrones depuis utils
    from utils import JOUEURS_DIR, async_json_load, load_dungeons_async, load_surveillance_async

    gauges = {}
    try:
        # Utilisation de la nouvelle fonction asynchrone non-bloquante
        data = await load_surveillance_async()
        gauges["radar_players"] = len(data.get("players", {}))
        gauges["radar_alliances"] = len(data.get("alliances", {}))
    except Exception as e:
        _log.error(f"❌ [Metrics] Erreur load_surveillance_async : {e}")

    try:
        # Utilisation de la nouvelle fonction asynchrone non-bloquante
        data = await load_dungeons_async()
        gauges["fortress_sessions"] = len(data.get("sessions", {}))
    except Exception as e:
        _log.error(f"❌ [Metrics] Erreur load_dungeons_async : {e}")

    try:
        path = JOUEURS_DIR / "votes.json"
        if path.exists():
            # Utilisation de async_json_load générique
            votes = await async_json_load(path)
            now = datetime.now()
            active = 0
            for expiry in votes.values():
                try:
                    if datetime.fromisoformat(str(expiry)) > now:
                        active += 1
                except Exception:
                    pass
            gauges["active_shields"] = active
    except Exception as e:
        _log.error(f"❌ [Metrics] Erreur lecture des votes : {e}")

    return gauges
