import asyncio
import hashlib
import os
import time
import traceback
from datetime import datetime
from typing import Any

import aiohttp
import discord

from .client import ch_datetime, ch_datetime64
from .context import CommandContext, get_command_context, get_trace_id, new_trace_id, set_command_context

_client = None
_settings = None

# Commands started but not finished yet, keyed by interaction.id
_pending: dict[int, CommandContext] = {}
_PENDING_MAX = 500
_PENDING_TTL_S = 120


def bind(client, settings) -> None:
    global _client, _settings
    _client, _settings = client, settings


def is_active() -> bool:
    return _client is not None and _settings is not None and _settings.enabled


def _base() -> dict[str, Any]:
    return {"bot_version": _settings.bot_version, "instance": _settings.instance}


def _enqueue(table: str, row: dict) -> None:
    if is_active():
        _client.enqueue(table, row)


def _extract_params(interaction) -> dict[str, str]:
    """Flatten the options of an interaction, sub-commands included"""
    parameters: dict[str, str] = {}

    def walk(options):
        for option in options or []:
            if option.get("type") in (1, 2):
                walk(option.get("options", []))
            elif "value" in option:
                parameters[str(option.get("name"))] = str(option.get("value"))[:200]

    try:
        walk((interaction.data or {}).get("options", []))
    except Exception:
        pass
    return parameters


def start_command(interaction, command_name: str, lang: str = "", gge_server: str = "", is_owner: bool = False):
    """Open a command context. Returns the CommandContext, or None when disabled"""
    if not is_active():
        return None
    try:
        context = CommandContext(
            trace_id=new_trace_id(),
            command=command_name or "inconnue",
            command_root=(command_name or "").split(" ")[0],
            cog=getattr(getattr(interaction, "command", None), "module", "") or "",
            interaction_id=int(getattr(interaction, "id", 0) or 0),
            interaction_type=str(getattr(interaction.type, "name", "application_command")),
            user_id=int(getattr(interaction.user, "id", 0) or 0),
            user_name=str(getattr(interaction.user, "name", ""))[:100],
            is_owner=bool(is_owner),
            guild_id=int(getattr(interaction.guild, "id", 0) or 0) if interaction.guild else 0,
            guild_name=str(getattr(interaction.guild, "name", ""))[:200] if interaction.guild else "",
            channel_id=int(getattr(interaction.channel, "id", 0) or 0) if interaction.channel else 0,
            is_dm=interaction.guild is None,
            lang=lang or "",
            discord_locale=str(getattr(interaction, "locale", "") or "")[:16],
            gge_server=gge_server or "",
            params=_extract_params(interaction),
        )
        set_command_context(context)
        if len(_pending) < _PENDING_MAX and context.interaction_id:
            _pending[context.interaction_id] = context
        return context
    except Exception:
        return None


def _write_command(
    context: CommandContext,
    status: str,
    allowed: bool,
    deny_reason: str = "",
    error_type: str = "",
    error_message: str = "",
) -> None:
    if context is None or context.finalized:
        return
    context.finalized = True
    _pending.pop(context.interaction_id, None)
    row = {
        "ts": ch_datetime64(),
        "trace_id": context.trace_id,
        "interaction_id": context.interaction_id,
        "interaction_type": context.interaction_type,
        "command": context.command,
        "command_root": context.command_root,
        "cog": context.cog,
        "user_id": context.user_id,
        "user_name": context.user_name,
        "is_owner": int(context.is_owner),
        "guild_id": context.guild_id,
        "guild_name": context.guild_name,
        "channel_id": context.channel_id,
        "is_dm": int(context.is_dm),
        "lang": context.lang,
        "discord_locale": context.discord_locale,
        "gge_server": context.gge_server,
        "server_featured": int(context.server_featured),
        "params": context.params,
        "allowed": int(allowed),
        "deny_reason": deny_reason,
        "status": status,
        "error_type": error_type[:100],
        "error_message": error_message[:500],
        "duration_ms": context.elapsed_ms(),
        "api_calls": context.api_calls,
        "api_time_ms": context.api_time_ms,
        "cache_hit": int(context.cache_hit),
        "result_rows": context.result_rows,
        "has_vote_shield": int(context.has_vote_shield),
        **_base(),
    }
    _enqueue("command_logs", row)


def deny_command(context, reason: str) -> None:
    """The command was refused by a guard: spam, ban, maintenance"""
    if is_active():
        _write_command(context, status="denied", allowed=False, deny_reason=reason)


def complete_command(interaction, status: str = "ok", error: BaseException | None = None) -> None:
    """The command finished, successfully or with a crash"""
    if not is_active():
        return
    try:
        context = _pending.get(int(getattr(interaction, "id", 0) or 0)) or get_command_context()
        if context is None:
            return
        _write_command(
            context,
            status=status,
            allowed=True,
            error_type=type(error).__name__ if error else "",
            error_message=str(error) if error else "",
        )
    except Exception:
        pass


def sweep_pending() -> None:
    """Close commands that never completed: Discord timeout, killed task"""
    if not is_active():
        return
    try:
        cutoff = time.monotonic() - _PENDING_TTL_S
        for context in [c for c in _pending.values() if c.started_at < cutoff]:
            _write_command(context, status="timeout", allowed=True)
    except Exception:
        pass


def fingerprint_exception(exception: BaseException) -> str:
    """Stable signature of an exception: type plus the last 3 frames"""
    try:
        frames = traceback.extract_tb(exception.__traceback__)[-3:]
        signature = (
            type(exception).__name__
            + "|"
            + "|".join(f"{f.filename.rsplit('/', 1)[-1]}:{f.name}:{f.lineno}" for f in frames)
        )
    except Exception:
        signature = type(exception).__name__
    return hashlib.sha1(signature.encode("utf-8", "replace")).hexdigest()[:16]


async def _send_observability_webhook(titre: str, description: str):
    """Envoie le rapport de crash via Webhook sans dépendre de la session du bot."""
    webhook_url = os.getenv("WEBHOOK_SYSTEM")
    if not webhook_url or webhook_url.startswith("https://discord.com/api/webhooks/TON/"):
        return

    embed = discord.Embed(title=titre, description=description, color=0x8B0000)
    embed.set_footer(text="GGE Assistant - Observability Crash Report")

    try:
        async with aiohttp.ClientSession() as session:
            webhook = discord.Webhook.from_url(webhook_url, session=session)
            await webhook.send(embed=embed, username="GGE Crash Report 🐛")
    except Exception as e:
        import logging

        logger = logging.getLogger("GGE_Bot")
        logger.error(f"❌ [Metrics] Échec de l'envoi du webhook d'observabilité : {e}")


def record_error(
    source: str,
    scope: str = "",
    exception: BaseException | None = None,
    traceback_text: str = "",
    severity: str = "error",
    cog: str = "",
    module: str = "",
    user_id: int = 0,
    guild_id: int = 0,
    command: str = "",
    gge_server: str = "",
    params: dict | None = None,
    notified: bool = False,
) -> None:
    if not is_active():
        return
    try:
        if exception is not None and not traceback_text:
            traceback_text = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
        context = get_command_context()
        _enqueue(
            "errors",
            {
                "ts": ch_datetime64(),
                "trace_id": get_trace_id(),
                "source": source,
                "scope": (scope or "")[:100],
                "cog": cog[:64],
                "module": module[:64],
                "severity": severity,
                "exc_type": type(exception).__name__ if exception else "",
                "exc_message": (str(exception) if exception else "")[:1000],
                "traceback": (traceback_text or "")[:16000],
                "fingerprint": fingerprint_exception(exception) if exception else "",
                "user_id": user_id or (context.user_id if context else 0),
                "guild_id": guild_id or (context.guild_id if context else 0),
                "command": command or (context.command if context else ""),
                "gge_server": gge_server or (context.gge_server if context else ""),
                "params": {str(k): str(v)[:200] for k, v in (params or {}).items()},
                "notified": int(notified),
                **_base(),
            },
        )

        if exception is not None:
            tb_short = traceback_text[-2000:] if len(traceback_text) > 2000 else traceback_text
            titre = f"💥 Crash [{source}] : {scope}"
            description = f"**Erreur :** `{type(exception).__name__}: {exception}`\n```python\n{tb_short}\n```"

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_send_observability_webhook(titre, description))
            except RuntimeError:
                pass

    except Exception:
        pass


def record_bot_status(**gauges: Any) -> None:
    if not is_active():
        return
    try:
        row = {"ts": ch_datetime64(), **_base()}
        row.update({key: value for key, value in gauges.items() if value is not None})
        _enqueue("bot_status", row)
    except Exception:
        pass


def record_scan_run(
    gge_server: str, status: str, started_at: datetime, ended_at: datetime | None = None, **fields: Any
) -> None:
    if not is_active():
        return
    try:
        row = {
            "ts_start": ch_datetime64(started_at),
            "ts_end": ch_datetime64(ended_at or datetime.now()),
            "trace_id": get_trace_id(),
            "gge_server": (gge_server or "").upper(),
            "status": status,
            **_base(),
        }
        row.update({key: value for key, value in fields.items() if value is not None})
        _enqueue("scan_runs", row)
    except Exception:
        pass


def record_alert(
    source: str,
    alert_type: str,
    gge_server: str = "",
    target_type: str = "",
    target_id: str = "",
    target_name: str = "",
    changes: dict | None = None,
    channel: str = "dm",
    guild_id: int = 0,
    recipients: int = 0,
    delivered: int = 0,
    failed: int = 0,
    dm_blocked: int = 0,
    detection_lag_ms: int = 0,
    poll_interval_s: int = 0,
    etag_hit: bool = False,
    error_message: str = "",
) -> None:
    if not is_active():
        return
    try:
        _enqueue(
            "alerts",
            {
                "ts": ch_datetime64(),
                "trace_id": get_trace_id(),
                "source": source,
                "alert_type": alert_type,
                "gge_server": (gge_server or "").upper(),
                "target_type": target_type,
                "target_id": str(target_id)[:100],
                "target_name": str(target_name)[:200],
                "changes": {str(k): str(v)[:200] for k, v in (changes or {}).items()},
                "channel": channel,
                "guild_id": int(guild_id or 0),
                "recipients": int(recipients),
                "delivered": int(delivered),
                "failed": int(failed),
                "dm_blocked": int(dm_blocked),
                "detection_lag_ms": int(detection_lag_ms),
                "poll_interval_s": int(poll_interval_s),
                "etag_hit": int(etag_hit),
                "error_message": error_message[:500],
                **_base(),
            },
        )
    except Exception:
        pass


def record_guild_event(event: str, guild=None, **fields: Any) -> None:
    if not is_active():
        return
    try:
        row = {"ts": ch_datetime64(), "trace_id": get_trace_id(), "event": event, **_base()}
        if guild is not None:
            owner = getattr(guild, "owner", None)
            row.update(
                {
                    "guild_id": int(getattr(guild, "id", 0) or 0),
                    "guild_name": str(getattr(guild, "name", ""))[:200],
                    "owner_id": int(getattr(owner, "id", 0) or 0) if owner else 0,
                    "member_count": int(getattr(guild, "member_count", 0) or 0),
                }
            )
        row.update({key: value for key, value in fields.items() if value is not None})
        _enqueue("guild_events", row)
    except Exception:
        pass


def record_vote(
    user_id: int = 0,
    source: str = "webhook",
    event_type: str = "upvote",
    accepted: bool = True,
    reject_reason: str = "",
    signature_version: str = "",
    shield_until: datetime | None = None,
    dm_sent: bool = False,
    lang: str = "",
    total_active_shields: int = 0,
) -> None:
    if not is_active():
        return
    try:
        row = {
            "ts": ch_datetime64(),
            "trace_id": get_trace_id(),
            "source": source,
            "event_type": event_type,
            "user_id": int(user_id or 0),
            "accepted": int(accepted),
            "reject_reason": reject_reason,
            "signature_version": signature_version,
            "is_weekend": int(datetime.now().weekday() >= 5),
            "dm_sent": int(dm_sent),
            "lang": lang,
            "total_active_shields": int(total_active_shields),
            **_base(),
        }
        if shield_until is not None:
            row["shield_until"] = ch_datetime(shield_until)
        _enqueue("votes", row)
    except Exception:
        pass


def upsert_user(user_id: int, **fields: Any) -> None:
    if not is_active():
        return
    try:
        row = {"user_id": int(user_id), "updated_at": ch_datetime64(), "last_seen": ch_datetime()}
        row.update({key: value for key, value in fields.items() if value is not None})
        _enqueue("dim_users", row)
    except Exception:
        pass


def upsert_guild(guild=None, guild_id: int = 0, **fields: Any) -> None:
    if not is_active():
        return
    try:
        row = {"updated_at": ch_datetime64()}
        if guild is not None:
            owner = getattr(guild, "owner", None)
            row.update(
                {
                    "guild_id": int(getattr(guild, "id", 0) or 0),
                    "guild_name": str(getattr(guild, "name", ""))[:200],
                    "owner_id": int(getattr(owner, "id", 0) or 0) if owner else 0,
                    "member_count": int(getattr(guild, "member_count", 0) or 0),
                }
            )
        else:
            row["guild_id"] = int(guild_id)
        row.update({key: value for key, value in fields.items() if value is not None})
        _enqueue("dim_guilds", row)
    except Exception:
        pass
