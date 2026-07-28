# -*- coding: utf-8 -*-
import os
import io
import json
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler

import discord
from discord.ext import commands, tasks

from utils import TOKEN, TOPGG_TOKEN, BOT_VERSION, MON_ID_DISCORD, load_maintenance, load_blocks_async, get_cached_data, CACHE, charger_langues, CONFIG_DIR, get_server_config, t

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
        # On va chercher les traductions dans les JSON via la fonction t()
        title = t(lang, "welcome_title", defaut="🏰 Welcome to GGE Assistant!")
        desc = t(lang, "welcome_desc", defaut="Here is how to get started:")
        
        embed = discord.Embed(title=title, description=desc, color=0x0B1D51)
        
        embed.add_field(
            name=t(lang, "welcome_step1_title", defaut="1️⃣ Configuration"), 
            value=t(lang, "welcome_step1_desc", defaut="Use </setup:0> to configure your profile."), 
            inline=False
        )
        embed.add_field(
            name=t(lang, "welcome_step2_title", defaut="2️⃣ Tools"), 
            value=t(lang, "welcome_step2_desc", defaut="Type </help:0> to see all commands."), 
            inline=False
        )
        embed.add_field(
            name=t(lang, "welcome_step3_title", defaut="3️⃣ Calendar"), 
            value=t(lang, "welcome_step3_desc", defaut="Use </calendar setup:0> to get alerts."), 
            inline=False
        )
        
        embed.set_footer(text=t(lang, "welcome_footer", defaut="Select your language below."))
        return embed

    @discord.ui.button(label="Français", emoji="🇫🇷", style=discord.ButtonStyle.secondary, custom_id="welc_fr")
    async def btn_fr(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.get_welcome_embed("fr"))

    @discord.ui.button(label="English", emoji="🇬🇧", style=discord.ButtonStyle.secondary, custom_id="welc_en")
    async def btn_en(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.get_welcome_embed("en"))

    @discord.ui.button(label="Deutsch", emoji="🇩🇪", style=discord.ButtonStyle.secondary, custom_id="welc_de")
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
        self.session = aiohttp.ClientSession()
        logger.info("🔌 Chargement des modules ...")
        extensions = [
            "cogs.admin", 
            "cogs.aide", 
            "cogs.calendrier", 
            "cogs.classement",
            "cogs.config", 
            "cogs.events", 
            "cogs.forteresses", 
            "cogs.guerre", 
            "cogs.profils", 
            "cogs.radar",
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
            
        if not self.topgg_update_task.is_running():
            self.topgg_update_task.start()
            logger.info("🛰️ [Tasks] topgg_update_task initialisée dans le setup_hook.")

        self.add_view(WelcomeView())

    async def on_ready(self):
        charger_langues()
        logger.info(f"✅ Bot connecté en tant que {self.user} (ID: {self.user.id})")
        
        if self.maintenance_mode:
            statut_maint = t("fr", "bot_activity_maintenance", defaut="🚧 EN MAINTENANCE 🚧")
            activity = discord.Activity(type=discord.ActivityType.watching, name=statut_maint)
            target_status = discord.Status.dnd  # 🔴 Pastille rouge (Ne pas déranger)
        else:
            activity = discord.Activity(type=discord.ActivityType.watching, name="/setup ➔ /help")
            target_status = discord.Status.online # 🟢 Pastille verte (En ligne)

        await self.change_presence(activity=activity, status=target_status)
        
        logger.info(f"📡 Statut mis à jour : {activity.name} | Pastille : {target_status}")

    # ==========================================
    # 📥 SUIVI DES AJOUTS / RETRAITS DU BOT
    # ==========================================
    async def on_guild_join(self, guild: discord.Guild):
        """Déclenché quand le bot est ajouté sur un nouveau serveur."""
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
        """Déclenché quand le bot est expulsé ou quitte un serveur."""
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
        # Si le bot est en maintenance, on fige le statut sur la maintenance !
        if self.maintenance_mode:
            statut_maint = t("fr", "bot_activity_maintenance", defaut="🚧 EN MAINTENANCE 🚧")
            activity = discord.Activity(type=discord.ActivityType.watching, name=statut_maint)
            await self.change_presence(activity=activity, status=discord.Status.dnd)
            return

        # 1. Calculs en direct
        nb_serveurs = len(self.guilds)
        nb_membres = sum(guild.member_count for guild in self.guilds if guild.member_count)
        dossier_serveurs = Path('/app/data/server_scans')
        if dossier_serveurs.exists():
            nb_gge_serveurs = sum(1 for d in dossier_serveurs.iterdir() if d.is_dir())
        else:
            nb_gge_serveurs = 0

        # 2. Liste de tes statuts tournants
        statuts = [
            # Affichera : "Écoute ⚙️ /setup | Start here"
            discord.Activity(type=discord.ActivityType.listening, name="⚙️ /setup | Start here"),
            
            # Affichera : "Regarde 📖 /help | All commands"
            discord.Activity(type=discord.ActivityType.watching, name="📖 /help | All commands"),
            
            # Affichera : "Regarde 🌍 15 servers | 👥 3500 users"
            discord.Activity(type=discord.ActivityType.watching, name=f"🌍 {nb_serveurs} servers | 👥 {nb_membres} users"),
            
            # Affichera : "Participe à ⚔️ 12 GGE servers"
            discord.Activity(type=discord.ActivityType.competing, name=f"⚔️ {nb_gge_serveurs} GGE servers"),
            
            # Affichera : "Joue à 🚀 Version 1.1.1"
            discord.Activity(type=discord.ActivityType.playing, name=f"🚀 {BOT_VERSION}")
        ]

        if self.custom_status:
            # On le met en mode "Joue à" suivi de ton message (ex: Joue à ⚠️ Commande /radar HS)
            statuts.append(discord.Activity(type=discord.ActivityType.playing, name=self.custom_status))

        # 3. Choix du statut et incrémentation
        activity = statuts[self.status_index % len(statuts)]
        self.status_index += 1

        # 4. Envoi à Discord
        await self.change_presence(activity=activity, status=discord.Status.online)

    @status_task.before_loop
    async def before_status_task(self):
        await self.wait_until_ready()

    # ==========================================
    # 📈 SYNCHRONISATION TOP.GG
    # ==========================================
    @tasks.loop(minutes=3600)
    async def topgg_update_task(self):
        """Envoie le nombre de serveurs Discord à Top.gg toutes les 30 minutes."""
        if not TOPGG_TOKEN:
            return

        # Top.gg demande le nombre de serveurs Discord (guilds), pas les serveurs GGE
        serveurs_count = len(self.guilds)
        
        url = f"https://top.gg/api/bots/{self.user.id}/stats"
        headers = {"Authorization": TOPGG_TOKEN}
        payload = {"server_count": serveurs_count}

        try:
            # On utilise self.session qui est déjà initialisé dans ton setup_hook
            async with self.session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    logger.info(f"📈 [Top.gg] Mise à jour réussie : {serveurs_count} serveurs.")
                else:
                    logger.warning(f"⚠️ [Top.gg] Erreur HTTP {response.status} lors de la mise à jour.")
        except Exception as e:
            logger.error(f"❌ [Top.gg] Erreur de connexion : {e}")

    @topgg_update_task.before_loop
    async def before_topgg_task(self):
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
                logger.info(f"▶️ [COMMANDE] {interaction.user.name} a lancé `/{cmd_name}` sur [{lieu}]{options_txt}")
        except Exception as e:
            logger.error(f"⚠️ Erreur lors de l'écriture du log : {e}")

        # --- 2. VÉRIFICATION DE LA CONFIGURATION (PERSONNELLE UNIQUEMENT) ---
        commandes_libres = ["setup", "help", "contact", "changelog"]
        
        if cmd_name not in commandes_libres:
            config_ok = False
            
            # On vérifie uniquement le profil du joueur
            fichier_users = CONFIG_DIR / 'users.json'
            if fichier_users.exists():
                with open(fichier_users, 'r', encoding='utf-8') as f:
                    if str(interaction.user.id) in json.load(f):
                        config_ok = True
            
            # Si le joueur n'est pas dans users.json
            if not config_ok:
                if interaction.type == discord.InteractionType.application_command:
                    # Le </setup:0> créera un lien cliquable natif sur Discord !
                    msg = t(langue, "bot_err_config_dm", defaut="⚠️ **Halte là !**\nTu n'as pas encore configuré ton profil personnel. Utilise la commande </setup:0> pour définir ton serveur et ta langue avant d'utiliser le bot.")
                    await interaction.response.send_message(msg, ephemeral=True)
                return False

        # --- 2.5. VÉRIFICATION DES COMMANDES STRICTEMENT PRIVÉES (DM ONLY) ---
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
            "radar alliance remove"
        ]

        if interaction.guild and cmd_name in commandes_privees:
            if interaction.type == discord.InteractionType.application_command:
                msg = t(langue, "err_dm_only", defaut="<:error:1512505075220611172> Cette commande est uniquement utilisable en **Messages Privés**.")
                await interaction.response.send_message(msg, ephemeral=True)
            return False

        # --- 2.6. VÉRIFICATION DES COMMANDES STRICTEMENT SERVEUR (GUILD ONLY) ---

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
            "diplomacy list"
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
            
            # Blocage TOTAL
            if "ALL" in user_blocks:
                if interaction.type == discord.InteractionType.application_command:
                    reason = user_blocks['ALL']
                    msg = t(langue, "bot_err_user_blocked_all", reason=reason, defaut=f"🛑 **Accès refusé** :\n> {reason}")
                    await interaction.response.send_message(msg, ephemeral=True)
                return False
            
            # Blocage COMMANDE PRÉCISE
            if cmd_name in user_blocks:
                if interaction.type == discord.InteractionType.application_command:
                    reason = user_blocks[cmd_name]
                    msg = t(langue, "bot_err_user_blocked_cmd", reason=reason, defaut=f"🛑 **Accès restreint** :\n> {reason}")
                    await interaction.response.send_message(msg, ephemeral=True)
                return False

        return True

    async def close(self):
        self.flag_watcher_task.cancel()
        self.status_task.cancel()
        if self.session: 
            await self.session.close()
        await super().close()

bot = GGEAssistantBot()

# ==========================================
# 🚀 DÉMARRAGE
# ==========================================
if __name__ == "__main__":
    bot.run(TOKEN)