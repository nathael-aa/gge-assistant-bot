import asyncio
import hashlib
import hmac
import json
import logging
import os
import socket
import sys
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import aiohttp
import discord
from aiohttp import web
from discord.ext import commands, tasks

import observability as obs
from utils import (
    BOT_VERSION,
    CACHE,
    CONFIG_DIR,
    JOUEURS_DIR,
    MON_ID_DISCORD,
    TOKEN,
    TOPGG_TOKEN,
    charger_langues,
    get_server_config,
    load_blocks_async,
    load_maintenance,
    t,
)

# ========================================
# ⚙️ INITIALISATION DU BOT ET DES LOGS
# ========================================
os.makedirs("/app/logs/general", exist_ok=True)
os.makedirs("/app/data", exist_ok=True)

logger = logging.getLogger("GGE_Bot")
logger.setLevel(logging.INFO)

formatter = logging.Formatter("%(asctime)s | [%(levelname)s] | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

file_handler = TimedRotatingFileHandler(
    "/app/logs/general/discord_bot.log", when="midnight", interval=1, backupCount=31, encoding="utf-8-sig"
)


def custom_log_namer(default_name):
    return default_name.replace(".log.", "_") + ".log"


file_handler.namer = custom_log_namer
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Mirror the log stream above into ClickHouse (stays inert when CLICKHOUSE_HOST is unset)
obs.setup(logger, bot_version=BOT_VERSION)

print(
    "\n" + "█" * 60 + "\n" + "█" + " " * 18 + "NOUVEAU DÉMARRAGE DU BOT" + " " * 16 + "█\n" + "█" * 60 + "\n",
    flush=True,
)
logger.info("🟢 Démarrage du système de logs...")


class WelcomeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def get_welcome_embed(self, lang="fr"):
        title = t(lang, "welcome_title", defaut="<:guides:1533429318947045616> Welcome to GGE Assistant!")
        desc = t(lang, "welcome_desc", defaut="Here is how to get started:")

        embed = discord.Embed(title=title, description=desc, color=0x0B1D51)

        embed.add_field(
            name=t(lang, "welcome_step1_title", defaut="<:one:1533556309838790796> Configuration"),
            value=t(lang, "welcome_step1_desc", defaut="Use </setup:0> to configure your profile."),
            inline=False,
        )
        embed.add_field(
            name=t(lang, "welcome_step2_title", defaut="<:two:1533556308723109999> Tools"),
            value=t(lang, "welcome_step2_desc", defaut="Type </help:0> to see all commands."),
            inline=False,
        )
        embed.add_field(
            name=t(lang, "welcome_step3_title", defaut="<:three:1533556307511087144> Calendar"),
            value=t(lang, "welcome_step3_desc", defaut="Use </calendar setup:0> to get alerts."),
            inline=False,
        )

        embed.set_footer(text=t(lang, "welcome_footer", defaut="Select your language below."))
        return embed

    @discord.ui.button(
        label="Français",
        emoji="<:flagfrench:1535281050249601105>",
        style=discord.ButtonStyle.secondary,
        custom_id="welc_fr",
    )
    async def btn_fr(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.get_welcome_embed("fr"))

    @discord.ui.button(
        label="English",
        emoji="<:flagunitedkingdom:1535281046394769429>",
        style=discord.ButtonStyle.secondary,
        custom_id="welc_en",
    )
    async def btn_en(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.get_welcome_embed("en"))

    @discord.ui.button(
        label="Deutsch",
        emoji="<:flaggermany:1535281037716889750>",
        style=discord.ButtonStyle.secondary,
        custom_id="welc_de",
    )
    async def btn_de(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.get_welcome_embed("de"))


class GGEAssistantBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.maintenance_mode = load_maintenance()

        self.tree.interaction_check = self.global_interaction_check
        self.scan_flag_detected = False

        self.status_index = 0
        self.custom_status = None

        self.tree.on_error = self.on_tree_error

    def export_commands_json(self):
        """Exporte le 'Slash Command Payload' incluant les groupes et sous-commandes."""
        try:
            payload = []

            def process_command(cmd, group_name=""):

                if isinstance(cmd, discord.app_commands.Group):
                    group_data = {
                        "name": f"{group_name}{cmd.name}",
                        "description": cmd.description,
                        "type": 1,
                        "options": [],
                    }

                    for sub_cmd in cmd.commands:
                        sub_data = {
                            "name": sub_cmd.name,
                            "description": sub_cmd.description,
                            "type": 1,
                            "options": self.serialize_options(sub_cmd.options) if hasattr(sub_cmd, "options") else [],
                        }
                        group_data["options"].append(sub_data)

                    payload.append(group_data)
                else:
                    cmd_data = {
                        "name": cmd.name,
                        "description": cmd.description,
                        "type": 1,
                        "options": self.serialize_options(cmd.options) if hasattr(cmd, "options") else [],
                    }
                    payload.append(cmd_data)

            for cmd in self.tree.get_commands():
                process_command(cmd)

            with open("commands.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4, ensure_ascii=False)

            logger.info("📄 [JSON] Commandes et sous-commandes actuelles générées avec succès.")
        except Exception as e:
            logger.error(f"❌ Erreur lors de l'export JSON : {e}")
            obs.record_error(source="background", scope="export_commands", exception=e, cog="core")

    def serialize_options(self, options):
        """Transforme les options en format JSON pur."""
        serialized = []
        for opt in options:
            opt_data = {
                "name": opt.name,
                "description": opt.description,
                "type": opt.type.value,
                "required": opt.required,
            }
            if opt.autocomplete:
                opt_data["autocomplete"] = True
            if hasattr(opt, "choices") and opt.choices:
                opt_data["choices"] = [{"name": c.name, "value": c.value} for c in opt.choices]
            if hasattr(opt, "options") and opt.options:
                opt_data["options"] = self.serialize_options(opt.options)
            serialized.append(opt_data)
        return serialized

    async def setup_hook(self):
        if TOPGG_TOKEN and TOPGG_TOKEN != "FAUX_TOKEN":
            self.topgg_token = TOPGG_TOKEN
            logger.info("🟢 Token Top.gg enregistré (utilisation via aiohttp v1).")
        else:
            self.topgg_token = None
            logger.warning("⚠️ Aucun token Top.gg détecté. Les requêtes de vote seront ignorées.")

        # Must run before self.session exists: aiohttp tracing only covers sessions created afterwards
        await obs.start(self)

        connecteur_ipv4 = aiohttp.TCPConnector(family=socket.AF_INET)
        self.session = aiohttp.ClientSession(connector=connecteur_ipv4)

        logger.info("🔌 Chargement des modules ...")
        extensions = [
            "cogs.admin",
            "cogs.aide",
            "cogs.calendrier",
            "cogs.classement",
            "cogs.config",
            "cogs.events",
            "cogs.forteresses",
            "cogs.gge_hub_community",
            "cogs.profils",
            "cogs.radar",
            "cogs.scan_server",
            "cogs.storms",
            "cogs.target",
        ]
        for ext in extensions:
            try:
                await self.load_extension(ext)
                logger.info(f"✅ Module {ext} chargé.")
            except Exception as e:
                logger.error(f"❌ Erreur {ext} : {e}")

        await self.tree.sync()

        self.export_commands_json()

        if not self.flag_watcher_task.is_running():
            self.flag_watcher_task.start()
            logger.info("🛰️ [Tasks] flag_watcher_task initialisée dans le setup_hook.")

        if not self.status_task.is_running():
            self.status_task.start()
            logger.info("🛰️ [Tasks] status_task initialisée dans le setup_hook.")

        if not self.sync_topgg_votes_task.is_running():
            self.sync_topgg_votes_task.start()
            logger.info("🛰️ [Tasks] sync_topgg_votes_task initialisée dans le setup_hook.")

        if not self.post_server_count_task.is_running():
            self.post_server_count_task.start()
            logger.info("🛰️ [Tasks] post_server_count_task initialisée dans le setup_hook.")

        if not self.update_servers_task.is_running():
            self.update_servers_task.start()
            logger.info("🛰️ [Tasks] update_special_servers_task initialisée dans le setup_hook.")

        self.add_view(WelcomeView())

        self.web_app = web.Application()
        self.web_app.router.add_post("/dblwebhook", self.vote_handler)
        self.web_runner = web.AppRunner(self.web_app)
        await self.web_runner.setup()

        self.web_site = web.TCPSite(self.web_runner, "0.0.0.0", 5011)
        await self.web_site.start()
        logger.info("🌐 [Webhook] Serveur web en écoute sur le port 5011.")

    async def on_ready(self):
        charger_langues()
        logger.info(f"✅ Bot connecté en tant que {self.user} (ID: {self.user.id})")

        try:
            path_fort = JOUEURS_DIR / "forteresses_sessions.json"
            path_users = CONFIG_DIR / "users.json"

            if path_fort.exists():
                with open(path_fort, encoding="utf-8") as f:
                    data_fort = json.load(f)
                    sessions = data_fort.get("sessions", {})

                if sessions:
                    users_data = {}
                    if path_users.exists():
                        try:
                            with open(path_users, encoding="utf-8") as f_u:
                                users_data = json.load(f_u)
                        except Exception:
                            pass

                    logger.info(f"🛑 [Redémarrage] Envoi du préavis de reprise à {len(sessions)} joueurs...")

                    for uid in sessions.keys():
                        try:
                            user = self.get_user(int(uid)) or await self.fetch_user(int(uid))
                            langue = users_data.get(uid, {}).get("langue", "fr")

                            titre = t(langue, "bot_restart_notify_title", defaut="{e_refresh} Redémarrage système")
                            desc = t(
                                langue,
                                "bot_restart_notify_desc",
                                defaut=(
                                    "{e_working} Une mise à jour ou une maintenance du système vient d'avoir lieu.\n\n"
                                    "{e_icon_search} Votre radar de forteresses a repris automatiquement en arrière-plan.\n"
                                    "{e_nocheck} Veuillez noter que les boutons interactifs (*Relancer / Vérifier*) de vos anciennes alertes ne sont désormais plus fonctionnels.\n\n"
                                    "{e_check} *(Retour à la normale effectif)*"
                                ),
                            )

                            embed = discord.Embed(title=titre, description=desc, color=discord.Color.orange())
                            await user.send(embed=embed)
                        except Exception:
                            pass

        except Exception as e:
            logger.error(f"❌ Erreur lors de l'envoi du préavis au démarrage : {e}")

        if self.maintenance_mode:
            statut_maint = t("fr", "bot_activity_maintenance", defaut="🚧 EN MAINTENANCE 🚧")
            activity = discord.Activity(type=discord.ActivityType.watching, name=statut_maint)
            target_status = discord.Status.dnd
        else:
            activity = discord.Activity(type=discord.ActivityType.watching, name="/setup ➔ /help")
            target_status = discord.Status.online

        await self.change_presence(activity=activity, status=target_status)
        logger.info(f"📡 Statut mis à jour : {activity.name} | Pastille : {target_status}")

    async def on_guild_join(self, guild: discord.Guild):
        logger.info(
            f"🎉 [NOUVEAU SERVEUR] Le bot a rejoint '{guild.name}' (ID: {guild.id}) | Membres : {guild.member_count}"
        )

        obs.record_guild_event("guild_join", guild=guild, guild_total=len(self.guilds))
        obs.upsert_guild(guild=guild, is_active=1)

        webhook_servers = os.getenv("WEBHOOK_JOIN")
        if webhook_servers and webhook_servers.startswith("http"):
            proprio = guild.owner.name if guild.owner else "Inconnu"
            payload = {
                "username": "GGE Serveurs 📈",
                "embeds": [
                    {
                        "title": "🎉 Nouveau Serveur Rejoint !",
                        "description": f"**Nom :** `{guild.name}`\n**ID :** `{guild.id}`\n**Membres :** `{guild.member_count}`\n**Propriétaire :** `{proprio}`",
                        "color": 0x2ECC71,
                    }
                ],
            }
            try:
                await self.session.post(webhook_servers, json=payload)
            except:
                pass

        channel_to_send = guild.system_channel

        def can_send_welcome(channel):
            if not channel:
                return False
            perms = channel.permissions_for(guild.me)
            return perms.view_channel and perms.send_messages and perms.embed_links

        if not can_send_welcome(channel_to_send):
            channel_to_send = None
            for channel in guild.text_channels:
                if can_send_welcome(channel):
                    channel_to_send = channel
                    break

        if channel_to_send:
            view = WelcomeView()
            embed_initial = view.get_welcome_embed("en")
            try:
                await channel_to_send.send(embed=embed_initial, view=view)
            except discord.Forbidden:
                logger.warning(f"🔇 Message de bienvenue bloqué par les paramètres du serveur '{guild.name}'.")
            except Exception as e:
                logger.error(f"❌ Erreur inattendue du message de bienvenue sur {guild.name} : {e}")
                self.loop.create_task(
                    self._send_background_error(
                        "🐛 Crash Inattendu (Bienvenue)",
                        f"Crash lors du message de bienvenue sur **{guild.name}**.\nErreur : `{e}`",
                    )
                )

    async def on_guild_remove(self, guild: discord.Guild):
        logger.warning(f"👋 [DÉPART SERVEUR] Le bot a été retiré de '{guild.name}' (ID: {guild.id})")

        obs.record_guild_event("guild_leave", guild=guild, guild_total=len(self.guilds))
        obs.upsert_guild(guild=guild, is_active=0, left_at=obs.ch_datetime())

        webhook_servers = os.getenv("WEBHOOK_LEAVE")
        if webhook_servers and webhook_servers.startswith("http"):
            payload = {
                "username": "GGE Serveurs 📉",
                "embeds": [
                    {
                        "title": "👋 Serveur Quitté",
                        "description": f"**Nom :** `{guild.name}`\n**ID :** `{guild.id}`\n**Membres perdus :** `{guild.member_count}`",
                        "color": 0xE74C3C,
                    }
                ],
            }
            try:
                await self.session.post(webhook_servers, json=payload)
            except:
                pass

    @tasks.loop(seconds=15)
    async def flag_watcher_task(self):
        obs.set_task_name("flag_watcher_task")
        try:
            flag_path = Path("/app/data/scan.flag")

            if flag_path.exists():
                if not self.scan_flag_detected:
                    logger.info(
                        "⚡ [Watcher] Fichier scan.flag détecté. Un scan global du serveur est en cours d'exécution..."
                    )
                    self.scan_flag_detected = True
            else:
                if self.scan_flag_detected:
                    logger.info(
                        "🔄 [Watcher] Fichier scan.flag effacé ! Fin des écritures détectée. Actualisation de la RAM..."
                    )
                    self.scan_flag_detected = False
                    try:
                        for srv in CACHE:
                            CACHE[srv]["last_refresh"] = 0
                        logger.info("✅ [Watcher] Le cache mémoire sera réactualisé à la prochaine commande.")
                    except Exception as e:
                        logger.error(f"❌ [Watcher] Échec : {e}")
        except Exception as e:
            obs.record_error(source="task", scope="flag_watcher_task", exception=e, cog="core")

    @tasks.loop(seconds=20)
    async def status_task(self):
        obs.set_task_name("status_task")
        try:
            if self.maintenance_mode:
                statut_maint = t("fr", "bot_activity_maintenance", defaut="🚧 EN MAINTENANCE 🚧")
                activity = discord.Activity(type=discord.ActivityType.watching, name=statut_maint)
                await self.change_presence(activity=activity, status=discord.Status.dnd)
                return

            nb_serveurs = len(self.guilds)
            nb_membres = sum(guild.member_count for guild in self.guilds if guild.member_count)

            def compter_serveurs_gge():
                dossier_serveurs = Path("/app/data/server_scans")
                if dossier_serveurs.exists():
                    return sum(1 for d in dossier_serveurs.iterdir() if d.is_dir())
                return 0

            nb_gge_serveurs = await asyncio.to_thread(compter_serveurs_gge)

            statuts = [
                discord.Activity(type=discord.ActivityType.listening, name="⚙️ /setup | Start here"),
                discord.Activity(type=discord.ActivityType.watching, name="📖 /help | All commands"),
                discord.Activity(
                    type=discord.ActivityType.watching, name=f"🌍 {nb_serveurs} servers | 👥 {nb_membres} users"
                ),
                discord.Activity(type=discord.ActivityType.competing, name=f"⚔️ {nb_gge_serveurs} GGE servers"),
                discord.Activity(type=discord.ActivityType.playing, name=f"🚀 {BOT_VERSION}"),
            ]

            if self.custom_status:
                statuts.append(discord.Activity(type=discord.ActivityType.playing, name=self.custom_status))

            activity = statuts[self.status_index % len(statuts)]
            self.status_index += 1

            await self.change_presence(activity=activity, status=discord.Status.online)
        except Exception as e:
            obs.record_error(source="task", scope="status_task", exception=e, cog="core")

    @status_task.before_loop
    async def before_status_task(self):
        await self.wait_until_ready()

    async def vote_handler(self, request):
        secret = os.getenv("TOPGG_WEBHOOK_SECRET", "faux_secret")

        signature_header = request.headers.get("x-topgg-signature")
        auth_header = request.headers.get("Authorization")

        try:
            raw_body = await request.text()
        except Exception as e:
            obs.record_error(source="webhook", scope="topgg_body", exception=e, cog="core")
            return web.Response(status=400, text="Unreadable body")

        if signature_header:
            try:
                parts = dict(part.split("=") for part in signature_header.split(","))
                message = f"{parts.get('t')}.{raw_body}".encode()
                expected_sig = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected_sig, parts.get("v1")):
                    logger.warning("❌ [Webhook] Signature v1 invalide.")
                    obs.record_vote(accepted=False, reject_reason="bad_signature_v1", signature_version="v1")
                    return web.Response(status=401, text="Signature invalide")
            except Exception as e:
                obs.record_error(source="webhook", scope="topgg_sig", exception=e, cog="core")
                obs.record_vote(accepted=False, reject_reason="invalid_signature_format", signature_version="v1")
                return web.Response(status=400, text="Erreur de calcul v1")

        elif auth_header:
            if auth_header != secret:
                logger.warning("❌ [Webhook] Mot de passe v0 invalide.")
                obs.record_vote(accepted=False, reject_reason="bad_password_v0", signature_version="v0")
                return web.Response(status=401, text="Mauvais mot de passe v0")

        else:
            logger.warning(f"❌ [Webhook] Requête refusée (ni v0, ni v1). Headers : {request.headers}")
            obs.record_vote(accepted=False, reject_reason="missing_auth", signature_version="none")
            return web.Response(status=401, text="Missing auth")

        try:
            payload = json.loads(raw_body)
        except Exception:
            obs.record_vote(
                accepted=False, reject_reason="invalid_json", signature_version="v1" if signature_header else "v0"
            )
            return web.Response(status=400, text="Invalid JSON")

        if "data" in payload:
            user_id = payload.get("data", {}).get("user", {}).get("platform_id")
            event_type = payload.get("type")
        else:
            user_id = payload.get("user")
            event_type = payload.get("type")

        if not user_id:
            logger.error(f"❌ [Webhook] Impossible de lire l'ID dans le payload : {payload}")
            obs.record_vote(
                accepted=False,
                reject_reason="missing_user_id",
                event_type=str(event_type or ""),
                signature_version="v1" if signature_header else "v0",
            )
            return web.Response(status=400, text="Missing user ID")

        webhook_votes = os.getenv("WEBHOOK_VOTES")

        obs.record_vote(
            user_id=int(user_id) if str(user_id).isdigit() else 0,
            source="webhook",
            event_type=str(event_type or "upvote"),
            accepted=True,
            signature_version="v1" if signature_header else "v0",
            shield_until=datetime.now() + timedelta(days=7),
        )

        if event_type in ["test", "webhook.test"]:
            logger.info(f"✅ [Webhook] TEST RÉUSSI ! La liaison avec Top.gg est parfaite (Test par {user_id}).")
        else:
            logger.info(f"✅ [Webhook] Vrai vote reçu ! Application du bouclier pour {user_id}.")

            if webhook_votes and webhook_votes.startswith("http"):
                payload = {
                    "username": "GGE Top.gg 🏆",
                    "embeds": [
                        {
                            "title": "🌟 Nouveau Vote reçu !",
                            "description": f"Un joueur (ID: `<@{user_id}>`) vient de voter pour le bot sur Top.gg !\nSon bouclier anti-pubs est activé pour 7 jours.",
                            "color": 0xFFD700,
                        }
                    ],
                }
                try:
                    self.loop.create_task(self.session.post(webhook_votes, json=payload))
                except:
                    pass

        VOTES_FILE = JOUEURS_DIR / "votes.json"
        votes_data = {}
        if VOTES_FILE.exists():
            try:
                with open(VOTES_FILE, encoding="utf-8") as f:
                    votes_data = json.load(f)
            except Exception:
                pass

        now = datetime.now()
        votes_data[str(user_id)] = (now + timedelta(days=7)).isoformat()

        try:
            with open(VOTES_FILE, "w", encoding="utf-8") as f:
                json.dump(votes_data, f, indent=4)
        except Exception as e:
            logger.error(f"❌ [Webhook] Erreur lors de la sauvegarde : {e}")
            obs.record_error(source="webhook", scope="topgg_save", exception=e, cog="core")

        try:
            user = self.get_user(int(user_id)) or await self.fetch_user(int(user_id))
            if user:
                from utils import USERS_CONFIG_CACHE

                langue = "en"

                user_data = USERS_CONFIG_CACHE.get(str(user_id))
                if user_data and isinstance(user_data, dict):
                    langue = user_data.get("lang", user_data.get("langue", "en"))

                titre_defaut = "🎉 Thank you for your support!"
                desc_defaut = (
                    "Your vote on Top.gg has been successfully recorded.\n\n"
                    "🛡️ **Your shield is now active!**\n"
                    "You won't see any vote requests on radar commands for the next **7 days**.\n\n"
                    "Happy gaming! ⚔️"
                )

                titre = t(langue, "vote_thanks_title", defaut=titre_defaut)
                description = t(langue, "vote_thanks_desc", defaut=desc_defaut)

                embed = discord.Embed(title=titre, description=description, color=discord.Color.brand_green())
                await user.send(embed=embed)
        except Exception:
            pass

        return web.Response(status=200, text="OK")

    @tasks.loop(hours=12)
    async def sync_topgg_votes_task(self):
        obs.set_task_name("sync_topgg_votes_task")
        try:
            VOTES_FILE = JOUEURS_DIR / "votes.json"

            if not getattr(self, "topgg_token", None):
                return

            headers = {"Authorization": f"Bearer {self.topgg_token}"}
            url = f"https://top.gg/api/bots/{self.user.id}/votes"

            recent_voters_ids = []
            try:
                async with self.session.get(url, headers=headers, timeout=10) as r:
                    if r.status != 200:
                        err_txt = await r.text()
                        logger.error(f"❌ [Top.gg] Erreur API ({r.status}) : {err_txt}")
                        return

                    raw_voters = await r.json()

                    for v in raw_voters:
                        if isinstance(v, dict):
                            recent_voters_ids.append(str(v.get("id", "")))
                        else:
                            recent_voters_ids.append(str(v))

                    recent_voters_ids = [uid for uid in recent_voters_ids if uid]

            except Exception as e:
                logger.error(f"❌ [Top.gg] Erreur de requête aiohttp : {e}")
                obs.record_error(source="task", scope="sync_topgg_fetch", exception=e, cog="core")
                return

            votes_data = {}
            if VOTES_FILE.exists():
                try:
                    with open(VOTES_FILE, encoding="utf-8") as f:
                        votes_data = json.load(f)
                except Exception:
                    pass

            now = datetime.now()
            updated = False

            keys_to_delete = []
            for uid, deadline_iso in votes_data.items():
                if datetime.fromisoformat(deadline_iso) < now:
                    keys_to_delete.append(uid)

            for uid in recent_voters_ids:
                if uid not in votes_data or uid in keys_to_delete:
                    votes_data[uid] = (now + timedelta(days=7)).isoformat()
                    if uid in keys_to_delete:
                        keys_to_delete.remove(uid)
                    updated = True

            for uid in keys_to_delete:
                del votes_data[uid]
                updated = True

            if updated:
                try:
                    with open(VOTES_FILE, "w", encoding="utf-8") as f:
                        json.dump(votes_data, f, indent=4)
                    logger.info("✅ [Top.gg] Base des votes mise à jour via API v1 (Boucliers 7j attribués/nettoyés).")
                except Exception as e:
                    logger.error(f"❌ Impossible de sauvegarder votes.json : {e}")
        except Exception as e:
            obs.record_error(source="task", scope="sync_topgg_votes_task", exception=e, cog="core")

    @sync_topgg_votes_task.before_loop
    async def before_sync_votes(self):
        await self.wait_until_ready()

    @tasks.loop(hours=3)
    async def update_servers_task(self):
        obs.set_task_name("update_servers_task")
        url = "https://ggetracker.github.io/i18n/servers.xml"
        webhook_url = os.getenv("WEBHOOK_SYNC")

        try:
            async with self.session.get(url, timeout=10) as r:
                if r.status == 200:
                    xml_text = await r.text()
                    root = ET.fromstring(xml_text)

                    config_file = CONFIG_DIR / "configuration.json"
                    if not config_file.exists():
                        return logger.error("❌ Fichier de configuration introuvable.")

                    with open(config_file, encoding="utf-8") as f:
                        config_data = json.load(f)

                    anciennes_infos = config_data.get("servers_info", {})
                    vieux_scan_minutes = config_data.get("scan_minutes", {})
                    vieux_noms_api = config_data.get("servers", {})

                    nouveau_servers_info = {}

                    for server in root.findall(".//server"):
                        name_elem = server.find("name")
                        enabled_elem = server.find("enabled")
                        featured_elem = server.find("featured")

                        if name_elem is not None and name_elem.text:
                            name = name_elem.text.strip()

                            is_enabled = (
                                (enabled_elem.text.strip().lower() == "true")
                                if (enabled_elem is not None and enabled_elem.text)
                                else False
                            )
                            is_featured = (
                                (featured_elem.text.strip().lower() == "true")
                                if (featured_elem is not None and featured_elem.text)
                                else False
                            )

                            minutes = anciennes_infos.get(name, {}).get("scan_minutes")
                            if minutes is None:
                                minutes = vieux_scan_minutes.get(name)

                            api_name = anciennes_infos.get(name, {}).get("api_name")
                            if api_name is None:
                                api_name = vieux_noms_api.get(name.lower())

                            nouveau_servers_info[name] = {
                                "enabled": is_enabled,
                                "featured": is_featured,
                                "scan_minutes": minutes,
                                "api_name": api_name,
                            }

                    nouveaux_serveurs = []
                    changements_featured = []

                    for name, new_data in nouveau_servers_info.items():
                        if name not in anciennes_infos:
                            nouveaux_serveurs.append(name)
                        else:
                            old_featured = anciennes_infos[name].get("featured", False)
                            if old_featured != new_data["featured"]:
                                etat = "🌟 Activées (Featured)" if new_data["featured"] else "❌ Désactivées"
                                changements_featured.append(f"• **{name}** ➔ Fonctions avancées : {etat}")

                    vieilles_cles_presentes = any(
                        k in config_data for k in ["active_servers", "special_servers", "scan_minutes", "servers"]
                    )

                    if nouveau_servers_info != anciennes_infos or vieilles_cles_presentes:
                        config_data["servers_info"] = nouveau_servers_info

                        config_data.pop("active_servers", None)
                        config_data.pop("special_servers", None)
                        config_data.pop("scan_minutes", None)
                        config_data.pop("servers", None)

                        if "live_api_commands" not in config_data:
                            config_data["live_api_commands"] = {"groups": ["storm", "fortress"], "specific": []}

                        with open(config_file, "w", encoding="utf-8") as f:
                            json.dump(config_data, f, indent=4, ensure_ascii=False)

                        logger.info(
                            f"🔄 [XML Sync] Base de données unifiée mise à jour avec {len(nouveau_servers_info)} serveurs."
                        )

                        if nouveaux_serveurs or changements_featured:
                            desc_parts = ["Le XML de GGE-Tracker a évolué :"]

                            if nouveaux_serveurs:
                                desc_parts.append(
                                    "\n🌍 **Nouveaux serveurs détectés :**\n"
                                    + "\n".join([f"• `{s}`" for s in nouveaux_serveurs])
                                )

                            if changements_featured:
                                desc_parts.append(
                                    "\n⭐ **Changements de fonctionnalités avancées :**\n"
                                    + "\n".join(changements_featured)
                                )

                            payload = {
                                "embeds": [
                                    {
                                        "title": "🔄 Alerte Synchro GGE-Tracker",
                                        "description": "\n".join(desc_parts),
                                        "color": 3447003,
                                        "timestamp": datetime.now().isoformat(),
                                    }
                                ]
                            }
                            if webhook_url and webhook_url.startswith("http"):
                                try:
                                    async with self.session.post(webhook_url, json=payload) as resp:
                                        pass
                                except Exception as webhook_err:
                                    logger.error(f"❌ Erreur envoi webhook admin synchro : {webhook_err}")
                else:
                    logger.warning(f"⚠️ [XML Sync] Impossible d'accéder au XML (Erreur {r.status})")
        except Exception as e:
            logger.error(f"❌ [XML Sync] Erreur lors de l'unification des serveurs : {e}")
            obs.record_error(source="task", scope="update_servers_task", exception=e, cog="core")

    @tasks.loop(minutes=30)
    async def post_server_count_task(self):
        obs.set_task_name("post_server_count_task")
        try:
            if not getattr(self, "topgg_token", None):
                return

            url = f"https://top.gg/api/bots/{self.user.id}/stats"
            headers = {"Authorization": f"Bearer {self.topgg_token}"}
            payload = {"server_count": len(self.guilds)}

            async with self.session.post(url, headers=headers, json=payload, timeout=10) as r:
                if r.status == 200:
                    logger.info(f"📈 [Top.gg] Compteur de serveurs mis à jour : {len(self.guilds)} serveurs.")
                else:
                    logger.error(f"❌ [Top.gg] Échec de la mise à jour des stats ({r.status}).")
        except Exception as e:
            logger.error(f"❌ [Top.gg] Erreur réseau lors de la mise à jour des stats : {e}")
            obs.record_error(source="task", scope="post_server_count_task", exception=e, cog="core")

    @post_server_count_task.before_loop
    async def before_post_stats(self):
        await self.wait_until_ready()

    async def _send_system_alert(
        self, interaction: discord.Interaction, titre: str, description: str, couleur: int = 0xFF0000
    ):
        """Alerte système attachée à une interaction utilisateur (ex: Spam, Logs de commandes)."""
        webhook_url = os.getenv("WEBHOOK_SYSTEM")
        if not webhook_url or webhook_url.startswith("https://discord.com/api/webhooks/TON/"):
            return

        embed = discord.Embed(title=titre, description=description, color=couleur)
        embed.set_footer(text=f"Utilisateur : {interaction.user.name} ({interaction.user.id})")

        try:
            webhook = discord.Webhook.from_url(webhook_url, session=self.session)
            await webhook.send(embed=embed, username="GGE Système 🚨")
        except Exception as e:
            logger.error(f"❌ Erreur Webhook Système : {e}")
            obs.record_error(source="background", scope="send_system_alert", exception=e, cog="core")

    async def _send_background_error(self, titre: str, description: str):
        """Alerte système indépendante (Crash Global, Erreur de tâche de fond, Permissions)."""
        webhook_url = os.getenv("WEBHOOK_SYSTEM")
        if not webhook_url or webhook_url.startswith("https://discord.com/api/webhooks/TON/"):
            return

        embed = discord.Embed(title=titre, description=description, color=0x8B0000)
        embed.set_footer(text="GGE Assistant - Rapport de Crash Automatique")

        try:
            if not hasattr(self, "session") or self.session.closed:
                async with aiohttp.ClientSession() as session:
                    webhook = discord.Webhook.from_url(webhook_url, session=session)
                    await webhook.send(embed=embed, username="GGE Crash Report 🐛")
            else:
                webhook = discord.Webhook.from_url(webhook_url, session=self.session)
                await webhook.send(embed=embed, username="GGE Crash Report 🐛")
        except Exception as e:
            logger.error(f"❌ Erreur critique du Webhook de Crash Background : {e}")

    async def on_app_command_completion(self, interaction: discord.Interaction, command):
        """Close the command_logs row opened by global_interaction_check"""
        obs.complete_command(interaction, status="ok")

    async def on_error(self, event_method: str, *args, **kwargs):
        """Capture et signale automatiquement toutes les erreurs survenant dans un événement du bot."""
        exc_type, exc_value, exc_traceback = sys.exc_info()
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))

        logger.error(f"❌ Erreur globale dans l'événement '{event_method}':\n{tb_str}")

        obs.record_error(
            source="on_error",
            scope=event_method,
            exception=exc_value,
            traceback_text=tb_str,
            severity="critical",
            notified=True,
        )

        tb_str_short = tb_str[-2000:] if len(tb_str) > 2000 else tb_str

        self.loop.create_task(
            self._send_background_error(
                f"💥 Crash Événement : `{event_method}`",
                f"Une erreur inattendue a fait planter un événement du bot.\n\n**Traceback :**\n```py\n{tb_str_short}\n```",
            )
        )

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Le filet de sécurité #3 : Pour tes commandes d'administration classiques (!commande)."""

        if isinstance(error, commands.CommandNotFound):
            return

        tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        tb_str_short = tb_str[-2000:] if len(tb_str) > 2000 else tb_str

        logger.error(f"❌ Crash sur la commande admin '!{ctx.command}':\n{tb_str}")

        obs.record_error(
            source="on_command_error",
            scope=str(ctx.command),
            exception=error,
            traceback_text=tb_str,
            command=str(ctx.command),
            notified=True,
            user_id=getattr(ctx.author, "id", 0) or 0,
            guild_id=getattr(ctx.guild, "id", 0) or 0 if ctx.guild else 0,
        )

        self.loop.create_task(
            self._send_background_error(
                f"💥 Crash Commande Admin : `!{ctx.command}`",
                f"**Exécuté par :** {ctx.author.mention}\n\n**Traceback :**\n```py\n{tb_str_short}\n```",
            )
        )

    async def on_tree_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        """Capture et signale automatiquement les plantages lors de l'exécution d'une commande Slash."""
        tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        tb_str_short = tb_str[-2000:] if len(tb_str) > 2000 else tb_str

        cmd_name = interaction.command.name if interaction.command else "Inconnue"
        serveur = interaction.guild.name if interaction.guild else "Message Privé"

        logger.error(f"❌ Crash sur la commande '/{cmd_name}':\n{tb_str}")

        obs.record_error(
            source="on_tree_error",
            scope=cmd_name,
            exception=error,
            traceback_text=tb_str,
            command=cmd_name,
            notified=True,
            user_id=getattr(interaction.user, "id", 0) or 0,
            guild_id=getattr(interaction.guild, "id", 0) or 0 if interaction.guild else 0,
        )
        obs.complete_command(interaction, status="error", error=error)

        self.loop.create_task(
            self._send_background_error(
                f"💥 Crash Commande : `/{cmd_name}`",
                f"**Exécuté par :** {interaction.user.mention} (`{interaction.user.id}`)\n"
                f"**Serveur :** {serveur}\n\n"
                f"**Traceback :**\n```py\n{tb_str_short}\n```",
            )
        )

        try:
            msg = "⚠️ Aïe ! Une erreur interne a fait planter cette commande. Mon créateur vient de recevoir le rapport de crash et va corriger ça au plus vite."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

    async def global_interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.type == discord.InteractionType.autocomplete:
            return True

        cmd_name = (
            interaction.command.qualified_name if interaction.command else interaction.data.get("name", "inconnue")
        )

        # The trace_id set here is inherited by the command's API calls and log lines
        is_mine = interaction.user.id == MON_ID_DISCORD
        obs_ctx = obs.start_command(interaction, cmd_name, is_owner=is_mine)

        if is_mine and getattr(self, "bypass_createur", True):
            return True

        langue, serveur = await get_server_config(interaction)
        if obs_ctx is not None:
            obs_ctx.lang, obs_ctx.gge_server = langue, serveur

        import time

        if not hasattr(self, "_spam_cache"):
            self._spam_cache = {}

        if interaction.user.id != MON_ID_DISCORD:
            user_id = interaction.user.id
            now = time.time()

            self._spam_cache[user_id] = [t for t in self._spam_cache.get(user_id, []) if now - t < 10]
            self._spam_cache[user_id].append(now)

            if len(self._spam_cache[user_id]) > 6:
                if len(self._spam_cache[user_id]) == 7:
                    msg = t(langue, "err_spam", defaut="⚠️ Tu vas trop vite ! Patiente quelques secondes.")
                    await interaction.response.send_message(msg, ephemeral=True)

                    self.loop.create_task(
                        self._send_system_alert(
                            interaction,
                            "⚠️ Alerte Spam",
                            f"{interaction.user.mention} a lancé trop de commandes en moins de 10 secondes (`/{cmd_name}`).",
                            0xFFA500,
                        )
                    )
                obs.deny_command(obs_ctx, "spam_ratelimit")
                return False

        try:
            if interaction.type == discord.InteractionType.application_command:
                lieu = interaction.guild.name if interaction.guild else "Message Privé"
                params = []

                def extract_options(opts):
                    for opt in opts:
                        if opt.get("type") in (1, 2):
                            extract_options(opt.get("options", []))
                        elif "value" in opt:
                            params.append(f"{opt.get('name')}: {opt.get('value')}")

                extract_options(interaction.data.get("options", []))
                options_txt = f" | ⚙️ [{', '.join(params)}]" if params else ""
                logger.info(f"📝 [COMMANDE] {interaction.user.name} a lancé `/{cmd_name}` sur [{lieu}]{options_txt}")
        except Exception as e:
            logger.error(f"⚠️ Erreur lors de l'écriture du log : {e}")
            self.loop.create_task(
                self._send_system_alert(
                    interaction,
                    "🐛 Erreur Console (Logs)",
                    f"Impossible d'écrire le log pour `/{cmd_name}`.\n```py\n{e}\n```",
                )
            )

        commandes_libres = ["setup", "help", "contact", "changelog"]

        if cmd_name not in commandes_libres:
            config_ok = False

            from utils import USERS_CONFIG_CACHE

            if USERS_CONFIG_CACHE and str(interaction.user.id) in USERS_CONFIG_CACHE:
                config_ok = True

            if not config_ok:
                if interaction.type == discord.InteractionType.application_command:
                    msg = t(
                        langue,
                        "bot_err_config_dm",
                        defaut="⚠️ **Halte là !**\nTu n'as pas encore configuré ton profil personnel. Utilise la commande </setup:0> pour définir ton serveur et ta langue avant d'utiliser le bot.",
                    )
                    await interaction.response.send_message(msg, ephemeral=True)
                obs.deny_command(obs_ctx, "no_user_config")
                return False

        commandes_privees = [
            "fortress",
            "fortress scan",
            "fortress stop",
            "rival",
            "rival start",
            "rival add",
            "rival list",
            "rival stop",
            "radar",
            "radar add",
            "radar remove",
            "radar list",
            "radar alliance",
            "radar alliance add",
            "radar alliance remove",
        ]

        if interaction.guild and cmd_name in commandes_privees:
            if interaction.type == discord.InteractionType.application_command:
                msg = t(
                    langue,
                    "err_dm_only",
                    defaut="<:error:1512505075220611172> Cette commande est uniquement utilisable en **Messages Privés**.",
                )
                await interaction.response.send_message(msg, ephemeral=True)
            obs.deny_command(obs_ctx, "dm_only")
            return False

        commandes_serveur = [
            "calendar",
            "calendar setup",
            "calendar track",
            "calendar untrack",
            "event_goal_set",
            "event_summary",
            "diplomacy",
            "diplomacy add",
            "diplomacy remove",
            "diplomacy list",
        ]

        if not interaction.guild and cmd_name in commandes_serveur:
            if interaction.type == discord.InteractionType.application_command:
                msg = t(
                    langue,
                    "err_guild_only",
                    defaut="<:error:1512505075220611172> Cette commande est uniquement utilisable sur des **Serveurs Discord**.",
                )
                await interaction.response.send_message(msg, ephemeral=True)
            obs.deny_command(obs_ctx, "guild_only")
            return False

        try:
            import json

            from utils import CONFIG_DIR

            config_file = CONFIG_DIR / "configuration.json"
            if config_file.exists():
                with open(config_file, encoding="utf-8") as f:
                    config_data = json.load(f)

                    servers_info = config_data.get("servers_info", {})
                    live_config = config_data.get("live_api_commands", {})
                    groupes_live = live_config.get("groups", [])
                    commandes_live = live_config.get("specific", [])

                base_cmd = cmd_name.split(" ")[0]

                if base_cmd in groupes_live or cmd_name in groupes_live or cmd_name in commandes_live:
                    is_featured = servers_info.get(serveur, {}).get("featured", False)
                    if obs_ctx is not None:
                        obs_ctx.server_featured = bool(is_featured)

                    if serveur and not is_featured:
                        if interaction.type == discord.InteractionType.application_command:
                            msg = t(
                                langue,
                                "err_unsupported_special",
                                defaut="⚠️ La commande n'est actuellement pas supportée pour ton serveur de jeu GGE. Pour avoir plus d'informations, merci d'utiliser /support.",
                            )
                            await interaction.response.send_message(msg, ephemeral=True)
                        obs.deny_command(obs_ctx, "server_not_featured")
                        return False
        except Exception as e:
            logger.error(f"❌ Erreur lors de la vérification des serveurs spéciaux : {e}")
            self.loop.create_task(
                self._send_system_alert(
                    interaction,
                    "🐛 Erreur Console (Vérification Serveur)",
                    f"Impossible de valider le serveur de l'utilisateur.\n```py\n{e}\n```",
                )
            )

        if self.maintenance_mode:
            if interaction.type == discord.InteractionType.application_command:
                msg = t(
                    langue,
                    "bot_err_maintenance",
                    defaut="🚧 **En cours de maintenance !**\nMes gobelins travaillent pour réactiver l'ensemble des fonctionnalités au plus vite.",
                )
                await interaction.response.send_message(msg, ephemeral=True)
            obs.deny_command(obs_ctx, "maintenance")
            return False

        blocks_data = await load_blocks_async()
        global_cmds = blocks_data.get("global_commands", {})
        blocked_users = blocks_data.get("blocked_users", {})

        if cmd_name in global_cmds:
            if interaction.type == discord.InteractionType.application_command:
                reason = global_cmds[cmd_name]
                msg = t(
                    langue, "bot_err_cmd_blocked", reason=reason, defaut=f"⛔ **Commande désactivée** :\n> {reason}"
                )
                await interaction.response.send_message(msg, ephemeral=True)
            obs.deny_command(obs_ctx, "command_blocked")
            return False

        user_id_str = str(interaction.user.id)
        if user_id_str in blocked_users:
            user_blocks = blocked_users[user_id_str]

            if "ALL" in user_blocks:
                if interaction.type == discord.InteractionType.application_command:
                    reason = user_blocks["ALL"]
                    msg = t(
                        langue, "bot_err_user_blocked_all", reason=reason, defaut=f"🛑 **Accès refusé** :\n> {reason}"
                    )
                    await interaction.response.send_message(msg, ephemeral=True)

                    self.loop.create_task(
                        self._send_system_alert(
                            interaction,
                            "🛑 Intrusion bloquée (Ban ALL)",
                            f"Un utilisateur banni a tenté de forcer le passage sur `/{cmd_name}`.\nRaison du ban : {reason}",
                            0x8B0000,
                        )
                    )
                obs.deny_command(obs_ctx, "user_banned_all")
                return False

            if cmd_name in user_blocks:
                if interaction.type == discord.InteractionType.application_command:
                    reason = user_blocks[cmd_name]
                    msg = t(
                        langue,
                        "bot_err_user_blocked_cmd",
                        reason=reason,
                        defaut=f"🛑 **Accès restreint** :\n> {reason}",
                    )
                    await interaction.response.send_message(msg, ephemeral=True)

                    self.loop.create_task(
                        self._send_system_alert(
                            interaction,
                            "🛑 Intrusion bloquée (Ban Commande)",
                            f"Un utilisateur a tenté d'utiliser sa commande restreinte `/{cmd_name}`.\nRaison du ban : {reason}",
                            0x8B0000,
                        )
                    )
                obs.deny_command(obs_ctx, "user_banned_command")
                return False

        return True

    async def close(self):

        if hasattr(self, "flag_watcher_task"):
            self.flag_watcher_task.cancel()
        if hasattr(self, "status_task"):
            self.status_task.cancel()
        if hasattr(self, "sync_topgg_votes_task"):
            self.sync_topgg_votes_task.cancel()
        if hasattr(self, "post_server_count_task"):
            self.post_server_count_task.cancel()
        if hasattr(self, "update_servers_task"):
            self.update_servers_task.cancel()
        if hasattr(self, "web_site"):
            await self.web_site.stop()
            await self.web_runner.cleanup()

        session = getattr(self, "session", None)
        if session:
            await session.close()

        # Flush the telemetry buffer before shutdown
        await obs.stop()

        await super().close()


bot = GGEAssistantBot()


if __name__ == "__main__":
    bot.run(TOKEN)
