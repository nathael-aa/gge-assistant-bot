import asyncio
import json
import logging
import os
import traceback
from datetime import UTC, datetime, time
from pathlib import Path

import aiohttp
from discord.ext import commands, tasks

logger = logging.getLogger("GGE_Bot")


class ScanCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_url = "https://api.gge-tracker.com/api/v1"
        data_path = os.getenv("DATA_PATH", "/app/data")
        self.base_output_dir = Path(data_path) / "server_scans"
        self.configuration_path = Path(data_path) / "configs" / "configuration.json"

        self.headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GGE-Assistant/3.0 (Async)",
        }
        self.webhook_url = os.getenv("WEBHOOK_SCAN")

        # Démarrage de la tâche planifiée (ex: tous les jours à 00:30 UTC)
        self.daily_scan.start()

    def cog_unload(self):
        self.daily_scan.cancel()

    async def send_discord_alert(self, title, description, color=16711680):
        """Envoie une notification sur Discord de manière asynchrone"""
        if not self.webhook_url or not self.webhook_url.startswith("http"):
            return

        payload = {
            "embeds": [
                {"title": title, "description": description, "color": color, "timestamp": datetime.utcnow().isoformat()}
            ]
        }
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(self.webhook_url, json=payload)
        except Exception as e:
            logger.error(f"❌ Impossible d'envoyer l'alerte Discord : {e}")

    def get_active_servers(self):
        """Récupère la liste des serveurs ACTIFS depuis le fichier de config unifié"""
        active_servers = set()
        if self.configuration_path.exists():
            try:
                with open(self.configuration_path, encoding="utf-8") as f:
                    config_data = json.load(f)

                servers_info = config_data.get("servers_info", {})
                for srv_name, data in servers_info.items():
                    if isinstance(data, dict) and data.get("enabled") is True:
                        active_servers.add(srv_name.upper())

            except Exception as e:
                logger.error(f"❌ Erreur lecture configuration.json : {e}")

        if not active_servers:
            logger.warning("⚠️ Aucun serveur actif trouvé. Fallback sur E4K_FR1.")
            active_servers.add("E4K_FR1")
        return list(active_servers)

    async def fetch_page(self, session, server, page, max_retries=4):
        """Télécharge UNE page avec gestion des erreurs 429 adaptées au rate limit de l'API"""
        url = f"{self.api_url}/players"
        params = {
            "limit": 100,
            "page": page,
            "banFilter": 0,
            "allianceFilter": -1,
            "protectionFilter": -1,
            "inactiveFilter": 1,
            "kingdomFilter": 999,
            "orderBy": "might_current",
            "orderType": "DESC",
        }
        headers = self.headers.copy()
        headers["gge-server"] = server

        for attempt in range(max_retries):
            try:
                async with session.get(url, headers=headers, params=params, timeout=15) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 429:  # Too Many Requests
                        wait_time = 15 * (attempt + 1)
                        logger.warning(f"⚠️ 429 sur {server} (Page {page}). Purge API de {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"❌ Erreur {response.status} sur {server} (Page {page}).")
                        await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"❌ Exception réseau sur {server} (Page {page}): {e}")
                await asyncio.sleep(2)

        return None

    async def scan_server(self, session, server, index_actuel, total_serveurs):
        """Scanne un serveur complet de manière optimisée et non-bloquante"""
        logger.info(f"🔍 DÉMARRAGE [{index_actuel}/{total_serveurs}] : {server}")
        start_time = asyncio.get_event_loop().time()

        first_page_data = await self.fetch_page(session, server, 1)

        if not first_page_data or not first_page_data.get("players"):
            logger.error(f"❌ Aucun joueur trouvé ou erreur fatale pour {server}.")
            return None

        total_pages = first_page_data.get("pagination", {}).get("total_pages", 1)
        total_items = first_page_data.get("pagination", {}).get("total_items_count", "?")
        logger.info(f"📊 {server} : {total_items} joueurs sur {total_pages} pages.")

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
                    "main_points": p.get("might_current", 0),
                    "structures": [],
                }

        parse_players(first_page_data)

        if total_pages > 1:
            for page in range(2, total_pages + 1):
                res = await self.fetch_page(session, server, page)
                if res:
                    parse_players(res)

                await asyncio.sleep(1.2)

        duration = round(asyncio.get_event_loop().time() - start_time, 2)
        logger.info(
            f"✅ FINI [{index_actuel}/{total_serveurs}] : {server} - {len(all_players)} joueurs récupérés en {duration}s"
        )
        return all_players, duration

    def save_results(self, players_data, duration, serveur):
        """Sauvegarde les résultats en JSON (Fonction bloquante isolée)"""
        today = datetime.now().strftime("%Y-%m-%d")
        daily_dir = self.base_output_dir / serveur / today
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

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        return filepath

    async def scan_specific_server(self, server_name: str):
        """Scanne uniquement un serveur spécifique et sauvegarde les résultats."""
        server_name = server_name.upper()
        logger.info("======================================================")
        logger.info(f"🌍 DÉMARRAGE DU SCAN MANUEL CIBLÉ : {server_name}")
        logger.info("======================================================")

        try:
            async with aiohttp.ClientSession() as session:
                # On triche un peu sur l'index (1/1) pour l'affichage des logs
                result = await self.scan_server(session, server_name, 1, 1)

                if result:
                    players, duration = result
                    await asyncio.to_thread(self.save_results, players, duration, server_name)
                    return True
            return False
        except Exception as e:
            logger.error(f"❌ Erreur lors du scan spécifique de {server_name} : {e}")
            logger.error(traceback.format_exc())
            raise e

    # Lancement tous les jours à 00h30 UTC
    @tasks.loop(time=time(hour=0, minute=30, tzinfo=UTC))
    async def daily_scan(self):
        logger.info("======================================================")
        logger.info("🌍 DÉMARRAGE DE LA ROUTINE MULTI-SERVEURS (ASYNC)")
        logger.info("======================================================")

        try:
            servers_to_scan = self.get_active_servers()
            total_serveurs = len(servers_to_scan)

            async with aiohttp.ClientSession() as session:
                for index_actuel, srv in enumerate(servers_to_scan, start=1):
                    result = await self.scan_server(session, srv, index_actuel, total_serveurs)

                    if result:
                        players, duration = result
                        await asyncio.to_thread(self.save_results, players, duration, srv)

                    await asyncio.sleep(5)

            await self.send_discord_alert(
                "✅ Multi-Scan Terminé",
                f"Tous les serveurs actifs ({total_serveurs}) ont été scannés fluidement !",
                65280,
            )

        except Exception as e:
            logger.error(f"❌ CRASH FATAL DU SCANNER : {e}")
            logger.error(traceback.format_exc())
            await self.send_discord_alert(
                "🚨 CRASH DU SCANNER", f"La boucle asynchrone a planté :\n```py\n{e}\n```", 16711680
            )

    @daily_scan.before_loop
    async def before_daily_scan(self):
        """Attend que le bot soit connecté avant de lancer les crons"""
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(ScanCog(bot))
