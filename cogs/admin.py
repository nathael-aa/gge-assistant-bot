# -*- coding: utf-8 -*-
import os
import io
import logging
import urllib.parse
from datetime import datetime, timedelta
import discord
from discord.ext import commands

# Import des outils partagés depuis la boîte à outils (Versions ASYNC)
from utils import (
    MON_ID_DISCORD, 
    BOT_VERSION, 
    BASE_DATA_PATH, 
    load_blocks_async, 
    save_blocks_async, 
    PaginationView,
    load_maintenance_async,
    setup_embed_footer,
    save_maintenance_async
)

logger = logging.getLogger("GGE_Bot")

class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ==========================================
    # 🔄 SYNCHRONISATION DES COMMANDES
    # ==========================================
    @commands.command(name="sync", hidden=True)
    async def sync_tree(self, ctx):
        """[CACHÉE] !sync : Synchronise l'arbre des commandes Slash."""
        if ctx.author.id != MON_ID_DISCORD: return
        msg = await ctx.send("⏳ Synchronisation de l'arbre des commandes en cours...")
        try:
            synced = await self.bot.tree.sync()
            await msg.edit(content=f"✅ L'arbre a été synchronisé avec succès ! ({len(synced)} commandes disponibles)")
            logger.info(f"🔄 Commandes slash synchronisées par {ctx.author.name}")
        except Exception as e:
            await msg.edit(content=f"❌ Erreur lors de la synchronisation : {e}")

    # ==========================================
    # 🛑 GESTION DU MODE MAINTENANCE
    # ==========================================
    @commands.command(name="m", hidden=True)
    async def toggle_maintenance(self, ctx):
        """[CACHÉE] !m : Bascule le bot en mode maintenance."""
        if ctx.author.id != MON_ID_DISCORD: return
        self.bot.maintenance_mode = not self.bot.maintenance_mode
        # 🔐 Sécurisé : Enregistrement asynchrone avec verrou
        await save_maintenance_async(self.bot.maintenance_mode)
        
        if self.bot.maintenance_mode:
            await ctx.send("🚧 **Mode Maintenance : 🔴 ACTIVÉ**\n*Le bot ignore toutes les commandes Slash (l'état survivra aux redémarrages).*")
            await self.bot.change_presence(activity=discord.Game(name="🚧 EN MAINTENANCE 🚧"), status=discord.Status.dnd)
        else:
            await ctx.send("🚧 **Mode Maintenance : 🟢 DÉSACTIVÉ**\n*Le bot est de nouveau accessible à tous.*")
            await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="E4K FR1 | /aide"), status=discord.Status.online)

    # ==========================================
    # ⛔ VIDEUR : ZONE DE BLOCAGE DES COMMANDES
    # ==========================================
    @commands.command(name="ban_cmd", hidden=True)
    async def ban_cmd(self, ctx, commande: str, *, raison: str = "Maintenance."):
        """[CACHÉE] !ban_cmd <nom_commande> [raison] : Désactive une commande globale."""
        if ctx.author.id != MON_ID_DISCORD: return
        # 🔐 Sécurisé : Lecture asynchrone avec verrou
        data = await load_blocks_async()
        cmd_clean = commande.replace("/", "").strip()
        data["global_commands"][cmd_clean] = raison
        # 🔐 Sécurisé : Écriture asynchrone avec verrou
        await save_blocks_async(data)
        await ctx.send(f"⛔ **Videur** : La commande `/{cmd_clean}` is bloquée pour tout le monde.\n*Raison : {raison}*")

    @commands.command(name="unban_cmd", hidden=True)
    async def unban_cmd(self, ctx, commande: str):
        """[CACHÉE] !unban_cmd <nom_commande> : Réactive une commande globale."""
        if ctx.author.id != MON_ID_DISCORD: return
        # 🔐 Sécurisé : Lecture asynchrone avec verrou
        data = await load_blocks_async()
        cmd_clean = commande.replace("/", "").strip()
        if cmd_clean in data.get("global_commands", {}):
            del data["global_commands"][cmd_clean]
            # 🔐 Sécurisé : Écriture asynchrone avec verrou
            await save_blocks_async(data)
            await ctx.send(f"✅ **Videur** : La commande `/{cmd_clean}` est de nouveau disponible.")
        else:
            await ctx.send(f"⚠️ La commande `/{cmd_clean}` n'était pas bloquée.")

    # ==========================================
    # 👤 VIDEUR : ZONE DE SANCTION DES JOUEURS
    # ==========================================
    @commands.command(name="ban_user", hidden=True)
    async def ban_user(self, ctx, user: discord.User, commande: str, *, raison: str = "Abus."):
        """[CACHÉE] !ban_user <@joueur/ID> <commande/ALL> [raison] : Punit un joueur."""
        if ctx.author.id != MON_ID_DISCORD: return
        # 🔐 Sécurisé : Lecture asynchrone avec verrou
        data = await load_blocks_async()
        uid = str(user.id)
        cmd_clean = commande.replace("/", "").strip()
        
        if uid not in data["blocked_users"]: 
            data["blocked_users"][uid] = {}
            
        data["blocked_users"][uid][cmd_clean] = raison
        # 🔐 Sécurisé : Écriture asynchrone avec verrou
        await save_blocks_async(data)
        
        scope = "Toutes les commandes" if cmd_clean == "ALL" else f"La commande `/{cmd_clean}`"
        await ctx.send(f"🛑 **Videur** : {user.name} (`{uid}`) a été restreint.\n*Cible : {scope}*\n*Raison : {raison}*")

    @commands.command(name="unban_user", hidden=True)
    async def unban_user(self, ctx, user: discord.User, commande: str):
        """[CACHÉE] !unban_user <@joueur/ID> <commande/ALL> : Pardonne un joueur."""
        if ctx.author.id != MON_ID_DISCORD: return
        # 🔐 Sécurisé : Lecture asynchrone avec verrou
        data = await load_blocks_async()
        uid = str(user.id)
        cmd_clean = commande.replace("/", "").strip()
        
        if uid in data.get("blocked_users", {}) and cmd_clean in data["blocked_users"][uid]:
            del data["blocked_users"][uid][cmd_clean]
            if not data["blocked_users"][uid]: 
                del data["blocked_users"][uid]
            # 🔐 Sécurisé : Écriture asynchrone avec verrou
            await save_blocks_async(data)
            
            scope = "toutes les commandes" if cmd_clean == "ALL" else f"`/{cmd_clean}`"
            await ctx.send(f"✅ **Videur** : {user.name} a été pardonné pour {scope}.")
        else:
            await ctx.send(f"⚠️ Aucun blocage actif trouvé pour {user.name} sur {cmd_clean}.")

    # ==========================================
    # 📊 SUIVI ET CONTRÔLE DES SERVEURS
    # ==========================================
    @commands.command(name="bot_serveurs", hidden=True)
    async def admin_serveurs(self, ctx):
        """[CACHÉE] !bot_serveurs : Affiche la liste des serveurs hébergeant le bot."""
        if ctx.author.id != MON_ID_DISCORD: return
        
        serveurs = self.bot.guilds
        nb_serveurs = len(serveurs)
        
        liste_serveurs = [f"• **{g.name}** (ID: `{g.id}`) - *{g.member_count} membres*" for g in serveurs]
        texte_serveurs = "\n".join(liste_serveurs)

        if len(texte_serveurs) > 1900:
            texte_serveurs = texte_serveurs[:1850] + "\n\n... *(Liste tronquée car trop longue)*"

        embed = discord.Embed(
            title="🤖 Serveurs connectés",
            description=f"Le bot est actuellement présent sur **{nb_serveurs} serveurs** :\n\n{texte_serveurs}",
            color=discord.Color.blue()
        )
        setup_embed_footer(embed, ctx)
        await ctx.send(embed=embed)

    @commands.command(name="bot_quitter", hidden=True)
    async def admin_quitter(self, ctx, serveur_id: str):
        """[CACHÉE] !bot_quitter <ID> : Force le bot à quitter un serveur distant."""
        if ctx.author.id != MON_ID_DISCORD: return

        try:
            guild = self.bot.get_guild(int(serveur_id))
            if guild is None:
                return await ctx.send(f"❌ Serveur introuvable. Vérifie l'ID `{serveur_id}`.")

            nom_serveur = guild.name
            await guild.leave()
            await ctx.send(f"✅ Succès : Le bot a quitté le serveur **{nom_serveur}**.")
            logger.info(f"🚪 Le bot a quitté le serveur {nom_serveur} ({serveur_id}) via commande admin.")
            
        except ValueError:
            await ctx.send("❌ L'ID fourni doit être composé uniquement de chiffres.")
        except Exception as e:
            await ctx.send(f"❌ Erreur lors de la tentative de sortie : {e}")

    # ==========================================
    # 📜 EXTRACTION DU JOURNAL SYSTÈME (LOGS)
    # ==========================================
    @commands.command(name="log", hidden=True)
    async def get_log(self, ctx, date_demandee: str = "aujourd'hui"):
        """[CACHÉE] !log [date/hier/aujourd'hui] : Extrait les journaux d'activité."""
        if ctx.author.id != MON_ID_DISCORD: return

        date_demandee = date_demandee.lower()
        aujourd_hui = datetime.now().strftime("%Y-%m-%d")
        
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

        if not os.path.exists(file_path):
            return await ctx.send(f"❌ Impossible de trouver l'archive de logs pour le **{target_date}**.")

        try:
            with open(file_path, 'rb') as f:
                log_data = f.read()
                
            fichier_virtuel = io.BytesIO(log_data)
            fichier_discord = discord.File(fp=fichier_virtuel, filename=f"GGE_Logs_{target_date}.txt")
            
            await ctx.send(content=f"📜 **Extraction réussie.** Voici les journaux système du **{target_date}** :", file=fichier_discord)
            logger.info(f"📤 Export des logs ({target_date}) envoyé à {ctx.author.name}.")
        except Exception as e:
            await ctx.send(f"❌ Erreur lors de la lecture des logs : {e}")

async def setup(bot):
    await bot.add_cog(AdminCog(bot))