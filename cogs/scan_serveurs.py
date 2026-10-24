import asyncio
import json
import logging
import os
import shutil
import traceback
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord.ext import commands, tasks

import observability as obs
from utils import BASE_DATA_PATH, CONFIG_DIR, get_api_headers, load_configuration_async

logger = logging.getLogger("GGE_Bot")

tz_str = os.getenv("TZ", "Europe/Paris")
tz_env = ZoneInfo(tz_str)

heures_de_scan = [
    time(hour=0, minute=0, tzinfo=tz_env),
    time(hour=6, minute=0, tzinfo=tz_env),
    time(hour=12, minute=0, tzinfo=tz_env),
    time(hour=18, minute=0, tzinfo=tz_env),
]


class ScanServeursCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_url = "https://api.gge-tracker.com/api/v1"
        data_path = os.getenv("DATA_PATH")
        self.base_output_dir = Path(data_path) / "server_scans" if data_path else BASE_DATA_PATH / "server_scans"
        self.configuration_path = CONFIG_DIR / "servers_cache.json"

        self.webhook_url = os.getenv("WEBHOOK_SCAN")
        self._scan_kind = "daily"

        self.daily_scan.start()

    def cog_unload(self):
        self.daily_scan.cancel()

    async def send_discord_alert(self, title, description, color=16711680):
        """Envoie une notification sur Discord de manière asynchrone."""
        if not self.webhook_url or not self.webhook_url.startswith("http"):
            return

        payload = {
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "color": color,
                    "timestamp": discord.utils.utcnow().isoformat(),
                }
            ]
        }
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(self.webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10))
        except Exception as e:
            logger.error(f"❌ Impossible d'envoyer l'alerte Discord : {e}")

    async def get_active_servers(self):
        """Récupère la liste des serveurs ACTIFS depuis le fichier de config unifié."""
        active_servers = set()
        try:
            config_data = await load_configuration_async()
            servers_info = config_data.get("servers_info", {})
            for srv_name, data in servers_info.items():
                if isinstance(data, dict) and data.get("enabled") is True:
                    active_servers.add(srv_name.upper())
        except Exception as e:
            logger.error(f"❌ Erreur lecture servers_cache.json : {e}")

        if not active_servers:
            logger.warning("⚠️ Aucun serveur actif trouvé. Fallback sur E4K_FR1.")
            active_servers.add("E4K_FR1")
        return list(active_servers)

    async def fetch_export_page(self, session, server, cursor=None, max_retries=4, obs_stats=None):
        """Télécharge une page de 5000 joueurs via le nouvel endpoint d'export."""
        url = f"{self.api_url}/export/players"

        params = {
            "limit": 5000,
            "fields": "player_id,player_name,alliance_id,alliance_name,level,legendary_level,honor,might_current",
        }

        if cursor:
            params["cursor"] = cursor

        headers = await get_api_headers(custom_server=server)

        for attempt in range(max_retries):
            try:
                async with session.get(url, headers=headers, params=params, timeout=20) as response:
                    if response.status == 200:
                        if obs_stats is not None:
                            obs_stats["pages_ok"] += 1
                        return await response.json()
                    elif response.status == 429:
                        wait_time = 15 * (attempt + 1)
                        if obs_stats is not None:
                            obs_stats["http_429"] += 1
                            obs_stats["backoff_total_s"] += wait_time
                        logger.warning(f"⚠️ 429 sur {server} (Curseur {cursor}). Purge API de {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        if obs_stats is not None:
                            obs_stats["http_errors"] += 1
                        logger.error(f"❌ Erreur {response.status} sur {server} (Curseur {cursor}).")
                        await asyncio.sleep(2)
            except Exception as e:
                if obs_stats is not None:
                    obs_stats["http_errors"] += 1
                logger.error(f"❌ Exception réseau sur {server} (Curseur {cursor}): {e}")
                await asyncio.sleep(2)

        if obs_stats is not None:
            obs_stats["pages_failed"] += 1
        return None

    async def scan_server(self, session, server, index_actuel, total_serveurs):
        """Scanne un serveur complet en utilisant les curseurs (Pagination Keyset)."""
        logger.info(f"🔍 DÉMARRAGE [{index_actuel}/{total_serveurs}] : {server}")
        start_time = asyncio.get_event_loop().time()

        obs_stats = {"pages_ok": 0, "pages_failed": 0, "http_429": 0, "http_errors": 0, "backoff_total_s": 0}
        debut_scan = datetime.now()

        all_players = {}

        def parse_players(data_json):
            for p in data_json.get("players", []):
                name = p.get("player_name")
                if not name:
                    continue
                alliance_raw = p.get("alliance_name") or "Sans alliance"
                if alliance_raw.startswith("[") and alliance_raw.endswith("]"):
                    alliance_raw = alliance_raw.strip("[]")
                all_players[name] = {
                    "player_id": p.get("player_id"),
                    "rank": 0,
                    "score": 0,
                    "category": 1,
                    "alliance": alliance_raw,
                    "alliance_id": p.get("alliance_id"),
                    "level": p.get("level", 0),
                    "legendary_level": p.get("legendary_level", 0),
                    "honor": p.get("honor", 0),
                    "victory_points": 0,
                    "main_points": str(p.get("might_current", 0)),
                    "structures": [],
                }

        cursor = None
        pages_count = 0

        while True:
            res = await self.fetch_export_page(session, server, cursor, obs_stats=obs_stats)

            if not res or not res.get("players"):
                if pages_count == 0:
                    logger.error(f"❌ Aucun joueur trouvé ou erreur fatale pour {server} dès la première page.")
                    obs.record_scan_run(
                        gge_server=server,
                        status="failed",
                        started_at=debut_scan,
                        kind=self._scan_kind,
                        server_index=index_actuel,
                        servers_total=total_serveurs,
                        error_message="Erreur fatale sur la première page",
                        **obs_stats,
                    )
                    return None
                else:
                    logger.warning(f"⚠️ Scan partiel pour {server} (Interrompu à la page {pages_count + 1}).")
                break

            parse_players(res)
            pages_count += 1

            page_info = res.get("page", {})
            has_more = page_info.get("has_more", False)
            cursor = page_info.get("next_cursor")

            if not has_more or not cursor:
                break

            await asyncio.sleep(0.5)

        duration = round(asyncio.get_event_loop().time() - start_time, 2)
        logger.info(
            f"✅ FINI [{index_actuel}/{total_serveurs}] : {server} | "
            f"{len(all_players)} joueurs récupérés en {duration}s ({pages_count} reqs)"
        )

        alliances_uniques = {p["alliance"] for p in all_players.values() if p.get("alliance") != "Sans alliance"}
        obs.record_scan_run(
            gge_server=server,
            status="success" if not obs_stats["pages_failed"] else "partial",
            started_at=debut_scan,
            kind=self._scan_kind,
            server_index=index_actuel,
            servers_total=total_serveurs,
            duration_s=duration,
            pages_total=pages_count,
            players_total=len(all_players),
            alliances_total=len(alliances_uniques),
            **obs_stats,
        )

        all_players_sorted = dict(sorted(all_players.items(), key=lambda item: item[1]["main_points"], reverse=True))

        return all_players_sorted, duration

    def save_results(self, players_data, duration, serveur):
        """Sauvegarde les résultats en JSON (Fonction bloquante isolée) et nettoie les anciens."""
        today = datetime.now().strftime("%Y-%m-%d")
        server_dir = self.base_output_dir / serveur
        daily_dir = server_dir / today
        daily_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%H%M%S")
        filepath = daily_dir / f"server_{timestamp}.json"

        alliances = set(p["alliance"] for p in players_data.values() if p["alliance"] != "Sans alliance")

        output_data = {
            "scan_date": datetime.now().isoformat(),
            "scan_duration": duration,
            "server": serveur,
            "total_players": len(players_data),
            "stats": {"total_alliances": len(alliances), "total_capitals": 0, "total_outposts": 0, "total_castles": 0},
            "players": players_data,
        }

        # Sauvegarde du scan complet lourd
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        # ==========================================
        # CRÉATION DES INDEX LÉGERS (AUTOCOMPLÉTION)
        # ==========================================
        try:
            noms_joueurs = list(players_data.keys())
            noms_alliances = list(alliances)

            # 1. Index des Joueurs
            players_file = daily_dir / "players_autocomplete.json"
            with open(players_file, "w", encoding="utf-8") as f:
                json.dump(noms_joueurs, f, ensure_ascii=False)

            # 2. Index des Alliances
            alliances_file = daily_dir / "alliances_autocomplete.json"
            with open(alliances_file, "w", encoding="utf-8") as f:
                json.dump(noms_alliances, f, ensure_ascii=False)

        except Exception as e:
            logger.error(f"❌ Erreur lors de la création des index d'autocomplétion pour {serveur} : {e}")

        # ==========================================
        # SYSTÈME DE NETTOYAGE DES ANCIENS FICHIERS
        # ==========================================
        try:
            date_dirs = sorted([d for d in server_dir.iterdir() if d.is_dir()])

            if len(date_dirs) > 3:
                dirs_to_remove = date_dirs[:-3]
                for d in dirs_to_remove:
                    shutil.rmtree(d)
                    logger.info(f"🧹 Nettoyage : dossier obsolète {d.name} supprimé pour {serveur}.")
        except Exception as e:
            logger.error(f"❌ Erreur lors du nettoyage des vieux scans pour {serveur} : {e}")

        return filepath

    async def scan_specific_server(self, server_name: str):
        """Scanne uniquement un serveur spécifique et sauvegarde les résultats."""
        server_name = server_name.upper()
        self._scan_kind = "targeted"
        obs.set_task_name("scan_specific_server")
        logger.info("======================================================")
        logger.info(f"🌍 DÉMARRAGE DU SCAN MANUEL CIBLÉ : {server_name}")
        logger.info("======================================================")

        try:
            result = await self.scan_server(self.bot.session, server_name, 1, 1)

            if result:
                players, duration = result
                await asyncio.to_thread(self.save_results, players, duration, server_name)
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Erreur lors du scan spécifique de {server_name} : {e}")
            logger.error(traceback.format_exc())
            obs.record_error(
                source="task", scope="scan_specific_server", exception=e, cog="scan_server", gge_server=server_name
            )
            raise e
        finally:
            self._scan_kind = "daily"

    @tasks.loop(time=heures_de_scan)
    async def daily_scan(self):
        self._scan_kind = "daily"
        obs.set_task_name("daily_scan")
        logger.info("======================================================")
        logger.info("🌍 DÉMARRAGE DE LA ROUTINE MULTI-SERVEURS (ASYNC)")
        logger.info("======================================================")
        try:
            servers_to_scan = await self.get_active_servers()
            total_serveurs = len(servers_to_scan)

            for index_actuel, srv in enumerate(servers_to_scan, start=1):
                result = await self.scan_server(self.bot.session, srv, index_actuel, total_serveurs)

                if result:
                    players, duration = result
                    await asyncio.to_thread(self.save_results, players, duration, srv)

                await asyncio.sleep(1)

            await self.send_discord_alert(
                "✅ Multi-Scan Terminé",
                f"Tous les serveurs actifs ({total_serveurs}) ont été scannés fluidement !",
                65280,
            )

        except Exception as e:
            logger.error(f"❌ CRASH FATAL DU SCANNER : {e}")
            logger.error(traceback.format_exc())
            obs.record_error(
                source="task", scope="daily_scan", exception=e, cog="scan_server", severity="critical", notified=True
            )
            await self.send_discord_alert(
                "🚨 CRASH DU SCANNER", f"La boucle asynchrone a planté :\n```py\n{e}\n```", 16711680
            )

    @daily_scan.before_loop
    async def before_daily_scan(self):
        """Attend que le bot soit connecté avant de lancer les crons."""
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(ScanServeursCog(bot))
