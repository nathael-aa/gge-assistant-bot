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

from utils import TOKEN, BOT_VERSION, MON_ID_DISCORD, load_maintenance, load_blocks_async, get_cached_data, CACHE, charger_langues, CONFIG_DIR, get_server_config, t

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
            "cogs.line_bridge"
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
    # 🛑 LE VIDEUR UNIQUE ET UNIVERSEL
    # ==========================================
    async def global_interaction_check(self, interaction: discord.Interaction) -> bool:
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

        # --- 2. VÉRIFICATION DE LA CONFIGURATION (SERVEURS ET DMs) ---
        commandes_libres = ["setup", "help", "contact", "changelog"]
        
        if cmd_name not in commandes_libres:
            config_ok = False
            
            # Vérif 1 : Config personnelle de l'utilisateur (Priorité absolue)
            fichier_users = CONFIG_DIR / 'users.json'
            if fichier_users.exists():
                with open(fichier_users, 'r', encoding='utf-8') as f:
                    if str(interaction.user.id) in json.load(f):
                        config_ok = True
            
            # Vérif 2 : Config du Serveur Discord (si on est sur un serveur et pas de config perso)
            if not config_ok and interaction.guild:
                fichier_serveurs = CONFIG_DIR / 'serveurs.json'
                if fichier_serveurs.exists():
                    with open(fichier_serveurs, 'r', encoding='utf-8') as f:
                        if str(interaction.guild_id) in json.load(f):
                            config_ok = True
            
            # Si aucune configuration n'est trouvée du tout
            if not config_ok:
                if interaction.type == discord.InteractionType.application_command:
                    if interaction.guild:
                        msg = t(langue, "bot_err_config_server", defaut="❌ **Configuration Requise**\nCe serveur n'est pas configuré. Tu dois d'abord utiliser la commande `/setup` (Portée: Personnel) pour créer ton profil, ou demander à un admin de configurer le serveur.")
                    else:
                        msg = t(langue, "bot_err_config_dm", defaut="❌ **Configuration Requise**\nPour utiliser le bot en Message Privé, tu dois d'abord configurer ton profil.\nTape la commande `/setup` (Portée: Personnel) pour choisir ton serveur GGE.")
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
        if self.session: 
            await self.session.close()
        await super().close()

bot = GGEAssistantBot()

# ==========================================
# 🚀 DÉMARRAGE
# ==========================================
if __name__ == "__main__":
    bot.run(TOKEN)