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

# On importe nos constantes et outils depuis la boîte à outils
from utils import TOKEN, BOT_VERSION, MON_ID_DISCORD, load_maintenance, load_blocks_async, get_cached_data, CACHE

# ==========================================
# ⚙️ INITIALISATION DU BOT ET DES LOGS
# ==========================================
os.makedirs('/app/logs', exist_ok=True)
os.makedirs('/app/data', exist_ok=True)

logger = logging.getLogger("GGE_Bot")
logger.setLevel(logging.INFO)

# 📅 Le formateur magique qui gère la date et l'heure
formatter = logging.Formatter('%(asctime)s | [%(levelname)s] | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# 1. Gestionnaire pour le fichier de logs (Rotation à minuit)
file_handler = TimedRotatingFileHandler('/app/logs/discord_bot.log', when="midnight", interval=1, backupCount=31, encoding='utf-8-sig')
def custom_log_namer(default_name): return default_name.replace(".log.", "_") + ".log"
file_handler.namer = custom_log_namer
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# 2. Gestionnaire pour la console (StreamHandler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# 👁️ Le voilà de retour !
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

    async def setup_hook(self):
        self.session = aiohttp.ClientSession()
        logger.info("🔌 Chargement des modules (Cogs)...")
        extensions = [
            "cogs.aide", 
            "cogs.radar", 
            "cogs.profils", 
            "cogs.events", 
            "cogs.calendrier", 
            "cogs.guerre", 
            "cogs.forteresses", 
            "cogs.line_bridge", 
            "cogs.admin"
        ]
        for ext in extensions:
            try:
                await self.load_extension(ext)
                logger.info(f"✅ Module {ext} chargé.")
            except Exception as e:
                logger.error(f"❌ Erreur {ext} : {e}")

        # 🚀 Lancement de la tâche UNE SEULE FOIS ici au démarrage global
        if not self.flag_watcher_task.is_running():
            self.flag_watcher_task.start()
            logger.info("🛰️ [Tasks] flag_watcher_task initialisée dans le setup_hook.")

    async def on_ready(self):
        # 1. Log de connexion
        logger.info(f"✅ Bot connecté en tant que {self.user} (ID: {self.user.id})")
        
        # 2. Mise à jour du statut sur Discord
        activity = discord.Activity(
            type=discord.ActivityType.watching, 
            name="/aide | E4K_FR1 🏰"
        )
        await self.change_presence(activity=activity)
        logger.info(f"📡 Statut mis à jour : {activity.name}")

    # ==========================================
    # 🛰️ BOUCLE DE VEILLE DE FIN DE SCAN (scan.flag)
    # ==========================================
    @tasks.loop(seconds=15)
    async def flag_watcher_task(self):
        """Surveille la présence du fichier flag pour vider et recharger la RAM en direct"""
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
                    # On force l'expiration temporelle du cache
                    CACHE['last_refresh'] = 0 
                    # On relance la lecture lourde en arrière-plan (non bloquant pour le bot)
                    await get_cached_data()
                    logger.info("✅ [Watcher] Synchronisation matérielle réussie ! Le cache mémoire exploite les nouvelles données fraîches.")
                except Exception as e:
                    logger.error(f"❌ [Watcher] Échec de l'actualisation automatique de la mémoire : {e}")

    # ==========================================
    # 🛑 LE VIDEUR ET ESPION (Pour les commandes Slash /)
    # ==========================================
    async def global_interaction_check(self, interaction: discord.Interaction) -> bool:
        # Récupération du nom de la commande
        cmd_name = interaction.command.qualified_name if interaction.command else interaction.data.get("name", "inconnue")
        
        # --- 1. SYSTÈME DE LOGS ---
        try:
            if interaction.type == discord.InteractionType.application_command:
                lieu = interaction.guild.name if interaction.guild else "Message Privé"
                
                # Extraction des paramètres pour les logs
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

        # --- 2. BYPASS : Le créateur a toujours accès à tout ---
        if interaction.user.id == MON_ID_DISCORD:
            return True

        # --- 3. MAINTENANCE GLOBALE ---
        if self.maintenance_mode:
            if interaction.type == discord.InteractionType.application_command:
                await interaction.response.send_message(
                    "🚧 **En cours de maintenance !**\nMes gobelins travaillent pour réactiver l'ensemble des fonctionnalités au plus vite.", 
                    ephemeral=True
                )
            return False 

        # --- 4. CHARGEMENT DES RÈGLES ---
        blocks_data = await load_blocks_async()
        global_cmds = blocks_data.get("global_commands", {})
        blocked_users = blocks_data.get("blocked_users", {})

        # --- 5. BLOCAGE COMMANDE GLOBALE ---
        if cmd_name in global_cmds:
            if interaction.type == discord.InteractionType.application_command:
                await interaction.response.send_message(f"⛔ **Commande désactivée** :\n> {global_cmds[cmd_name]}", ephemeral=True)
            return False

        # --- 6. BLOCAGE UTILISATEUR SPÉCIFIQUE ---
        user_id_str = str(interaction.user.id)
        if user_id_str in blocked_users:
            user_blocks = blocked_users[user_id_str]
            
            # Blocage TOTAL
            if "ALL" in user_blocks:
                if interaction.type == discord.InteractionType.application_command:
                    await interaction.response.send_message(f"🛑 **Accès refusé** :\n> {user_blocks['ALL']}", ephemeral=True)
                return False
            
            # Blocage COMMANDE PRÉCISE
            if cmd_name in user_blocks:
                if interaction.type == discord.InteractionType.application_command:
                    await interaction.response.send_message(f"🛑 **Accès restreint** :\n> {user_blocks[cmd_name]}", ephemeral=True)
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