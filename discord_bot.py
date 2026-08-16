import asyncio
import hashlib
import hmac
import json
import logging
import os
import socket
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import aiohttp
import discord
from aiohttp import web
from discord.ext import commands, tasks

from utils import (
    BOT_VERSION,
    CACHE,
    MON_ID_DISCORD,
    TOKEN,
    TOPGG_TOKEN,
    charger_langues,
    get_server_config,
    load_blocks_async,
    load_maintenance,
    t,
)

# ==========================================
# ⚙️ INITIALISATION DU BOT ET DES LOGS
# ==========================================
os.makedirs('/app/logs/general', exist_ok=True)
os.makedirs('/app/data', exist_ok=True)

logger = logging.getLogger("GGE_Bot")
logger.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s | [%(levelname)s] | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

file_handler = TimedRotatingFileHandler('/app/logs/general/discord_bot.log', when="midnight", interval=1, backupCount=31, encoding='utf-8-sig')
def custom_log_namer(default_name): return default_name.replace(".log.", "_") + ".log"
file_handler.namer = custom_log_namer
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

print("\n" + "█"*60 + "\n" + "█" + " "*18 + "NOUVEAU DÉMARRAGE DU BOT" + " "*16 + "█\n" + "█"*60 + "\n", flush=True)
logger.info("🟢 Démarrage du système de logs...")

# ==========================================
# 💌 MESSAGE DE BIENVENUE INTERACTIF
# ==========================================
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
            inline=False
        )
        embed.add_field(
            name=t(lang, "welcome_step2_title", defaut="<:two:1533556308723109999> Tools"), 
            value=t(lang, "welcome_step2_desc", defaut="Type </help:0> to see all commands."), 
            inline=False
        )
        embed.add_field(
            name=t(lang, "welcome_step3_title", defaut="<:three:1533556307511087144> Calendar"), 
            value=t(lang, "welcome_step3_desc", defaut="Use </calendar setup:0> to get alerts."), 
            inline=False
        )
        
        embed.set_footer(text=t(lang, "welcome_footer", defaut="Select your language below."))
        return embed

    @discord.ui.button(label="Français", emoji="<:flagfrench:1535281050249601105>", style=discord.ButtonStyle.secondary, custom_id="welc_fr")
    async def btn_fr(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.get_welcome_embed("fr"))

    @discord.ui.button(label="English", emoji="<:flagunitedkingdom:1535281046394769429>", style=discord.ButtonStyle.secondary, custom_id="welc_en")
    async def btn_en(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.get_welcome_embed("en"))

    @discord.ui.button(label="Deutsch", emoji="<:flaggermany:1535281037716889750>", style=discord.ButtonStyle.secondary, custom_id="welc_de")
    async def btn_de(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.get_welcome_embed("de"))

# ==========================================
# 🤖 CLASSE PRINCIPALE DU BOT
# ==========================================
class GGEAssistantBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix='!', intents=intents, help_command=None)
        self.maintenance_mode = load_maintenance()

        self.tree.interaction_check = self.global_interaction_check
        self.scan_flag_detected = False

        self.status_index = 0
        self.custom_status = None

    def export_commands_json(self):
        """Exporte uniquement le 'Slash Command Payload' minimal requis."""
        try:
            payload = []
            for cmd in self.tree.get_commands():
                cmd_data = {
                    "name": cmd.name,
                    "description": cmd.description,
                    "type": 1,
                    "options": self.serialize_options(cmd.options) if hasattr(cmd, 'options') else []
                }
                payload.append(cmd_data)
            
            with open("commands.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4, ensure_ascii=False)
            
            logger.info("📄 [JSON] Commandes actuelles générées avec succès.")
        except Exception as e:
            logger.error(f"❌ Erreur lors de l'export JSON : {e}")

    def serialize_options(self, options):
        """Transforme les options en format JSON pur."""
        serialized = []
        for opt in options:
            opt_data = {
                "name": opt.name,
                "description": opt.description,
                "type": opt.type.value,
                "required": opt.required
            }
            if opt.autocomplete: opt_data["autocomplete"] = True
            if hasattr(opt, 'choices') and opt.choices:
                opt_data["choices"] = [{"name": c.name, "value": c.value} for c in opt.choices]
            if hasattr(opt, 'options') and opt.options:
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
            "cogs.pvp", 
            "cogs.profils", 
            "cogs.radar",
            "cogs.scan_server",
            "cogs.storms",
        ]
        for ext in extensions:
            try:
                await self.load_extension(ext)
                logger.info(f"✅ Module {ext} chargé.")
            except Exception as e:
                logger.error(f"❌ Erreur {ext} : {e}")

        # 1. Synchronisation globale (pour le reste du monde)
        await self.tree.sync()
        
        # 2. Synchronisation instantanée pour tes serveurs de test
        SERVEURS_DE_TEST = [1342424613660921908, 1512165717380825310,1537532071898128566] 
        
        for guild_id in SERVEURS_DE_TEST:
            try:
                serveur = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=serveur)
                await self.tree.sync(guild=serveur)
                
                logger.info(f"⚡ [SYNC] Commandes forcées sur le serveur test ({guild_id})")
            except Exception as e:
                logger.error(f"❌ [SYNC] Échec sur le serveur {guild_id} : {e}")

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

        self.add_view(WelcomeView())

        # 🟢 DÉMARRAGE DU SERVEUR WEBHOOK (Port 5011)
        self.web_app = web.Application()
        self.web_app.router.add_post('/dblwebhook', self.vote_handler)
        self.web_runner = web.AppRunner(self.web_app)
        await self.web_runner.setup()
        
        # 0.0.0.0 veut dire qu'on accepte les connexions de l'extérieur du conteneur
        self.web_site = web.TCPSite(self.web_runner, '0.0.0.0', 5011)
        await self.web_site.start()
        logger.info("🌐 [Webhook] Serveur web en écoute sur le port 5011.")

    async def on_ready(self):
        charger_langues()
        logger.info(f"✅ Bot connecté en tant que {self.user} (ID: {self.user.id})")
        
        if self.maintenance_mode:
            statut_maint = t("fr", "bot_activity_maintenance", defaut="🚧 EN MAINTENANCE 🚧")
            activity = discord.Activity(type=discord.ActivityType.watching, name=statut_maint)
            target_status = discord.Status.dnd 
        else:
            activity = discord.Activity(type=discord.ActivityType.watching, name="/setup ➔ /help")
            target_status = discord.Status.online 

        await self.change_presence(activity=activity, status=target_status)
        logger.info(f"📡 Statut mis à jour : {activity.name} | Pastille : {target_status}")

    # ==========================================
    # 📥 SUIVI DES AJOUTS / RETRAITS DU BOT
    # ==========================================
    async def on_guild_join(self, guild: discord.Guild):
        logger.info(f"🎉 [NOUVEAU SERVEUR] Le bot a rejoint '{guild.name}' (ID: {guild.id}) | Membres : {guild.member_count}")
        
        channel_to_send = guild.system_channel
        if not channel_to_send or not channel_to_send.permissions_for(guild.me).send_messages:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    channel_to_send = channel
                    break
                    
        if channel_to_send:
            view = WelcomeView()
            embed_initial = view.get_welcome_embed("en")
            try:
                await channel_to_send.send(embed=embed_initial, view=view)
            except Exception as e:
                logger.error(f"❌ Impossible d'envoyer le message de bienvenue sur {guild.name} : {e}")

    async def on_guild_remove(self, guild: discord.Guild):
        logger.warning(f"👋 [DÉPART SERVEUR] Le bot a été retiré de '{guild.name}' (ID: {guild.id})")

    # ==========================================
    # 🛰️ BOUCLE DE VEILLE DE FIN DE SCAN (scan.flag)
    # ==========================================
    @tasks.loop(seconds=15)
    async def flag_watcher_task(self):
        flag_path = Path('/app/data/scan.flag')
        
        if flag_path.exists():
            if not self.scan_flag_detected:
                logger.info("⚡ [Watcher] Fichier scan.flag détecté. Un scan global du serveur est en cours d'exécution...")
                self.scan_flag_detected = True
        else:
            if self.scan_flag_detected:
                logger.info("🔄 [Watcher] Fichier scan.flag effacé ! Fin des écritures détectée. Actualisation de la RAM...")
                self.scan_flag_detected = False
                try:
                    for srv in CACHE:
                        CACHE[srv]['last_refresh'] = 0 
                    logger.info("✅ [Watcher] Le cache mémoire sera réactualisé à la prochaine commande.")
                except Exception as e:
                    logger.error(f"❌ [Watcher] Échec : {e}")

    # ==========================================
    # 🔄 BOUCLE DE ROTATION DES STATUTS
    # ==========================================
    @tasks.loop(seconds=30)
    async def status_task(self):
        if self.maintenance_mode:
            statut_maint = t("fr", "bot_activity_maintenance", defaut="🚧 EN MAINTENANCE 🚧")
            activity = discord.Activity(type=discord.ActivityType.watching, name=statut_maint)
            await self.change_presence(activity=activity, status=discord.Status.dnd)
            return

        nb_serveurs = len(self.guilds)
        nb_membres = sum(guild.member_count for guild in self.guilds if guild.member_count)
        
        def compter_serveurs_gge():
            dossier_serveurs = Path('/app/data/server_scans')
            if dossier_serveurs.exists():
                return sum(1 for d in dossier_serveurs.iterdir() if d.is_dir())
            return 0
            
        nb_gge_serveurs = await asyncio.to_thread(compter_serveurs_gge)

        statuts = [
            discord.Activity(type=discord.ActivityType.listening, name="⚙️ /setup | Start here"),
            discord.Activity(type=discord.ActivityType.watching, name="📖 /help | All commands"),
            discord.Activity(type=discord.ActivityType.watching, name=f"🌍 {nb_serveurs} servers | 👥 {nb_membres} users"),
            discord.Activity(type=discord.ActivityType.competing, name=f"⚔️ {nb_gge_serveurs} GGE servers"),
            discord.Activity(type=discord.ActivityType.playing, name=f"🚀 {BOT_VERSION}")
        ]

        if self.custom_status:
            statuts.append(discord.Activity(type=discord.ActivityType.playing, name=self.custom_status))

        activity = statuts[self.status_index % len(statuts)]
        self.status_index += 1

        await self.change_presence(activity=activity, status=discord.Status.online)

    @status_task.before_loop
    async def before_status_task(self):
        await self.wait_until_ready()

    # ==========================================
    # 🌐 WEBHOOK TOP.GG (HYBRIDE v0 & v1 SÉCURISÉ)
    # ==========================================
    async def vote_handler(self, request):
        secret = "whs_c7babaae2778a44fca787c43be391732e3924c5790907857e61b15b397e3fc32"
        
        signature_header = request.headers.get("x-topgg-signature")
        auth_header = request.headers.get("Authorization")
        
        raw_body = await request.text()
        
        # --- 1. VÉRIFICATION DE SÉCURITÉ ---
        if signature_header:
            # Méthode v1 (Nouvelle avec HMAC)
            try:
                parts = dict(part.split('=') for part in signature_header.split(','))
                message = f"{parts.get('t')}.{raw_body}".encode()
                expected_sig = hmac.new(secret.encode('utf-8'), message, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected_sig, parts.get('v1')):
                    logger.warning("❌ [Webhook] Signature v1 invalide.")
                    return web.Response(status=401, text="Signature invalide")
            except Exception:
                return web.Response(status=400, text="Erreur de calcul v1")
                
        elif auth_header:
            # Méthode v0 (Ancienne avec Header)
            if auth_header != secret:
                logger.warning("❌ [Webhook] Mot de passe v0 invalide.")
                return web.Response(status=401, text="Mauvais mot de passe v0")
                
        else:
            logger.warning(f"❌ [Webhook] Requête refusée (ni v0, ni v1). Headers : {request.headers}")
            return web.Response(status=401, text="Missing auth")

        # --- 2. LECTURE INTELLIGENTE DU JSON (v0 & v1) ---
        try:
            payload = json.loads(raw_body)
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        # Détection du format
        if "data" in payload:
            # C'est un format v1
            user_id = payload.get("data", {}).get("user", {}).get("platform_id")
            event_type = payload.get("type")
        else:
            # C'est un format v0
            user_id = payload.get("user")
            event_type = payload.get("type")

        if not user_id:
            logger.error(f"❌ [Webhook] Impossible de lire l'ID dans le payload : {payload}")
            return web.Response(status=400, text="Missing user ID")

        # --- 3. GESTION DU TYPE (TEST ou VOTE) ---
        if event_type in ["test", "webhook.test"]:
            logger.info(f"✅ [Webhook] TEST RÉUSSI ! La liaison avec Top.gg est parfaite (Test par {user_id}).")
        else:
            logger.info(f"✅ [Webhook] Vrai vote reçu ! Application du bouclier pour {user_id}.")

        # --- 4. SAUVEGARDE (Bouclier 7j) ---
        VOTES_FILE = Path('/app/data/configs/votes.json')
        votes_data = {}
        if VOTES_FILE.exists():
            try:
                with open(VOTES_FILE, encoding='utf-8') as f:
                    votes_data = json.load(f)
            except Exception:
                pass

        now = datetime.now()
        votes_data[str(user_id)] = (now + timedelta(days=7)).isoformat()

        try:
            with open(VOTES_FILE, 'w', encoding='utf-8') as f:
                json.dump(votes_data, f, indent=4)
        except Exception as e:
            logger.error(f"❌ [Webhook] Erreur lors de la sauvegarde : {e}")

        # --- 5. MESSAGE PRIVÉ (TRADUIT) ---
        try:
            user = self.get_user(int(user_id)) or await self.fetch_user(int(user_id))
            if user:
                # 1. On cherche la langue du joueur dans le cache RAM
                from utils import USERS_CONFIG_CACHE
                langue = "en" # Anglais par défaut
                
                user_data = USERS_CONFIG_CACHE.get(str(user_id))
                if user_data and isinstance(user_data, dict):
                    langue = user_data.get("lang", user_data.get("langue", "en"))

                # 2. Textes par défaut (Anglais)
                titre_defaut = "🎉 Thank you for your support!"
                desc_defaut = (
                    "Your vote on Top.gg has been successfully recorded.\n\n"
                    "🛡️ **Your shield is now active!**\n"
                    "You won't see any vote requests on radar commands for the next **7 days**.\n\n"
                    "Happy gaming! ⚔️"
                )

                # 3. Traduction via ta fonction t()
                titre = t(langue, "vote_thanks_title", defaut=titre_defaut)
                description = t(langue, "vote_thanks_desc", defaut=desc_defaut)

                # 4. Envoi de l'embed
                embed = discord.Embed(
                    title=titre,
                    description=description,
                    color=discord.Color.brand_green()
                )
                await user.send(embed=embed)
        except Exception:
            pass

        return web.Response(status=200, text="OK")

    # ==========================================
    # 🔄 TÂCHE : SYNCHRONISATION DES VOTES (12H)
    # ==========================================
    @tasks.loop(hours=12)
    async def sync_topgg_votes_task(self):
        VOTES_FILE = Path('/app/data/configs/votes.json')
        
        # Si pas de token, on annule
        if not getattr(self, 'topgg_token', None):
            return

        # 🟢 1. REQUÊTE DIRECTE À L'API (Format v1)
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
                
                # Extraction ultra-robuste des IDs
                for v in raw_voters:
                    if isinstance(v, dict):
                        recent_voters_ids.append(str(v.get("id", "")))
                    else:
                        recent_voters_ids.append(str(v))
                        
                recent_voters_ids = [uid for uid in recent_voters_ids if uid]
                
        except Exception as e:
            logger.error(f"❌ [Top.gg] Erreur de requête aiohttp : {e}")
            return

        # 🟢 2. GESTION DU FICHIER JSON (Ton code intact)
        votes_data = {}
        if VOTES_FILE.exists():
            try:
                with open(VOTES_FILE, encoding='utf-8') as f:
                    votes_data = json.load(f)
            except Exception:
                pass

        now = datetime.now()
        updated = False

        keys_to_delete = []
        for uid, deadline_iso in votes_data.items():
            if datetime.fromisoformat(deadline_iso) < now:
                keys_to_delete.append(uid)
        
        # On utilise notre nouvelle liste d'IDs (recent_voters_ids)
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
                with open(VOTES_FILE, 'w', encoding='utf-8') as f:
                    json.dump(votes_data, f, indent=4)
                logger.info("✅ [Top.gg] Base des votes mise à jour via API v1 (Boucliers 7j attribués/nettoyés).")
            except Exception as e:
                logger.error(f"❌ Impossible de sauvegarder votes.json : {e}")

    @sync_topgg_votes_task.before_loop
    async def before_sync_votes(self):
        await self.wait_until_ready()

    # ==========================================
    # 📈 TÂCHE : MISE À JOUR DU NOMBRE DE SERVEURS TOP.GG
    # ==========================================
    @tasks.loop(minutes=30)
    async def post_server_count_task(self):
        if not getattr(self, 'topgg_token', None):
            return

        url = f"https://top.gg/api/bots/{self.user.id}/stats"
        headers = {"Authorization": f"Bearer {self.topgg_token}"}
        payload = {"server_count": len(self.guilds)}

        try:
            async with self.session.post(url, headers=headers, json=payload, timeout=10) as r:
                if r.status == 200:
                    logger.info(f"📈 [Top.gg] Compteur de serveurs mis à jour : {len(self.guilds)} serveurs.")
                else:
                    logger.error(f"❌ [Top.gg] Échec de la mise à jour des stats ({r.status}).")
        except Exception as e:
            logger.error(f"❌ [Top.gg] Erreur réseau lors de la mise à jour des stats : {e}")

    @post_server_count_task.before_loop
    async def before_post_stats(self):
        await self.wait_until_ready()

    # ==========================================
    # 🛑 LE VIDEUR UNIQUE ET UNIVERSEL
    # ==========================================
    async def global_interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.type == discord.InteractionType.autocomplete:
            return True

        cmd_name = interaction.command.qualified_name if interaction.command else interaction.data.get("name", "inconnue")
        
        langue, _ = await get_server_config(interaction)
        
        # --- 1. SYSTÈME DE LOGS ---
        try:
            if interaction.type == discord.InteractionType.application_command:
                lieu = interaction.guild.name if interaction.guild else "Message Privé"
                params = []
                def extract_options(opts):
                    for opt in opts:
                        if opt.get("type") in (1, 2): extract_options(opt.get("options", []))
                        elif "value" in opt: params.append(f"{opt.get('name')}: {opt.get('value')}")
                extract_options(interaction.data.get("options", []))
                options_txt = f" | ⚙️ [{', '.join(params)}]" if params else ""
                logger.info(f"📝 [COMMANDE] {interaction.user.name} a lancé `/{cmd_name}` sur [{lieu}]{options_txt}")
        except Exception as e:
            logger.error(f"⚠️ Erreur lors de l'écriture du log : {e}")

        # --- 2. VÉRIFICATION DE LA CONFIGURATION (AVEC CACHE RAM) ---
        commandes_libres = ["setup", "help", "contact", "changelog"]
        
        if cmd_name not in commandes_libres:
            config_ok = False
            
            # On l'importe ICI, à la volée, pour avoir la valeur la plus récente !
            from utils import USERS_CONFIG_CACHE
            
            # Vérification ultra-rapide en RAM
            if USERS_CONFIG_CACHE and str(interaction.user.id) in USERS_CONFIG_CACHE:
                config_ok = True
            
            if not config_ok:
                if interaction.type == discord.InteractionType.application_command:
                    msg = t(langue, "bot_err_config_dm", defaut="⚠️ **Halte là !**\nTu n'as pas encore configuré ton profil personnel. Utilise la commande </setup:0> pour définir ton serveur et ta langue avant d'utiliser le bot.")
                    await interaction.response.send_message(msg, ephemeral=True)
                return False

        # --- 2.5. VÉRIFICATION DES COMMANDES STRICTEMENT PRIVÉES (DM ONLY) ---
        commandes_privees = [
            "fortress", "fortress scan", "fortress stop",
            "rival", "rival start", "rival add", "rival list", "rival stop",
            "radar", "radar add", "radar remove", "radar list",
            "radar alliance", "radar alliance add", "radar alliance remove"
        ]

        if interaction.guild and cmd_name in commandes_privees:
            if interaction.type == discord.InteractionType.application_command:
                msg = t(langue, "err_dm_only", defaut="<:error:1512505075220611172> Cette commande est uniquement utilisable en **Messages Privés**.")
                await interaction.response.send_message(msg, ephemeral=True)
            return False

        # --- 2.6. VÉRIFICATION DES COMMANDES STRICTEMENT SERVEUR (GUILD ONLY) ---
        commandes_serveur = [
            "calendar", "calendar setup", "calendar track", "calendar untrack",
            "event_goal_set", "event_summary",
            "diplomacy", "diplomacy add", "diplomacy remove", "diplomacy list"
        ]

        if not interaction.guild and cmd_name in commandes_serveur:
            if interaction.type == discord.InteractionType.application_command:
                msg = t(langue, "err_guild_only", defaut="<:error:1512505075220611172> Cette commande est uniquement utilisable sur des **Serveurs Discord**.")
                await interaction.response.send_message(msg, ephemeral=True)
            return False

        # --- 3. BYPASS : Le créateur a toujours accès au reste ---
        if interaction.user.id == MON_ID_DISCORD:
            return True

        # --- 4. MAINTENANCE GLOBALE ---
        if self.maintenance_mode:
            if interaction.type == discord.InteractionType.application_command:
                msg = t(langue, "bot_err_maintenance", defaut="🚧 **En cours de maintenance !**\nMes gobelins travaillent pour réactiver l'ensemble des fonctionnalités au plus vite.")
                await interaction.response.send_message(msg, ephemeral=True)
            return False 

        # --- 5. CHARGEMENT DES RÈGLES DE BANS ---
        blocks_data = await load_blocks_async()
        global_cmds = blocks_data.get("global_commands", {})
        blocked_users = blocks_data.get("blocked_users", {})

        # --- 6. BLOCAGE COMMANDE GLOBALE ---
        if cmd_name in global_cmds:
            if interaction.type == discord.InteractionType.application_command:
                reason = global_cmds[cmd_name]
                msg = t(langue, "bot_err_cmd_blocked", reason=reason, defaut=f"⛔ **Commande désactivée** :\n> {reason}")
                await interaction.response.send_message(msg, ephemeral=True)
            return False

        # --- 7. BLOCAGE UTILISATEUR SPÉCIFIQUE ---
        user_id_str = str(interaction.user.id)
        if user_id_str in blocked_users:
            user_blocks = blocked_users[user_id_str]
            
            if "ALL" in user_blocks:
                if interaction.type == discord.InteractionType.application_command:
                    reason = user_blocks['ALL']
                    msg = t(langue, "bot_err_user_blocked_all", reason=reason, defaut=f"🛑 **Accès refusé** :\n> {reason}")
                    await interaction.response.send_message(msg, ephemeral=True)
                return False
            
            if cmd_name in user_blocks:
                if interaction.type == discord.InteractionType.application_command:
                    reason = user_blocks[cmd_name]
                    msg = t(langue, "bot_err_user_blocked_cmd", reason=reason, defaut=f"🛑 **Accès restreint** :\n> {reason}")
                    await interaction.response.send_message(msg, ephemeral=True)
                return False

        return True

    async def close(self):
        if hasattr(self, 'flag_watcher_task'): self.flag_watcher_task.cancel()
        if hasattr(self, 'status_task'): self.status_task.cancel()
        if hasattr(self, 'sync_topgg_votes_task'): self.sync_topgg_votes_task.cancel()
        if hasattr(self, 'post_server_count_task'): self.post_server_count_task.cancel()
        if hasattr(self, 'web_site'):
            await self.web_site.stop()
            await self.web_runner.cleanup()
        session = getattr(self, 'session', None)
        if session: 
            await session.close()
            
        await super().close()

bot = GGEAssistantBot()

# ===========================================
# 🚀 DÉMARRAGE
# ===========================================
if __name__ == "__main__":
    bot.run(TOKEN)