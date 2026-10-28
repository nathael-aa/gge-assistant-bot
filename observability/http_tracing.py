"""
aiohttp instrumentation filling api_requests without touching the cogs
"""

import re
import time
from urllib.parse import unquote

import aiohttp

from .context import get_command_context, get_task_name, get_trace_id, record_api_call

# Host to service classification
_SERVICES: tuple[tuple[str, str], ...] = (
    ("api-beta.gge-tracker.com", "gge_tracker_beta"),
    ("api.gge-tracker.com", "gge_tracker"),
    ("empire-api.fly.dev", "empire_api"),
    ("communityhub.goodgamestudios.com", "gge_communityhub"),
    ("top.gg", "topgg"),
    ("goodgamestudios.com", "gge_xml"),
    ("langserver.public.ggs-net.com", "gge_xml"),
)

# Words belonging to the route itself (they are never replaced)
_ROUTE_WORDS = {
    "api",
    "v1",
    "players",
    "player",
    "alliances",
    "alliance",
    "statistics",
    "dungeons",
    "meta",
    "events",
    "date",
    "id",
    "name",
    "woa",
    "aquamarine",
    "leaderboard",
    "server",
    "movements",
    "castles",
    "history",
    "search",
    "analysis",
    "hgh",
    "profile",
    "ranking",
    "stats",
    "bots",
    "webhooks",
    "check",
    "votes",
    "wall",
    "walls",
    "top",
    "current",
    "list",
}
# A segment following one of these is a variable, and its shape picks the placeholder:
# /statistics/player/123 -> {id} but /EmpireEx_0/player/Nath -> {name}
_VARIABLE_PARENTS = {
    "id",
    "player",
    "players",
    "alliance",
    "alliances",
    "name",
    "statistics",
    "dungeons",
    "castles",
}

_RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_RE_HEXA = re.compile(r"^[0-9a-fA-F]{16,}$")
_RE_SERVER = re.compile(r"^(EmpireEx_{0,1}\d*|E4K_[A-Z0-9]+|EmpirefourkingdomsExGG_{0,1}\d*|[A-Z]{2,5}\d)$")


def classify_service(host: str, path: str) -> str:
    """Derive the service column from the host"""
    host = (host or "").lower()
    for suffix, service in _SERVICES:
        if host == suffix or host.endswith("." + suffix) or suffix in host:
            return service
    if "discord" in host:
        return "discord_webhook" if "/api/webhooks/" in path else "discord_api"
    return "other"


def normalize_endpoint(path: str, service: str = "") -> str:
    """
    Normalised route, to keep the cardinality of endpoint low,cf:
    /api/v1/alliances/id/1234          -> /alliances/id/{id}
    /api/v1/players/Player123456789    -> /players/{name}
    /api/v1/woa/events/date/2026-01-01 -> /woa/events/date/{date}
    /api/v1/EmpireEx_10/player/Nath    -> /{server}/player/{name}
    """
    if service == "discord_webhook":
        return "/api/webhooks/{id}/{token}"

    route = unquote(path or "/")
    if route.startswith("/api/v1"):
        route = route[len("/api/v1") :] or "/"

    parts: list[str] = []
    previous = ""
    for segment in route.split("/"):
        if not segment:
            parts.append("")
            continue
        lowered = segment.lower()

        if segment.isdigit():
            normalised = "{id}"
        elif _RE_DATE.match(segment):
            normalised = "{date}"
        elif _RE_SERVER.match(segment):
            normalised = "{server}"
        elif lowered in _ROUTE_WORDS:
            normalised = lowered
        elif previous in _VARIABLE_PARENTS:
            normalised = "{name}"
        elif _RE_HEXA.match(segment) or len(segment) > 40:
            normalised = "{id}"
        else:
            normalised = segment[:60]

        parts.append(normalised)
        previous = lowered

    return ("/".join(parts) or "/")[:200]


def _status_class(code: int) -> str:
    if code == 0:
        return "network"
    if code >= 500:
        return "5xx"
    if code >= 400:
        return "4xx"
    if code >= 300:
        return "3xx"
    return "2xx"


class HttpTracer:
    """Build api_requests rows from aiohttp events"""

    def __init__(self, client, settings):
        self.client = client
        self.settings = settings

    async def _on_start(self, session, context, params):
        context.gge_start = time.monotonic()
        context.gge_skip = getattr(session, "_gge_obs_internal", False)

    async def _on_end(self, session, context, params):
        if getattr(context, "gge_skip", False):
            return
        response = params.response
        self._emit(
            params.method,
            params.url,
            getattr(context, "gge_start", None),
            status_code=response.status,
            response_bytes=response.content_length or 0,
            etag_sent="If-None-Match" in (params.headers or {}),
        )

    async def _on_exception(self, session, context, params):
        if getattr(context, "gge_skip", False):
            return
        error = params.exception
        self._emit(
            params.method,
            params.url,
            getattr(context, "gge_start", None),
            status_code=0,
            error_class=type(error).__name__,
            error_message=str(error)[:500],
            etag_sent="If-None-Match" in (params.headers or {}),
        )

    def _emit(
        self,
        method: str,
        url,
        started: float | None,
        status_code: int,
        response_bytes: int = 0,
        error_class: str = "",
        error_message: str = "",
        etag_sent: bool = False,
    ) -> None:
        try:
            duration_ms = int((time.monotonic() - started) * 1000) if started else 0
            host = url.host or ""
            path = url.path or "/"
            service = classify_service(host, path)

            if service == "discord_api" and not self.settings.trace_discord_api:
                return

            record_api_call(duration_ms)
            context = get_command_context()
            task = get_task_name()

            query_params = {}
            try:
                for key, value in url.query.items():
                    query_params[str(key)] = str(value)[:200]
            except Exception:
                pass

            from .client import ch_datetime64

            row = {
                "ts": ch_datetime64(),
                "trace_id": get_trace_id(),
                "direction": "outbound",
                "service": service,
                "method": (method or "GET").upper(),
                "host": host,
                "endpoint": normalize_endpoint(path, service),
                "query": query_params,
                "gge_server": context.gge_server if context else "",
                "status_code": status_code,
                "status_class": _status_class(status_code),
                "ok": 1 if 200 <= status_code < 400 else 0,
                "duration_ms": duration_ms,
                "rate_limited": 1 if status_code == 429 else 0,
                "not_modified": 1 if status_code == 304 else 0,
                "etag_sent": 1 if etag_sent else 0,
                "response_bytes": int(response_bytes or 0),
                "error_class": error_class,
                "error_message": error_message,
                "origin": "command" if context else ("task" if task else "internal"),
                "command": context.command if context else "",
                "cog": context.cog if context else "",
                "task_name": task,
                "user_id": context.user_id if context else 0,
                "guild_id": context.guild_id if context else 0,
                "bot_version": self.settings.bot_version,
                "instance": self.settings.instance,
            }
            self.client.enqueue("api_requests", row)
        except Exception as e:
            # We add a log for tracking failed emissions which were previously swallowed.
            import logging

            logger = logging.getLogger("GGE_Bot")
            logger.error(f"❌ [Metrics] Échec de la collecte d'un événement API : {e}")

    def build_trace_config(self) -> aiohttp.TraceConfig:
        trace_config = aiohttp.TraceConfig()
        trace_config.on_request_start.append(self._on_start)
        trace_config.on_request_end.append(self._on_end)
        trace_config.on_request_exception.append(self._on_exception)
        return trace_config


_patched = False


def instrument_aiohttp(tracer: HttpTracer) -> None:
    global _patched
    if _patched:
        return

    original_init = aiohttp.ClientSession.__init__

    def instrumented_init(self, *args, **kwargs):
        try:
            traces = list(kwargs.get("trace_configs") or [])
            traces.append(tracer.build_trace_config())
            kwargs["trace_configs"] = traces
        except Exception:
            pass
        return original_init(self, *args, **kwargs)

    aiohttp.ClientSession.__init__ = instrumented_init  # type: ignore[method-assign]
    _patched = True
