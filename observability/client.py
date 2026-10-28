"""
ClickHouse client: bounded memory buffer, batched JSONEachRow inserts
Never runs DDL and never raises towards the bot: when the server
is unreachable, the rows are simply dropped
"""

import asyncio
import json
import logging
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import aiohttp

from .config import ClickHouseSettings

# Kept apart from the "GGE_Bot" logger
INTERNAL_LOGGER_NAME = "gge_observability"
_log = logging.getLogger(INTERNAL_LOGGER_NAME)

_FORMAT_DATETIME64 = "%Y-%m-%d %H:%M:%S.%f"
_FORMAT_DATETIME = "%Y-%m-%d %H:%M:%S"


def ch_datetime64(when: datetime | None = None) -> str:
    """Millisecond timestamp in the format expected by DateTime64(3)"""
    return (when or datetime.now()).strftime(_FORMAT_DATETIME64)[:-3]


def ch_datetime(when: datetime | None = None) -> str:
    """Second timestamp in the format expected by DateTime"""
    return (when or datetime.now()).strftime(_FORMAT_DATETIME)


class ClickHouseClient:
    def __init__(self, settings: ClickHouseSettings):
        self.settings = settings
        self._buffers: dict[str, deque] = {}
        self._bytes = 0
        self._lock = threading.Lock()
        self._flush_lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None
        self._flusher: asyncio.Task | None = None
        self._closing = False

        # Circuit breaker: rows are dropped while ClickHouse does not answer
        self._available = False
        self._next_probe = 0.0
        self._announced_down = False

        # Self-observation counters, exposed through get_stats()
        self.rows_sent = 0
        self.rows_dropped_full = 0
        self.rows_dropped_unavailable = 0
        self.rows_dropped_failed = 0
        self.flush_failures = 0

    async def start(self) -> None:
        """Open the HTTP session, test the connection, start the flusher"""
        if not self.settings.enabled or self._flusher is not None:
            return

        timeout = aiohttp.ClientTimeout(total=self.settings.timeout_s)
        auth = aiohttp.BasicAuth(self.settings.user, self.settings.password)
        self._session = aiohttp.ClientSession(timeout=timeout, auth=auth)
        # Read by the HTTP tracer: these requests must never be traced, or they would loop
        self._session._gge_obs_internal = True

        await self.ping()
        self._flusher = asyncio.create_task(self._flush_loop(), name="clickhouse_flusher")

    async def stop(self) -> None:
        """Flush the buffer one last time, then close the session"""
        self._closing = True
        if self._flusher:
            self._flusher.cancel()
            try:
                await self._flusher
            except (asyncio.CancelledError, Exception):
                pass
            self._flusher = None
        try:
            await self.flush()
        except Exception:
            pass
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
        self._session = None

    async def ping(self) -> bool:
        r"""Test the server. /!\ Never raises: a failure only puts the telemetry to sleep"""
        if not self.settings.enabled or self._session is None:
            return False
        try:
            url = f"{self.settings.url}/?query={'SELECT%201'}"
            async with self._session.get(url) as response:
                await response.read()
                reachable = 200 <= response.status < 300
        except Exception:
            reachable = False

        self._next_probe = time.monotonic() + self.settings.probe_interval_s
        if reachable and not self._available:
            _log.info("ClickHouse OK : %s", self.settings.masked_target())
            self._announced_down = False
        elif not reachable and not self._announced_down:
            # One line when the server goes down, not one per flush
            _log.warning(
                "ClickHouse error (%s)",
                self.settings.masked_target(),
            )
            self._announced_down = True
        self._available = reachable
        return reachable

    @property
    def available(self) -> bool:
        return self._available

    def enqueue(self, table: str, row: dict[str, Any]) -> None:
        """Queue one row. Non-blocking, thread-safe, never raises"""
        if not self.settings.enabled or self._closing:
            return
        try:
            payload = json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str)
        except Exception:
            return

        size = len(payload)
        with self._lock:
            queue = self._buffers.get(table)
            if queue is None:
                queue = self._buffers[table] = deque()

            # Full buffer: evict the oldest rows so the memory footprint stays constant
            queued = sum(len(d) for d in self._buffers.values())
            while queued >= self.settings.max_entries or self._bytes + size > self.settings.max_bytes:
                evicted = self._evict_oldest_locked()
                if evicted is None:
                    break
                queued -= 1

            queue.append(payload)
            self._bytes += size

    def _evict_oldest_locked(self) -> str | None:
        """Drop one row from the longest queue. Must be called while holding the lock"""
        target_table, longest = None, 0
        for name, queue in self._buffers.items():
            if len(queue) > longest:
                target_table, longest = name, len(queue)
        if target_table is None or longest == 0:
            return None
        evicted = self._buffers[target_table].popleft()
        self._bytes -= len(evicted)
        self.rows_dropped_full += 1
        return evicted

    async def _flush_loop(self) -> None:
        while not self._closing:
            try:
                await asyncio.sleep(self.settings.flush_interval_s)
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # The flusher must never die
                self.flush_failures += 1
                _log.error(f"❌ [Metrics] Erreur dans le flush_loop : {e}")

    async def flush(self) -> None:
        """Send the whole buffer. Silent when ClickHouse is unavailable"""
        if not self.settings.enabled or self._session is None:
            return

        async with self._flush_lock:
            with self._lock:
                if not any(self._buffers.values()):
                    return
                snapshot = {name: list(f) for name, f in self._buffers.items() if f}
                for queue in self._buffers.values():
                    queue.clear()
                self._bytes = 0

            # Server down: re-ping from time to time, drop the rows in the meantime
            if not self._available and time.monotonic() < self._next_probe:
                self.rows_dropped_unavailable += sum(len(v) for v in snapshot.values())
                return
            if not self._available and not await self.ping():
                self.rows_dropped_unavailable += sum(len(v) for v in snapshot.values())
                return

            for table, rows in snapshot.items():
                for started in range(0, len(rows), self.settings.batch_size):
                    batch = rows[started : started + self.settings.batch_size]
                    if await self._send_with_retry(table, batch):
                        self.rows_sent += len(batch)
                    else:
                        self.rows_dropped_failed += len(batch)

    def _build_url(self, table: str) -> str:
        statement = f"INSERT INTO {self.settings.database}.{table} FORMAT JSONEachRow"
        parameters = {
            "query": statement,
            # Schema drift tolerance: an unknown or missing column must not fail a log insert
            "input_format_skip_unknown_fields": "1",
            "input_format_defaults_for_omitted_fields": "1",
            "date_time_input_format": "best_effort",
        }
        if self.settings.async_insert:
            parameters["async_insert"] = "1"
            parameters["wait_for_async_insert"] = "0"
        return f"{self.settings.url}/?{urlencode(parameters)}"

    async def _send_with_retry(self, table: str, batch: list[str]) -> bool:
        url = self._build_url(table)
        payload = "\n".join(batch)
        for attempt in range(1, self.settings.max_retries + 1):
            try:
                await self._send(url, payload)
                if not self._available:
                    self._available = True
                    self._announced_down = False
                return True
            except Exception as error:
                if attempt == self.settings.max_retries:
                    self.flush_failures += 1
                    self._available = False
                    self._next_probe = time.monotonic() + self.settings.probe_interval_s
                    if not self._announced_down:
                        _log.warning(
                            "Failed ClickHouse insertion %s (%d lines) : %s",
                            table,
                            len(batch),
                            error,
                        )
                        self._announced_down = True
                    return False
                await asyncio.sleep((self.settings.retry_base_ms / 1000) * (2 ** (attempt - 1)))
        return False

    async def _send(self, url: str, payload: str) -> None:
        assert self._session is not None
        async with self._session.post(
            url,
            data=payload.encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
        ) as response:
            body = await response.text()
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"ClickHouse HTTP {response.status}: {body[:300]}")

    # ------------------------------------------------------------------
    def get_stats(self) -> dict[str, int]:
        with self._lock:
            pending_rows = sum(len(f) for f in self._buffers.values())
            size_bytes = self._bytes
        return {
            "buffered_rows": pending_rows,
            "buffered_bytes": size_bytes,
            "rows_sent": self.rows_sent,
            "rows_dropped_full": self.rows_dropped_full,
            "rows_dropped_unavailable": self.rows_dropped_unavailable,
            "rows_dropped_failed": self.rows_dropped_failed,
            "flush_failures": self.flush_failures,
            "available": int(self._available),
        }
