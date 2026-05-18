# -*- coding: utf-8 -*-
import os
import io
import json
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler

import discord
from discord.ext import commands

# On importe nos constantes depuis notre boîte à outils
from utils import TOKEN, BOT_VERSION, MON_ID_DISCORD

# ==========================================
# ⚙️ INITIALISATION DU BOT ET DES LOGS
# ==========================================

os.makedirs('/app/logs', exist_ok=True)
os.makedirs('/app/data', exist_ok=True)

logger = logging.getLogger("GGE_Bot")
logger.setLevel(logging.INFO)

handler = TimedRotatingFileHandler('/app/logs/discord_bot.log', when="midnight", interval=1, backupCount=31, encoding='utf-8-sig')
def custom_log_namer(default_name):
    return default_name.replace(".log.", "_") + ".log"
handler.namer = custom_log_namer
formatter = logging.Formatter('%(asctime)s | [%(levelname)s] | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
handler.setFormatter(formatter)
logger.addHandler(handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

print("\n" + "█"*60 + "\n" + "█" + " "*18 + "NOUVEAU DÉMARRAGE DU BOT" + " "*16 + "█\n" + "█"*60 + "\n", flush=True)
logger.info("🟢 Démarrage du système de logs...")

# ==========================================
# 💾 GESTION DE LA SAUVEGARDE MAINTENANCE
# ==========================================
MAINTENANCE_FILE = '/app/data/maintenance.json'

def load_maintenance():
    if os.path.exists(MAINTENANCE_FILE):
        try:
            with open(MAINTENANCE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f).get("maintenance_mode", False)
        except:
            pass
    return False

def save_maintenance(etat):
    with open(MAINTENANCE_FILE, 'w', encoding='utf-8') as f:
        json.dump({"maintenance_mode": etat}, f)

# ==========================================
# 🤖 CONFIGURATION DU BOT DISCORD (Le Cœur)
# ==========================================
class GGEAssistantBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        super().__init__(
            command_prefix='!', 
            intents=intents,
            help_command=None
        )
        
        self.maintenance_mode = load_maintenance()
        self.tree.interaction_check = self.global_interaction_check
        self.session = None

    async def setup_hook(self):
        # 1. On crée la session réseau globale (INDISPENSABLE)
        self.session = aiohttp.ClientSession() 
        
        logger.info("🔌 Chargement des modules (Cogs)...")
        
        # 2. On charge TOUS les modules ici, une seule fois
        extensions = [
            "cogs.aide",
            "cogs.sanctions",
            "cogs.radar",
            "cogs.profils",
            "cogs.events",
            "cogs.guerre",
            "cogs.forteresses"
        ]
        
        for extension in extensions:
            try:
                await self.load_extension(extension)
                logger.info(f"✅ Module {extension} chargé.")
            except Exception as e:
                logger.error(f"❌ Erreur lors du chargement de {extension} : {e}")
        
        logger.info("✨ Tous les modules sont opérationnels !")

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()

    # 🛑 LE VIDEUR ET ESPION (Pour les commandes Slash /)
    async def global_interaction_check(self, interaction: discord.Interaction) -> bool:
        # --- 1. SYSTÈME DE LOGS ---
        try:
            if interaction.type == discord.InteractionType.application_command:
                cmd_name = interaction.command.qualified_name if interaction.command else interaction.data.get("name", "inconnue")
                lieu = interaction.guild.name if interaction.guild else "Message Privé"
                
                options_txt = ""
                params = []
                if "options" in interaction.data:
                    def extract_options(opts):
                        for opt in opts:
                            if opt.get("type") in (1, 2):  # Si c'est un groupe ou sous-commande
                                extract_options(opt.get("options", []))
                            elif "value" in opt:
                                params.append(f"{opt.get('name')}: {opt.get('value')}")
                    
                    extract_options(interaction.data.get("options", []))
                
                if params:
                    options_txt = f" | ⚙️ Paramètres ➔ [{', '.join(params)}]"

                logger.info(f"▶️ [COMMANDE] {interaction.user.name} a lancé `/{cmd_name}` sur [{lieu}]{options_txt}")
        except Exception as e:
            logger.error(f"⚠️ Erreur lors de l'écriture du log : {e}")

        # --- 2. SYSTÈME DE MAINTENANCE GLOBALE ---
        if self.maintenance_mode and interaction.user.id != MON_ID_DISCORD:
            if interaction.type == discord.InteractionType.application_command:
                await interaction.response.send_message(
                    "🚧 **En cours de maintenance !**\nMes gobelins travaillent actuellement dans la salle des machines pour ajouter de nouvelles fonctionnalités. Reviens dans quelques minutes !", 
                    ephemeral=True
                )
            return False # ⛔ On bloque la commande
            
        return True # ✅ Tout est bon, on laisse passer !

    # LOG POUR LES COMMANDES TEXTES CLASSIQUES (!sync, !m, !log)
    async def on_command(self, ctx: commands.Context):
        lieu = ctx.guild.name if ctx.guild else "Message Privé"
        logger.info(f"▶️ [ADMIN] {ctx.author.name} a lancé `!{ctx.command.name}` sur [{lieu}]")

    async def on_ready(self):
        logger.info(f"✅ Connecté avec succès en tant que {self.user.name} (ID: {self.user.id})")
        
        if self.maintenance_mode:
            await self.change_presence(activity=discord.Game(name="🚧 EN MAINTENANCE 🚧"), status=discord.Status.dnd)
            logger.warning("🚧 Attention : Le bot a démarré en MODE MAINTENANCE.")
        else:
            await self.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.watching, 
                    name="E4K FR1 | /aide"
                ),
                status=discord.Status.online
            )
        logger.info(f"🤖 {BOT_VERSION} est prêt !")


bot = GGEAssistantBot()

# ==========================================
# 🛠️ COMMANDE CACHÉE : SYNCHRONISATION
# ==========================================
@bot.command(name="sync")
async def sync_tree(ctx):
    if ctx.author.id != MON_ID_DISCORD:
        return await ctx.send("❌ Seul mon créateur peut faire ça.")
        
    msg = await ctx.send("⏳ Synchronisation de l'arbre des commandes en cours...")
    try:
        synced = await bot.tree.sync()
        await msg.edit(content=f"✅ L'arbre a été synchronisé avec succès ! ({len(synced)} commandes disponibles)")
        logger.info(f"🔄 Commandes slash synchronisées par {ctx.author.name}")
    except Exception as e:
        await msg.edit(content=f"❌ Erreur lors de la synchronisation : {e}")

# ==========================================
# 🚧 SYSTÈME DE MAINTENANCE GLOBALE (!m)
# ==========================================
@bot.command(name="m", hidden=True)
async def toggle_maintenance(ctx):
    if ctx.author.id != MON_ID_DISCORD:
        return

    bot.maintenance_mode = not bot.maintenance_mode
    save_maintenance(bot.maintenance_mode)
    
    if bot.maintenance_mode:
        await ctx.send("🚧 **Mode Maintenance : 🔴 ACTIVÉ**\n*Le bot ignore désormais toutes les commandes sauf les tiennes (et l'état survivra aux redémarrages).*")
        await bot.change_presence(activity=discord.Game(name="🚧 EN MAINTENANCE 🚧"), status=discord.Status.dnd)
    else:
        await ctx.send("🚧 **Mode Maintenance : 🟢 DÉSACTIVÉ**\n*Tout le monde peut à nouveau utiliser le bot.*")
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="E4K FR1 | /aide"), status=discord.Status.online)

# ==========================================
# 📜 COMMANDE EXTRACTION DES LOGS (!log)
# ==========================================
@bot.command(name="log", hidden=True)
async def get_log(ctx, date_demandee: str = "aujourd'hui"):
    """Envoie le fichier log en MP ou sur le serveur (Admin uniquement)"""
    
    if ctx.author.id != MON_ID_DISCORD:
        return await ctx.send("⛔ Accès classifié : Tu n'as pas l'autorisation de lire les journaux système.")

    date_demandee = date_demandee.lower()
    aujourd_hui = datetime.now().strftime("%Y-%m-%d")
    
    # 1. Déduction du chemin exact
    if date_demandee in ["aujourd'hui", "today", "j", ""]:
        target_date = aujourd_hui
        file_path = '/app/logs/discord_bot.log'
    elif date_demandee in ["hier", "yesterday", "h"]:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        file_path = f'/app/logs/discord_bot_{target_date}.log'
    else:
        target_date = date_demandee
        if target_date == aujourd_hui:
            file_path = '/app/logs/discord_bot.log'
        else:
            file_path = f'/app/logs/discord_bot_{target_date}.log'

    # 2. Vérification de l'existence
    if not os.path.exists(file_path):
        return await ctx.send(f"❌ Impossible de trouver l'archive pour le **{target_date}**.\n*(Chemin cherché : `{file_path}`)*")

    # 3. Lecture en mémoire (Pour contourner le verrou d'écriture du bot)
    try:
        with open(file_path, 'rb') as f:
            log_data = f.read()
            
        fichier_virtuel = io.BytesIO(log_data)
        fichier_discord = discord.File(fp=fichier_virtuel, filename=f"GGE_Logs_{target_date}.txt")
        
        await ctx.send(content=f"📜 **Extraction réussie.** Voici les journaux système du **{target_date}** :", file=fichier_discord)
        logger.info(f"📤 Export des logs ({target_date}) envoyé à {ctx.author.name}.")
        
    except Exception as e:
        await ctx.send(f"❌ Erreur lors de la lecture du fichier : {e}")

# ==========================================
# 🚀 DÉMARRAGE DU MOTEUR
# ==========================================
if __name__ == "__main__":
    if not TOKEN:
        logger.error("❌ ERREUR CRITIQUE : Aucun token Discord trouvé dans les variables d'environnement.")
    else:
        bot.run(TOKEN)