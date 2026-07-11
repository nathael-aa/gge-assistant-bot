# -*- coding: utf-8 -*-
import os
import json
import logging
import shutil
import aiohttp
from pathlib import Path
from datetime import datetime, timedelta
import discord
from discord import app_commands
from discord.ext import commands

from utils import (
    BOT_VERSION, 
    CONFIG_DIR,
    JOUEURS_DIR,
    PaginationView, 
    MON_ID_DISCORD, 
    get_file_lock, 
    setup_embed_footer,
    get_api_headers,
    get_server_config,
    t
)

logger = logging.getLogger("GGE_Bot")

CONTACTS_FILE = JOUEURS_DIR / 'contacts.json'
CHANGELOG_FILE = CONFIG_DIR / 'changelog.json'

# ==========================================
# 🎛️ CLASSE DU MENU INTERACTIF (BOUTONS)
# ==========================================
class MenuAideView(discord.ui.View):
    def __init__(self, embeds, langue="fr"):
        super().__init__(timeout=7200)
        self.embeds = embeds
        
        self.btn_home.label = t(langue, "aide_btn_sommaire", defaut="Sommaire")
        self.btn_1.label = t(langue, "aide_btn_aide", defaut="Aide")
        self.btn_2.label = t(langue, "aide_btn_events", defaut="Events")
        self.btn_3.label = t(langue, "aide_btn_fort", defaut="Fort.")
        self.btn_4.label = t(langue, "aide_btn_guerre", defaut="Guerre")
        self.btn_5.label = t(langue, "aide_btn_profils", defaut="Profils")
        self.btn_6.label = t(langue, "aide_btn_radar", defaut="Radar")

    async def update_menu(self, interaction: discord.Interaction, page: int):
        await interaction.response.edit_message(embed=self.embeds[page], view=self)

    @discord.ui.button(emoji="<:listitem:1512573892596858960>", style=discord.ButtonStyle.success, row=0)
    async def btn_home(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 0)

    @discord.ui.button(emoji="<:parameters:1512573735390154986>", style=discord.ButtonStyle.primary, row=0)
    async def btn_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 1)

    @discord.ui.button(emoji="<:events:1512574699555782666>", style=discord.ButtonStyle.primary, row=0)
    async def btn_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 2)

    @discord.ui.button(emoji="<:fortresses:1512574700839239892>", style=discord.ButtonStyle.primary, row=0)
    async def btn_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 3)

    @discord.ui.button(emoji="<:2_:1512574740915818527>", style=discord.ButtonStyle.primary, row=1)
    async def btn_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 4)

    @discord.ui.button(emoji="<:renames:1512574708913143858>", style=discord.ButtonStyle.primary, row=1)
    async def btn_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 5)

    @discord.ui.button(emoji="<:icon_analyze:1512573874150314005>", style=discord.ButtonStyle.primary, row=1)
    async def btn_6(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 6)

# ========================================================
# 🎛️ COMPOSANT UI : CHANGELOG SELECT MENU
# ========================================================
class ChangelogSelect(discord.ui.Select):
    def __init__(self, patches, cog):
        self.patches = patches
        self.cog = cog
        
        options = []
        for i, patch in enumerate(patches[:25]): 
            version = patch.get("version", "Unknown Version")
            date = patch.get("date", "Unknown Date")
            
            emoji = "✨" if i == 0 else "📜"
            
            options.append(discord.SelectOption(
                label=version[:100], 
                description=f"Deployed on {date}"[:100],
                value=str(i),
                emoji=emoji
            ))
        
        super().__init__(
            placeholder="Select a version to read the patch notes...", 
            min_values=1, 
            max_values=1, 
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        selected_index = int(self.values[0])
        selected_patch = self.patches[selected_index]
        
        embed = self.cog.build_changelog_embed(selected_patch)
        await interaction.response.edit_message(embed=embed, view=self.view)

class ChangelogView(discord.ui.View):
    def __init__(self, patches, cog):
        super().__init__(timeout=300)
        self.add_item(ChangelogSelect(patches, cog))

# ==========================================
# 🤖 MODULE PRINCIPAL (COMMANDES DU BOT)
# ==========================================
class AideCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        self.clr_sommaire    = discord.Color.from_rgb(255, 215, 0)  
        self.clr_aide        = discord.Color.from_rgb(244, 196, 48) 
        self.clr_events      = discord.Color.from_rgb(138, 43, 226) 
        self.clr_forteresses = discord.Color.from_rgb(46, 204, 113) 
        self.clr_guerre      = discord.Color.from_rgb(178, 34, 34)  
        self.clr_profils     = discord.Color.from_rgb(0, 139, 139)  
        self.clr_radar       = discord.Color.from_rgb(26, 43, 76)   

        self.clr_statut    = self.clr_aide
        self.clr_changelog = self.clr_aide
        self.clr_contact   = self.clr_aide

    # ==========================================
    # 📖 COMMANDE : HELP
    # ==========================================
    @app_commands.command(name="help", description="Displays the complete user manual for the GGE Assistant bot")
    async def aide_commande(self, interaction: discord.Interaction):
        try: await interaction.response.defer(ephemeral=False)
        except: return
        
        langue, _ = await get_server_config(interaction)

        embeds = []

        # PAGE 0 : LE SOMMAIRE
        desc0 = t(langue, "aide_p0_desc", version=BOT_VERSION, defaut=f"Bienvenue dans l'interface de navigation tactique.\n*Version du bot : {BOT_VERSION}*\n\n**Parcourez la documentation des modules via les boutons ci-dessous :**")
        embed0 = discord.Embed(title=t(langue, "aide_p0_title", defaut="📖 Manuel d'Utilisation - GGE Assistant"), description=desc0, color=self.clr_sommaire)
        embed0.add_field(name=t(langue, "aide_p0_f1_n", defaut="<:parameters:1512573735390154986> 1. Module Aide"), value=t(langue, "aide_p0_f1_v", defaut="Outils système, diagnostics, nouveautés et support."), inline=False)
        embed0.add_field(name=t(langue, "aide_p0_f2_n", defaut="<:events:1512574699555782666> 2. Module Événements"), value=t(langue, "aide_p0_f2_v", defaut="Liaison de comptes, scores en direct, objectifs, historique sur plusieurs sessions, classement unifié du serveur."), inline=False)
        embed0.add_field(name=t(langue, "aide_p0_f3_n", defaut="<:fortresses:1512574700839239892> 3. Module Forteresses"), value=t(langue, "aide_p0_f3_v", defaut="Scanners automatisés des forteresses prêtes sur la carte du monde."), inline=False)
        embed0.add_field(name=t(langue, "aide_p0_f4_n", defaut="<:2_:1512574740915818527> 4. Module Guerre"), value=t(langue, "aide_p0_f4_v", defaut="Analyse tactique joueur et alliance, comparatif entre joueur, ciblage légal et arbitrage RoE."), inline=False)
        embed0.add_field(name=t(langue, "aide_p0_f5_n", defaut="<:renames:1512574708913143858> 5. Module Profils"), value=t(langue, "aide_p0_f5_v", defaut="Profilages détaillés alliance et joueur, historique et colombe."), inline=False)
        embed0.add_field(name=t(langue, "aide_p0_f6_n", defaut="<:icon_analyze:1512573874150314005> 6. Module Radar Personnel"), value=t(langue, "aide_p0_f6_v", defaut="Système de surveillance furtif par alertes automatisées en MP."), inline=False)
        embeds.append(embed0)

        # 📑 PAGE 1 : COG AIDE
        embed1 = discord.Embed(title=t(langue, "aide_p1_title", defaut="<:parameters:1512573735390154986> 1. Module Aide"), color=self.clr_aide)
        embed1.description = t(langue, "aide_p1_f0", defaut="⚠️ **Avant de lancer une commande, vous devez utiliser : \n`/setup scope:👤 For me only (Personal) language: server: `\nSans cela, le bot ne fonctionnera pas** ⚠️")
        embed1.add_field(name="• `/help`", value=t(langue, "aide_p1_f1", defaut="> Affiche le manuel d'utilisation complet du bot."), inline=False)
        embed1.add_field(name="• `/status`", value=t(langue, "aide_p1_f2", defaut="> Vérifie l'état de santé du système (latence, espace disque, statut de l'API)."), inline=False)
        embed1.add_field(name="• `/changelog`", value=t(langue, "aide_p1_f3", defaut="> Découvre les notes de mise à jour et nouveautés."), inline=False)
        embed1.add_field(name="• `/contact [message]`", value=t(langue, "aide_p1_f4", defaut="> Permet aux joueurs d'envoyer un rapport ou une suggestion directement au développeur."), inline=False)
        embeds.append(embed1)

        # 📑 PAGE 2 : COG EVENTS
        embed2 = discord.Embed(title=t(langue, "aide_p2_title", defaut="<:events:1512574699555782666> 2. Module Événements"), color=self.clr_events)
        embed2.description = t(langue, "aide_p2_desc", defaut="Historique des résultats aux évents, informations serveur et classements.")
        embed2.add_field(name=t(langue, "aide_p2_g1", defaut="<:cartography:1512574691766964386> Configuration & Général"), value=t(langue, "aide_p2_v1", defaut=
            "• `/link_account [player]`\n> Lie un compte Discord à un pseudo de jeu GGE.\n\n"
            "• `/server`\n> Affiche les statistiques du serveur GGE et le Top 15 Alliances."), inline=False)
        embed2.add_field(name=t(langue, "aide_p2_g2", defaut="<:gt:1512574701807997119> Suivi des Scores"), value=t(langue, "aide_p2_v2", defaut=
            "• `/event player [event_name] [player] [mode: latest/total]`\n> Affiche le score live ou le cumul d'un joueur.\n\n"
            "• `/event alliance [event_name] [alliance_name] [display_mode]`\n> Génère le classement interne complet des membres.\n\n"), inline=False)
        embed2.add_field(name=t(langue, "aide_p2_g3", defaut="<:ranking:1512438311132729525> Groupe de commandes /leaderboard"), value=t(langue, "aide_p2_v3", defaut=
            "• `/leaderboard woa`\n> Affiche le top de la dernière Roue de la Fortune.\n\n"
            "• `/leaderboard storm_islands`\n> Affiche le top des meilleurs pilleurs d'Aquamarine."), inline=False)
        embed2.add_field(name=t(langue, "aide_p2_g4", defaut="<:woaicon:1512165794740572292> Groupe de commandes /woa"), value=t(langue, "aide_p2_v4", defaut=
            "• `/woa history [player]`\n> Consulte l'historique des tickets dépensés.\n\n"
            "• `/woa summary`\n> Affiche l'activité économique globale du serveur."), inline=False)
        embed2.add_field(name=t(langue, "aide_p2_g5", defaut="<:cible:1512573711134490775> Groupe de commandes /rival"), value=t(langue, "aide_p2_v5", defaut=
            "• `/rival start [event_name] [threshold]` / `/rival stop`\n> Active ou désactive le radar secret en MP.\n\n"
            "• `/rival add [players]` / `/rival list`\n> Gère tes cibles de rival en direct."), inline=False)
        embed2.add_field(name=t(langue, "aide_p2_g6", defaut="<:events:1512574699555782666> Groupe de commandes Calendar"), value=t(langue, "aide_p2_v6", defaut=
            "• `/calendar setup [channel]`\n> Permets de choisir le channel ou seront envoyés les informations d'événements.\n\n"
            "• `/calendar track [alliance_name]`\n> Permet de suivre une alliance sur les événements.\n\n"
            "• `/calendar untrack [alliance_name]`\n> Retire une alliance du suivi événement.\n\n"
            "• `/calendar current`\n> Renvoie les événements ayant lieu ce mois."), inline=False)
        embed2.add_field(name=t(langue, "aide_p2_g7", defaut="<:ranking:1512438311132729525> Groupe de commandes /rank"), value=t(langue, "aide_p2_v7", defaut=
            "• `/rank player` / `/rank alliance`\n> Classement global et statistiques (Joueur ou Alliance).\n\n"
            "• `/rank event [event_name]`\n> Classements des événements (Nomades, Corbeaux, Étrangers, Samouraïs).\n\n"
            "• `/rank gacha [event_name]`\n> Classements des évents spéciaux (Banquet, Lune Creuse, Flora...)\n\n"
            "• `/rank league [league_name]`\n> Classements des Ligues (Saison, Ligue du Royaume).\n\n"
            "• `/rank realms [realm_name]`\n> Classements des mondes (Royaumes Extérieurs, Horizon).\n\n"
            "• `/rank statistique [stat_name]`\n> Classements globaux (Honneur, Puissance, Pillage, Légendaire)."), inline=False)
        embeds.append(embed2)

        # 📑 PAGE 3 : COG FORTERESSES
        embed3 = discord.Embed(title=t(langue, "aide_p3_title", defaut="<:fortresses:1512574700839239892> 3. Module Forteresses"), color=self.clr_forteresses)
        embed3.add_field(name="• `/fortress scan [player] [ice] [sands] [peaks]`", value=t(langue, "aide_p3_f1", defaut="> Recherche automatique en cascade des forteresses prêtes."), inline=False)
        embed3.add_field(name="• `/fortress stop`", value=t(langue, "aide_p3_f2", defaut="> Interrompt la session de recherche active."), inline=False)
        embeds.append(embed3)

        # 📑 PAGE 4 : COG GUERRE
        embed4 = discord.Embed(title=t(langue, "aide_p4_title", defaut="<:2_:1512574740915818527> 4. Module Guerre"), color=self.clr_guerre)
        embed4.add_field(name=t(langue, "aide_p4_g1", defaut="<:icon_analyze:1512573874150314005> Scanners & Opérations"), value=t(langue, "aide_p4_v1", defaut=
            "• `/alliance_scanner [alliance_name]`\n> Roster complet en temps réel (Cibles / Ordre des colombes).\n\n"
            "• `/proximity [my_player] [enemy_alliance]`\n> Trouve les ennemis les plus proches de ton château.\n\n"
            "• `/target [attacker] [sort_by] (target_alliance)`\n> Génère une liste de cibles légales selon la charte.\n\n"
            "• `/hr [attacker] [defender]`\n> Vérifie la légalité d'une attaque selon les RoE."), inline=False)
        embed4.add_field(name=t(langue, "aide_p4_g2", defaut="<:icon_friends2:1512573878801797271> Comparaison"), value=t(langue, "aide_p4_v2", defaut=
            "• `/compare player [player1] [player2]`\n> Duel statistique complet en 7 rounds.\n\n"
            "• `/compare alliance [alliance_1] [alliance_2]`\n> Duel complet entre deux alliances."), inline=False)
        embed4.add_field(name=t(langue, "aide_p4_g3", defaut="<:4_:1512574743369224303> Diplomatie"), value=t(langue, "aide_p4_v3", defaut=
            "• `/diplomacy add [my_alliance] [target] [status]`\n> Enregistre un lien Allié, PNA ou Guerre.\n\n"
            "• `/diplomacy remove [my_alliance] [target]`\n> Supprime un accord diplomatique.\n\n"
            "• `/diplomacy list [alliance_name]`\n> Affiche secrètement le registre diplomatique."), inline=False)
        embeds.append(embed4)

        # 📑 PAGE 5 : COG PROFILS
        embed5 = discord.Embed(title=t(langue, "aide_p5_title", defaut="<:renames:1512574708913143858> 5. Module Profils"), color=self.clr_profils)
        embed5.add_field(name=t(langue, "aide_p5_g1", defaut="<:Le_Hraut_Lumbricus_2:1512573890298380388> Renseignement Tactique"), value=t(langue, "aide_p5_v1", defaut=
            "• `/player [name]`\n> Fiche complète d'un joueur (Niveau, PP, Positions, Châteaux).\n\n"
            "• `/alliance [name]`\n> Profil détaillé de l'alliance et roster complet paginé.\n\n"
            "• `/dove [player]`\n> Compte à rebours précis avant la fin de protection."), inline=False)
        embed5.add_field(name=t(langue, "aide_p5_g2", defaut="<:memberlistactivity:1512573911257190481> Renseignement Historique"), value=t(langue, "aide_p5_v2", defaut=
            "• `/history [type] [player]`\n> Traque les changements de pseudos, d'alliances ou châteaux.\n\n"
            "• `/alliance_might [alliance_name] [days]`\n> Évolution de la puissance globale d'une alliance."), inline=False)
        embeds.append(embed5)

        # 📑 PAGE 6 : COG RADAR
        embed6 = discord.Embed(title=t(langue, "aide_p6_title", defaut="<:icon_analyze:1512573874150314005> 6. Module Radar Personnel"), color=self.clr_radar)
        embed6.add_field(name=t(langue, "aide_p6_g1", defaut="<:info:1512502828193808537> Surveillance Individuelle"), value=t(langue, "aide_p6_v1", defaut=
            "• `/radar add [player] (reason)`\n> Place une cible sous surveillance étroite (Alertes MP).\n\n"
            "• `/radar remove [player]`\n> Retire un joueur de ta surveillance personnelle.\n\n"
            "• `/radar list`\n> Tableau de bord centralisé de tous tes suivis actifs."), inline=False)
        embed6.add_field(name=t(langue, "aide_p6_g2", defaut="<:info:1512502828193808537> Sous-groupe /radar alliance"), value=t(langue, "aide_p6_v2", defaut=
            "• `/radar alliance add [alliance_name] (reason)`\n> Alerte MP si Entrée, Sortie, Promotion ou Rétrogradation.\n\n"
            "• `/radar alliance remove [alliance_name]`\n> Coupe la surveillance globale sur l'alliance."), inline=False)
        embeds.append(embed6)

        view = MenuAideView(embeds, langue=langue)
        await interaction.followup.send(embed=embeds[0], view=view)

    # ==========================================
    # 📡 COMMANDE : STATUS
    # ==========================================
    @app_commands.command(name="status", description="Checks the overall health status of the system (Bot, NAS Storage, GGE-Tracker API)")
    async def statut_commande(self, interaction: discord.Interaction):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        langue, serveur = await get_server_config(interaction)

        logger.info(f"📡 [Statut] Demande de diagnostic par {interaction.user.name} sur {serveur}")
        embed = discord.Embed(title=t(langue, "statut_title", serveur=serveur, defaut=f"📡 Diagnostic et Santé du Système - {serveur}"), color=self.clr_statut, timestamp=discord.utils.utcnow())

        # 🤖 1. DIAGNOSTIC BOT DISCORD
        ping = round(self.bot.latency * 1000)
        
        val_oui = t(langue, "statut_yes", defaut="<:parameters:1512573735390154986> Oui")
        val_non = t(langue, "statut_no", defaut="<:icon_friends2:1512573878801797271> Non")
        maintenance_txt = val_oui if getattr(self.bot, 'maintenance_mode', False) else val_non
        
        bot_txt = t(langue, "statut_bot_desc", ping=ping, version=BOT_VERSION, maint=maintenance_txt, defaut=(
            f"**Statut** : 🟢 Opérationnel\n"
            f"**Latence (Ping)** : `{ping} ms`\n"
            f"**Version Core** : `{BOT_VERSION}`\n"
            f"**Mode Maintenance** : {maintenance_txt}"
        ))
        embed.add_field(name=t(langue, "statut_bot_title", defaut="🤖 Bot Discord"), value=bot_txt, inline=False)

        # 💾 2. DIAGNOSTIC STOCKAGE SERVEUR
        try:
            total, used, free = shutil.disk_usage("/app/data")
            total_gb = total / (1024**3)
            used_gb = used / (1024**3)
            free_gb = free / (1024**3)
            pourcentage_plein = (used / total) * 100
            
            # --- 🔍 Scan Serveur ---
            scans_dir = Path(f"/app/data/server_scans/{serveur}")
            last_scan_txt = t(langue, "statut_storage_no_file", defaut="<:error:1512505075220611172> Aucun fichier trouvé")
            
            if scans_dir.exists():
                server_files = list(scans_dir.rglob('*.json'))
                if server_files:
                    latest_scan = max(server_files, key=lambda p: p.stat().st_mtime)
                    mtime_ts = int(latest_scan.stat().st_mtime)
                    last_scan_txt = f"<t:{mtime_ts}:F> (<t:{mtime_ts}:R>)"

            # --- 🧱 Scan Murs ---
            murs_dir = Path(f"/app/data/murs_scans/{serveur}")
            last_mur_txt = t(langue, "statut_storage_no_file", defaut="<:error:1512505075220611172> Aucun fichier trouvé")
            
            if murs_dir.exists():
                mur_files = list(murs_dir.rglob('*.json'))
                if mur_files:
                    latest_mur = max(mur_files, key=lambda p: p.stat().st_mtime)
                    mtime_mur = int(latest_mur.stat().st_mtime)
                    last_mur_txt = f"<t:{mtime_mur}:F> (<t:{mtime_mur}:R>)"
            
            storage_txt = t(langue, "statut_storage_desc", total=f"{total_gb:.2f}", used=f"{used_gb:.2f}", pct=f"{pourcentage_plein:.1f}", free=f"{free_gb:.2f}", last=last_scan_txt, lastm=last_mur_txt, defaut=(
                f"**Disque Local** : 🟢 Connecté (Lecture/Écriture OK)\n"
                f"**Espace Utilisé** : `{used_gb:.2f} Go` ({pourcentage_plein:.1f}%)\n"
                f"**Dernier Scan Serveur** : {last_scan_txt}\n"
                f"**Dernier Scan Murs** : {last_mur_txt}"
            ))
        except Exception as e:
            storage_txt = t(langue, "statut_storage_err", error=str(e), defaut=f"<:error:1512505075220611172> Erreur de lecture de l'espace de stockage : {e}")
            
        embed.add_field(name=t(langue, "statut_storage_title", defaut="💾 Stockage Interne"), value=storage_txt, inline=False)

        # 📡 3. DIAGNOSTIC API LIVE (GGE-TRACKER)
        url_api = "https://api.gge-tracker.com/api/v1/"
        headers = await get_api_headers(custom_server=serveur)
        
        def to_discord_ts(iso_str):
            if not iso_str or iso_str == "null": return t(langue, "statut_api_never", defaut="Jamais")
            try:
                dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
                return f"<t:{int(dt.timestamp())}:R>"
            except:
                return t(langue, "statut_api_unknown", defaut="Date inconnue")

        session = self.bot.session if hasattr(self.bot, 'session') and self.bot.session else aiohttp.ClientSession()
        api_title = t(langue, "statut_api_title", defaut="📡 API GGE-Tracker (Live)")
        
        try:
            async with session.get(url_api, headers=headers, timeout=10) as r:
                if r.status == 200:
                    api_data = await r.json()
                    v_api = api_data.get("version", "Inconnue")
                    
                    val_prog = t(langue, "statut_api_prog", defaut="<:error:1512505075220611172> En cours...")
                    val_done = t(langue, "statut_api_done", defaut="🟢 Terminé / À jour")
                    in_progress = val_prog if api_data.get("update_in_progress", False) else val_done
                    
                    updates = api_data.get("last_update", {})
                    
                    api_txt = t(langue, "statut_api_desc", v_api=v_api, in_progress=in_progress, defaut=(
                        f"**Statut API** : 🟢 En ligne (Code 200)\n"
                        f"**Version Distante** : `{v_api}`\n"
                        f"**Synchro Serveur GGE** : {in_progress}\n"
                    ))
                    embed.add_field(name=api_title, value=api_txt, inline=False)
                    
                    events_txt = t(langue, "statut_api_events", 
                        m=to_discord_ts(updates.get('might')), l=to_discord_ts(updates.get('loot')),
                        n=to_discord_ts(updates.get('nomad')), s=to_discord_ts(updates.get('samurai')),
                        c=to_discord_ts(updates.get('bloodcrow')), e=to_discord_ts(updates.get('war_realms')),
                        b=to_discord_ts(updates.get('berimond_kingdom')),
                        defaut=(
                        f"<:pp1:1512438903821570160> **Puissances** : {to_discord_ts(updates.get('might'))}\n"
                        f"<:loot:1512439015570276553> **Pillages** : {to_discord_ts(updates.get('loot'))}\n"
                        f"<:nomads:1512431070719774750> **Nomades** : {to_discord_ts(updates.get('nomad'))}\n"
                        f"<:samurai:1512430844935929868> **Samouraïs** : {to_discord_ts(updates.get('samurai'))}\n"
                        f"<:bloodcrow:1512430942990368928> **Corbeaux** : {to_discord_ts(updates.get('bloodcrow'))}\n"
                        f"<:war_realms:1512573773658980504> **Etrangers** : {to_discord_ts(updates.get('war_realms'))}\n"
                        f"<:berimond:1512430901756428390> **Bérimond** : {to_discord_ts(updates.get('berimond_kingdom'))}"
                    ))
                    embed.add_field(name=t(langue, "statut_api_events_title", defaut="⏱️ Dernière capture des données GGE-Tracker"), value=events_txt, inline=False)
                else:
                    embed.add_field(name=api_title, value=t(langue, "statut_api_err_instable", code=r.status, defaut=f"<:error:1512505075220611172> L'API répond mais rencontre une instabilité (Code : {r.status})."), inline=False)
        except Exception as e:
            embed.add_field(name=api_title, value=t(langue, "statut_api_err_offline", error=str(e), defaut=f"<:error:1512505075220611172> **Hors ligne** : Impossible de joindre l'API de suivi du jeu ({e})."), inline=False)

        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed)

    # ==========================================
    # 🛠️ HELPER : GENERATE CHANGELOG EMBED
    # ==========================================
    def build_changelog_embed(self, patch: dict) -> discord.Embed:
        version = patch.get('version', 'Unknown Version')
        date = patch.get('date', 'Unknown Date')
        content = patch.get('content', [])

        embed = discord.Embed(
            title=f"🚀 Update Notes : {version}",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow()
        )
        
        lignes_propres = []
        raw_lines = content if isinstance(content, list) else content.split('\n')
        
        for line in raw_lines:
            line = line.strip()
            if not line: continue
            
            if not any(line.startswith(c) for c in ['🔹', '•', '-', '*', '🚀', '🛠️', '⚙️', '🟢', '❌', '⚠️', '📈', '✨', '🪙']):
                lignes_propres.append(f"🔹 {line}")
            else:
                lignes_propres.append(line)

        description = "\n\n".join(lignes_propres) if lignes_propres else "*No details provided for this update.*"
        
        embed.description = description
        embed.set_footer(text=f"Patch deployed on {date}")
        
        return embed

    # ==========================================
    # 🚀 COMMANDE : CHANGELOG
    # ==========================================
    @app_commands.command(name="changelog", description="Discover the latest news, fixes and improvements applied to the bot")
    async def changelog_commande(self, interaction: discord.Interaction):
        try: await interaction.response.defer(thinking=True)
        except: return

        logger.info(f"🚀 [Changelog] Consultation par {interaction.user.name}")

        # 1. Vérification du fichier
        if not os.path.exists(CHANGELOG_FILE):
            embed = discord.Embed(
                title="🚀 Update Notes (Changelog)", 
                description="📭 No update notes are available at the moment.",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            return await interaction.followup.send(embed=embed)

        # 2. Lecture sécurisée des données
        try:
            with open(CHANGELOG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            patches = data.get("patches", [])
            
            if not patches:
                embed = discord.Embed(
                    title="🚀 Update Notes (Changelog)", 
                    description="📭 The patch history is empty.",
                    color=discord.Color.orange(),
                    timestamp=discord.utils.utcnow()
                )
                return await interaction.followup.send(embed=embed)

            # 3. Création de la vue et de l'embed par défaut (le plus récent)
            view = ChangelogView(patches, self)
            embed_initial = self.build_changelog_embed(patches[0])

            await interaction.followup.send(embed=embed_initial, view=view)

        except Exception as e:
            logger.error(f"❌ Erreur lecture changelog.json : {e}")
            embed_err = discord.Embed(
                title="🚀 Update Notes (Changelog)",
                description="<:error:1512505075220611172> An internal error occurred while reading the patch notes file.",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            await interaction.followup.send(embed=embed_err)

    # ==========================================
    # 📩 COMMANDE : CONTACT
    # ==========================================
    @app_commands.command(name="contact", description="Send a problem, bug, or suggestion directly to the developer")
    @app_commands.describe(message="Write your problem or suggestion in detail here")
    async def contact_commande(self, interaction: discord.Interaction, message: str):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return
        
        langue, _ = await get_server_config(interaction)

        logger.info(f"📩 [Contact] Nouveau message de {interaction.user.name} ({interaction.user.id})")
        maintenant_iso = discord.utils.utcnow().isoformat().replace('+00:00', 'Z')
        
        async with get_file_lock(CONTACTS_FILE):
            try:
                tickets_existants = []
                if os.path.exists(CONTACTS_FILE):
                    with open(CONTACTS_FILE, 'r', encoding='utf-8') as f:
                        try: tickets_existants = json.load(f)
                        except json.JSONDecodeError: tickets_existants = []

                nouveau_ticket = {
                    "date": maintenant_iso,
                    "user_id": str(interaction.user.id),
                    "user_name": interaction.user.name,
                    "serveur": interaction.guild.name if interaction.guild else "Message Privé",
                    "message": message
                }
                tickets_existants.append(nouveau_ticket)

                with open(CONTACTS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(tickets_existants, f, indent=4, ensure_ascii=False)
                    
            except Exception as e:
                logger.error(f"<:error:1512505075220611172> [Contact] Erreur écriture JSON : {e}")
                err_msg = t(langue, "contact_err_write", defaut="<:error:1512505075220611172> Une erreur technique interne a empêché l'enregistrement de ton message.")
                return await interaction.followup.send(err_msg)

        try:
            developpeur = self.bot.get_user(MON_ID_DISCORD)
            if not developpeur:
                developpeur = await self.bot.fetch_user(MON_ID_DISCORD)

            embed_mp = discord.Embed(title="📩 Nouveau Ticket Reçu !", color=self.clr_contact, timestamp=discord.utils.utcnow())
            embed_mp.add_field(name="<:players:1512504277392953426> Expéditeur", value=f"**{interaction.user.name}** (`{interaction.user.id}`)", inline=True)
            embed_mp.add_field(name="<:castles:1512574693859786822> Provenance", value=f"*{nouveau_ticket['serveur']}*", inline=True)
            embed_mp.add_field(name="<:memberlist:1512572899360378971> Message", value=f"```text\n{message}\n```", inline=False)
            await setup_embed_footer(embed_mp, interaction, "fr")

            await developpeur.send(embed=embed_mp)
            
        except discord.Forbidden:
            logger.warning(f"<:error:1512505075220611172> [Contact] Impossible d'envoyer le MP à {MON_ID_DISCORD} (DMs fermés).")
        except Exception as e:
            logger.error(f"<:error:1512505075220611172> [Contact] Erreur lors de l'alerte MP : {e}")

        succ_msg = t(langue, "contact_success", defaut="<:players:1512504277392953426> **Merci !** Ton message a bien été enregistré et transmis au développeur.")
        await interaction.followup.send(succ_msg)

async def setup(bot: commands.Bot):
    await bot.add_cog(AideCog(bot))