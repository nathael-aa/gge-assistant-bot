"""
Logging handler mirroring the existing logger into the logs table
"""

import logging
import re

from .client import INTERNAL_LOGGER_NAME, ch_datetime64
from .context import get_command_context, get_trace_id

# Création d'une instance sécurisée du logger interne
_log = logging.getLogger(INTERNAL_LOGGER_NAME)

# Project messages already carry a tag such as [COMMANDE] or [Radar Spy]
# So, it is reused as-is to fill the category column and filter on it
_RE_CATEGORY = re.compile(r"\[([^\]\[]{1,40})\]")


def extract_category(message: str) -> str:
    match = _RE_CATEGORY.search(message or "")
    return match.group(1).strip()[:40] if match else ""


class ClickHouseLogHandler(logging.Handler):
    """Write every log record into the ClickHouse buffer"""

    def __init__(self, client, settings, level=logging.INFO):
        super().__init__(level=level)
        self.client = client
        self.settings = settings

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Recursion guard: the ClickHouse client's own logs never go back to ClickHouse
            if record.name.startswith(INTERNAL_LOGGER_NAME):
                return

            message = record.getMessage()
            context = get_command_context()

            module_name = record.module or ""
            path = record.pathname or ""
            if "/cogs/" in path or "\\cogs\\" in path:
                module_name = f"cogs.{module_name}"

            row = {
                "ts": ch_datetime64(),
                "level": record.levelname,
                "logger_name": record.name,
                "module": module_name[:64],
                "func": (record.funcName or "")[:64],
                "line": int(record.lineno or 0),
                "category": extract_category(message),
                "message": message[:8000],
                "trace_id": get_trace_id(),
                "user_id": context.user_id if context else 0,
                "guild_id": context.guild_id if context else 0,
                "gge_server": context.gge_server if context else "",
                "bot_version": self.settings.bot_version,
                "instance": self.settings.instance,
                "extra": self._extract_extra(record),
            }

            # Exceptions logged with exc_info keep their type here; full detail goes to the errors table
            if record.exc_info and record.exc_info[0] is not None:
                row["extra"]["exc_type"] = record.exc_info[0].__name__

            self.client.enqueue("logs", row)
        except Exception as e:
            # A logging handler must never break its caller, but we should track failures
            _log.error(f"❌ [Metrics] Échec d'écriture dans le LogHandler : {e}")

    @staticmethod
    def _extract_extra(record: logging.LogRecord) -> dict:
        """Read a dict passed as logger.info(msg, extra={"gge": {...}})"""
        extra_data = getattr(record, "gge", None)
        if not isinstance(extra_data, dict):
            return {}
        return {str(key): str(value)[:500] for key, value in list(extra_data.items())[:20]}
