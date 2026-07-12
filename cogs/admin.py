# -*- coding: utf-8 -*-
import os
import io
import json
import logging
import urllib.parse
import asyncio
from datetime import datetime, timedelta
import discord
from discord import app_commands
from discord.ext import commands

from utils import (
    MON_ID_DISCORD, 
    BOT_VERSION,       
    BASE_DIR,       
    CONFIG_DIR,     
    t,              
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
        self.admin_lang = "fr"

    # ==========================================
    # 🆘 MENU D'AIDE ADMINISTRATEUR
    # ==========================================
    @commands.command(name="ahelp", aliases=["admin_help", "adminhelp"], hidden=True)
    async def admin_help(self, ctx):
        """[CACHÉE] !ahelp : Affiche le récapitulatif des commandes admin."""
        if ctx.author.id != MON_ID_DISCORD: return

        embed = discord.Embed(
            title="🛠️ Panneau de Contrôle Administrateur",
            description="Voici la liste de tes commandes secrètes (utilisables avec le préfixe `!`).",
            color=discord.Color.dark_red()
        )

        # Catégorie Système & Scripts
        embed.add_field(
            name="⚙️ Système & Scripts",
            value="`!sync` ➔ Synchronise les commandes Slash.\n"
                  "`!m` ➔ Active/Désactive le mode maintenance.\n"
                  "`!scan_manuel` ➔ Lance `auto_pa_daily.sh` en fond.\n"
                  "`!log [date]` ➔ Télécharge les logs (ex: *today, hier, 2026-07-08*).",
            inline=False
        )

        # Catégorie Sécurité (Videur)
        embed.add_field(
            name="⛔ Videur & Modération",
            value="`!ban_cmd [cmd] [raison]` ➔ Désactive une commande.\n"
                  "`!unban_cmd [cmd]` ➔ Réactive une commande.\n"
                  "`!ban_user [@user] [cmd ou ALL]` ➔ Restreint un utilisateur.\n"
                  "`!unban_user [@user] [cmd ou ALL]` ➔ Lève la restriction.",
            inline=False
        )

        # Catégorie Gestion des Serveurs
        embed.add_field(
            name="🤖 Serveurs Discord",
            value="`!bot_servers` ➔ Liste les serveurs utilisant le bot.\n"
                  "`!bot_leave [ID]` ➔ Force le bot à quitter un serveur.",
            inline=False
        )

        # Catégorie Traductions
        embed.add_field(
            name="🌐 Traductions (i18n)",
            value="`!i18n_sync` ➔ Scanne le code et met à jour les `.json`.\n"
                  "`!i18n_export [lang]` ➔ Génère un `.csv` pour les traducteurs.",
            inline=False
        )

        embed.set_footer(text="Ces commandes sont invisibles pour les utilisateurs normaux.")
        await ctx.send(embed=embed)

    # ==========================================
    # 🔄 SYNCHRONISATION DES COMMANDES
    # ==========================================
    @commands.command(name="sync", hidden=True)
    async def sync_tree(self, ctx):
        """[CACHÉE] !sync : Synchronise l'arbre des commandes Slash."""
        if ctx.author.id != MON_ID_DISCORD: return
        
        msg_start = t(self.admin_lang, "admin_sync_start", defaut="⏳ Synchronisation de l'arbre des commandes en cours...")
        msg = await ctx.send(msg_start)
        
        try:
            synced = await self.bot.tree.sync()
            msg_success = t(self.admin_lang, "admin_sync_success", count=len(synced), defaut=f"✅ L'arbre a été synchronisé avec succès ! ({len(synced)} commandes disponibles)")
            await msg.edit(content=msg_success)
            logger.info(f"🔄 Commandes slash synchronisées par {ctx.author.name}")
        except Exception as e:
            msg_err = t(self.admin_lang, "admin_sync_error", error=str(e), defaut=f"❌ Erreur lors de la synchronisation : {e}")
            await msg.edit(content=msg_err)

    # ==========================================
    # 🛑 GESTION DU MODE MAINTENANCE
    # ==========================================
    @commands.command(name="m", hidden=True)
    async def toggle_maintenance(self, ctx):
        """[CACHÉE] !m : Bascule le bot en mode maintenance."""
        if ctx.author.id != MON_ID_DISCORD: return
        
        self.bot.maintenance_mode = not self.bot.maintenance_mode
        await save_maintenance_async(self.bot.maintenance_mode)
        
        langue = getattr(self, "admin_lang", "fr")
        
        if self.bot.maintenance_mode:
            msg = t(langue, "admin_maint_on", defaut="🚧 **Mode Maintenance : 🔴 ACTIVÉ**\n*Le bot ignore toutes les commandes Slash.*")
            await ctx.send(msg)
            
            statut_maint = t("fr", "bot_activity_maintenance", defaut="🚧 EN MAINTENANCE 🚧")
            activity = discord.Activity(type=discord.ActivityType.watching, name=statut_maint)
            await self.bot.change_presence(activity=activity, status=discord.Status.dnd)
        else:
            msg = t(langue, "admin_maint_off", defaut="🚧 **Mode Maintenance : 🟢 DÉSACTIVÉ**\n*Le bot est de nouveau accessible à tous.*")
            await ctx.send(msg)
            
            activity = discord.Activity(type=discord.ActivityType.watching, name="/setup ➔ /help")
            await self.bot.change_presence(activity=activity, status=discord.Status.online)

    # ==========================================
    # ⛔ VIDEUR : ZONE DE BLOCAGE DES COMMANDES
    # ==========================================
    @commands.command(name="ban_cmd", hidden=True)
    async def ban_cmd(self, ctx, command: str, *, reason: str = "Maintenance."):
        """[CACHÉE] !ban_cmd [command] [reason] : Bloque une commande globalement."""
        if ctx.author.id != MON_ID_DISCORD: return
        data = await load_blocks_async()
        cmd_clean = command.replace("/", "").strip()
        data["global_commands"][cmd_clean] = reason
        await save_blocks_async(data)
        
        msg = t(self.admin_lang, "admin_ban_cmd", cmd=cmd_clean, reason=reason, defaut=f"⛔ **Videur** : La commande `/{cmd_clean}` est bloquée pour tout le monde.\n*Raison : {reason}*")
        await ctx.send(msg)

    @commands.command(name="unban_cmd", hidden=True)
    async def unban_cmd(self, ctx, command: str):
        """[CACHÉE] !unban_cmd [command] : Débloque une commande globalement."""
        if ctx.author.id != MON_ID_DISCORD: return
        data = await load_blocks_async()
        cmd_clean = command.replace("/", "").strip()
        
        if cmd_clean in data.get("global_commands", {}):
            del data["global_commands"][cmd_clean]
            await save_blocks_async(data)
            msg = t(self.admin_lang, "admin_unban_cmd_success", cmd=cmd_clean, defaut=f"✅ **Videur** : La commande `/{cmd_clean}` est de nouveau disponible.")
            await ctx.send(msg)
        else:
            msg = t(self.admin_lang, "admin_unban_cmd_fail", cmd=cmd_clean, defaut=f"⚠️ La commande `/{cmd_clean}` n'était pas bloquée.")
            await ctx.send(msg)

    # ==========================================
    # 👤 VIDEUR : ZONE DE SANCTION DES JOUEURS
    # ==========================================
    @commands.command(name="ban_user", hidden=True)
    async def ban_user(self, ctx, user: discord.User, command: str, *, reason: str = "Abus."):
        """[CACHÉE] !ban_user [user] [command] [reason] : Restreint un utilisateur."""
        if ctx.author.id != MON_ID_DISCORD: return
        data = await load_blocks_async()
        uid = str(user.id)
        cmd_clean = command.replace("/", "").strip()
        
        if uid not in data["blocked_users"]: 
            data["blocked_users"][uid] = {}
            
        data["blocked_users"][uid][cmd_clean] = reason
        await save_blocks_async(data)
        
        scope = "Toutes les commandes" if cmd_clean == "ALL" else f"La commande `/{cmd_clean}`"
        msg = t(self.admin_lang, "admin_ban_user", user=user.name, uid=uid, scope=scope, reason=reason, defaut=f"🛑 **Videur** : {user.name} (`{uid}`) a été restreint.\n*Cible : {scope}*\n*Raison : {reason}*")
        await ctx.send(msg)

    @commands.command(name="unban_user", hidden=True)
    async def unban_user(self, ctx, user: discord.User, command: str):
        """[CACHÉE] !unban_user [user] [command] : Lève la restriction d'un utilisateur."""
        if ctx.author.id != MON_ID_DISCORD: return
        data = await load_blocks_async()
        uid = str(user.id)
        cmd_clean = command.replace("/", "").strip()
        
        if uid in data.get("blocked_users", {}) and cmd_clean in data["blocked_users"][uid]:
            del data["blocked_users"][uid][cmd_clean]
            if not data["blocked_users"][uid]: 
                del data["blocked_users"][uid]
            await save_blocks_async(data)
            
            scope = "toutes les commandes" if cmd_clean == "ALL" else f"`/{cmd_clean}`"
            msg = t(self.admin_lang, "admin_unban_user_success", user=user.name, scope=scope, defaut=f"✅ **Videur** : {user.name} a été pardonné pour {scope}.")
            await ctx.send(msg)
        else:
            msg = t(self.admin_lang, "admin_unban_user_fail", user=user.name, cmd=cmd_clean, defaut=f"⚠️ Aucun blocage actif trouvé pour {user.name} sur {cmd_clean}.")
            await ctx.send(msg)

    # ==========================================
    # 📊 SUIVI ET CONTRÔLE DES SERVEURS
    # ==========================================
    @commands.command(name="bot_servers", hidden=True)
    async def admin_serveurs(self, ctx):
        """[CACHÉE] !bot_servers : Liste les serveurs Discord sur lesquels le bot est présent."""
        if ctx.author.id != MON_ID_DISCORD: return
        serveurs = self.bot.guilds
        nb_serveurs = len(serveurs)
        liste_serveurs = [f"• **{g.name}** (ID: `{g.id}`) - *{g.member_count} membres*" for g in serveurs]
        texte_serveurs = "\n".join(liste_serveurs)

        trunc_msg = t(self.admin_lang, "admin_servers_trunc", defaut="\n\n... *(Liste tronquée)*")
        if len(texte_serveurs) > 1900:
            texte_serveurs = texte_serveurs[:1850] + trunc_msg

        embed_title = t(self.admin_lang, "admin_servers_title", defaut="🤖 Serveurs connectés")
        embed_desc = t(self.admin_lang, "admin_servers_desc", count=nb_serveurs, liste=texte_serveurs, defaut=f"Le bot est actuellement présent sur **{nb_serveurs} serveurs** :\n\n{texte_serveurs}")

        embed = discord.Embed(
            title=embed_title,
            description=embed_desc,
            color=discord.Color.blue()
        )
        await setup_embed_footer(embed, ctx, self.admin_lang)
        await ctx.send(embed=embed)

    @commands.command(name="bot_leave", hidden=True)
    async def admin_quitter(self, ctx, server_id: str):
        """[CACHÉE] !bot_leave [server_id] : Force le bot à quitter un serveur Discord."""
        if ctx.author.id != MON_ID_DISCORD: return
        try:
            guild = self.bot.get_guild(int(server_id))
            if guild is None:
                msg = t(self.admin_lang, "admin_leave_fail", id=server_id, defaut=f"❌ Serveur introuvable. Vérifie l'ID `{server_id}`.")
                return await ctx.send(msg)
                
            nom_serveur = guild.name
            await guild.leave()
            
            msg = t(self.admin_lang, "admin_leave_success", nom=nom_serveur, defaut=f"✅ Succès : Le bot a quitté le serveur **{nom_serveur}**.")
            await ctx.send(msg)
            logger.info(f"🚪 Le bot a quitté le serveur {nom_serveur} via commande admin.")
        except Exception as e:
            msg = t(self.admin_lang, "admin_leave_error", error=str(e), defaut=f"❌ Erreur : {e}")
            await ctx.send(msg)

    # ==========================================
    # 🚀 DÉCLENCHEMENT MANUEL DES SCANNERS
    # ==========================================
    @commands.command(name="scan_manuel", hidden=True)
    async def scan_manuel(self, ctx):
        """[CACHÉE] !scan_manuel : Lance manuellement les scanners Python."""
        if ctx.author.id != MON_ID_DISCORD: return

        msg = await ctx.send("⏳ **Lancement manuel des scanners...** (Scan Serveur puis Murs)")

        try:
            # 1. Scanner de serveurs
            logger.info(f"🚀 Lancement manuel du ServerScanner par {ctx.author.name}")
            process_serveur = await asyncio.create_subprocess_exec(
                "python3", "/app/scanners/server_scanner.py"
            )
            await process_serveur.wait()

            if process_serveur.returncode != 0:
                await ctx.send("❌ **Erreur dans le Scanner Serveur.** Scan des murs annulé.")
                return
                
        except Exception as e:
            logger.error(f"❌ Erreur critique scan manuel : {e}")
            await ctx.send(f"⚠️ **Erreur lors de l'exécution :**\n```py\n{e}\n```")

    # ==========================================
    # 📜 EXTRACTION DU JOURNAL SYSTÈME (LOGS)
    # ==========================================
    @commands.command(name="log", hidden=True)
    async def get_log(self, ctx, requested_date: str = "today"):
        """[CACHÉE] !log [date] : Récupère les logs système pour une date spécifique."""
        if ctx.author.id != MON_ID_DISCORD: return

        requested_date = requested_date.lower()
        aujourd_hui = datetime.now().strftime("%Y-%m-%d")
        
        logs_dir = BASE_DIR / 'logs' / 'general'
        
        if requested_date in ["aujourd'hui", "today", "j", ""]:
            target_date = aujourd_hui
            file_path = logs_dir / 'discord_bot.log'
        elif requested_date in ["hier", "yesterday", "h"]:
            target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            file_path = logs_dir / f'discord_bot_{target_date}.log'
        else:
            target_date = requested_date
            if target_date == aujourd_hui:
                file_path = logs_dir / 'discord_bot.log'
            else:
                file_path = logs_dir / f'discord_bot_{target_date}.log'

        if not file_path.exists():
            msg = t(self.admin_lang, "admin_log_not_found", date=target_date, defaut=f"❌ Impossible de trouver l'archive de logs pour le **{target_date}**.")
            return await ctx.send(msg)

        try:
            with open(file_path, 'rb') as f:
                log_data = f.read()
                
            fichier_virtuel = io.BytesIO(log_data)
            fichier_discord = discord.File(fp=fichier_virtuel, filename=f"GGE_Logs_{target_date}.txt")
            
            msg = t(self.admin_lang, "admin_log_success", date=target_date, defaut=f"📜 **Extraction réussie.** Voici les journaux système du **{target_date}** :")
            await ctx.send(content=msg, file=fichier_discord)
            logger.info(f"📤 Export des logs ({target_date}) envoyé à {ctx.author.name}.")
        except Exception as e:
            msg = t(self.admin_lang, "admin_log_error", error=str(e), defaut=f"❌ Erreur lors de la lecture des logs : {e}")
            await ctx.send(msg)

    # ==========================================
    # 🧹 NETTOYAGE ET SYNCHRONISATION I18N
    # ==========================================
    @commands.command(name="i18n_sync", hidden=True)
    async def i18n_sync(self, ctx):
        """[CACHÉE] !i18n_sync : Scanne le code et met à jour les fichiers JSON."""
        if ctx.author.id != MON_ID_DISCORD: return
        
        import os, re, json
        from utils import BASE_DIR, LOCALES_DIR, charger_langues
        
        # 1. Scanner le code Python pour extraire les clés exactes
        pattern = re.compile(r'\bt\s*\(\s*[^,]+,\s*["\']([a-zA-Z0-9_]+)["\']')
        cles_utilisees = set()
        
        for root, dirs, files in os.walk(BASE_DIR):
            if '.git' in root or '__pycache__' in root: continue
            for file in files:
                if file.endswith('.py'):
                    with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                        cles_utilisees.update(pattern.findall(f.read()))
                        
        # 2. Ajouter manuellement les clés dynamiques
        for i in range(1, 13): 
            cles_utilisees.add(f"month_{i:02d}")
            
        cles_utilisees.update([
            "cal_ev_nomad", "cal_ev_samurai", "cal_ev_bloodcrow", "cal_ev_realms", 
            "cal_ev_storm", "cal_ev_berimond", "cal_ev_bladecoast", "cal_ev_rift", 
            "cal_ev_tournament", "cal_ev_horizon", "cal_ev_outer", "cal_ev_patronage", 
            "cal_ev_nobility"
        ])
        
        log_msg = []
        
        # 3. Traitement de FR.JSON
        fr_file = LOCALES_DIR / 'fr.json'
        if not fr_file.exists():
            return await ctx.send("❌ Impossible de trouver `fr.json`.")
            
        with open(fr_file, 'r', encoding='utf-8') as f:
            fr_data = json.load(f)
            
        cles_existantes_fr = set(fr_data.keys())
        manquantes_fr = cles_utilisees - cles_existantes_fr
        inutiles_fr = cles_existantes_fr - cles_utilisees
        
        for c in manquantes_fr: 
            fr_data[c] = "[TODO] Texte manquant"
        for c in inutiles_fr: 
            del fr_data[c]
            
        fr_data = dict(sorted(fr_data.items()))
        with open(fr_file, 'w', encoding='utf-8') as f:
            json.dump(fr_data, f, indent=2, ensure_ascii=False)
            
        log_msg.append(f"🇫🇷 **fr.json** : {len(manquantes_fr)} ajoutées, {len(inutiles_fr)} supprimées.")
        
        # 4. Traitement des autres langues (en, de) par rapport au fichier
        for lang in ['en', 'de']:
            lang_file = LOCALES_DIR / f'{lang}.json'
            if not lang_file.exists(): continue
            
            with open(lang_file, 'r', encoding='utf-8') as f:
                lang_data = json.load(f)
                
            l_existantes = set(lang_data.keys())
            l_manquantes = set(fr_data.keys()) - l_existantes
            l_inutiles = l_existantes - set(fr_data.keys())
            
            for c in l_manquantes: 
                texte_aide = fr_data[c]
                lang_data[c] = f"[TODO] {texte_aide}" if texte_aide != "[TODO] Texte manquant" else "[TODO] Texte manquant"
                
            for c in l_inutiles: 
                del lang_data[c]
                
            lang_data = dict(sorted(lang_data.items()))
            with open(lang_file, 'w', encoding='utf-8') as f:
                json.dump(lang_data, f, indent=2, ensure_ascii=False)
                
            log_msg.append(f"🌐 **{lang}.json** : {len(l_manquantes)} ajoutées, {len(l_inutiles)} supprimées.")
            
        # 5. Recharger les langues en mémoire vive
        charger_langues()
        
        embed = discord.Embed(title="🧹 Synchronisation i18n Terminée", description="\n".join(log_msg), color=discord.Color.green())
        embed.set_footer(text="Ouvre tes fichiers JSON et cherche '[TODO]' pour voir ce qu'il reste à traduire !")
        await ctx.send(embed=embed)

    # ==========================================
    # 📤 EXPORTATION POUR LES TRADUCTEURS (CSV)
    # ==========================================
    @commands.command(name="i18n_export", hidden=True)
    async def i18n_export(self, ctx, langue_cible: str = "en"):
        """[CACHÉE] !i18n_export [langue] : Génère un CSV pour les traducteurs communautaires."""
        if ctx.author.id != MON_ID_DISCORD: return
        
        import csv
        import re
        import io
        from utils import LOCALES_DIR
        
        fr_file = LOCALES_DIR / 'fr.json'
        cible_file = LOCALES_DIR / f'{langue_cible}.json'
        
        if not fr_file.exists() or not cible_file.exists():
            return await ctx.send(f"❌ Impossible de trouver `fr.json` ou `{langue_cible}.json`.")
            
        import json
        with open(fr_file, 'r', encoding='utf-8') as f:
            fr_data = json.load(f)
        with open(cible_file, 'r', encoding='utf-8') as f:
            cible_data = json.load(f)

        # Création du fichier CSV en mémoire
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        
        writer.writerow(['Clé (NE PAS TOUCHER)', 'Texte Français (RÉFÉRENCE)', f'Traduction ({langue_cible.upper()})', 'Variables obligatoires'])
        
        for cle, texte_fr in fr_data.items():
            variables = re.findall(r'\{[a-zA-Z0-9_]+\}', texte_fr)
            vars_str = ", ".join(variables) if variables else "Aucune"
            
            texte_cible = cible_data.get(cle, "[TODO] Texte manquant")
            
            writer.writerow([cle, texte_fr, texte_cible, vars_str])
            
        output.seek(0)
        file_bytes = io.BytesIO(output.getvalue().encode('utf-8-sig'))
        discord_file = discord.File(fp=file_bytes, filename=f"traduction_{langue_cible}.csv")
        
        embed = discord.Embed(
            title=f"📝 Fichier de traduction prêt ({langue_cible.upper()})", 
            description="Envoie ce fichier `.csv` à tes traducteurs.\nIls peuvent l'ouvrir avec **Excel** ou **Google Sheets**.\n\n⚠️ Dis-leur bien de :\n1. Ne pas modifier la colonne 1.\n2. Ne pas effacer les variables (colonne 4).",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed, file=discord_file)

async def setup(bot):
    await bot.add_cog(AdminCog(bot))