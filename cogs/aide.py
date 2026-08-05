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
        self.btn_aide.label = t(langue, "aide_btn_aide", defaut="Aide")
        self.btn_events.label = t(langue, "aide_btn_events", defaut="Events")
        self.btn_rank.label = t(langue, "aide_btn_rank", defaut="Ranks")
        self.btn_profils.label = t(langue, "aide_btn_profils", defaut="Profils")
        self.btn_guerre.label = t(langue, "aide_btn_guerre", defaut="PvP")
        self.btn_radar.label = t(langue, "aide_btn_radar", defaut="Radar")
        self.btn_fort.label = t(langue, "aide_btn_fort", defaut="Forteresses")

    async def update_menu(self, interaction: discord.Interaction, page: int):
        await interaction.response.edit_message(embed=self.embeds[page], view=self)

    # --- LIGNE 1 (row=0) ---
    @discord.ui.button(emoji="🏰", style=discord.ButtonStyle.success, row=0)
    async def btn_home(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 0) # Page 0: Sommaire

    @discord.ui.button(emoji="⚙️", style=discord.ButtonStyle.primary, row=0)
    async def btn_aide(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 1) # Page 1: Aide

    @discord.ui.button(emoji="📅", style=discord.ButtonStyle.primary, row=0)
    async def btn_events(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 2) # Page 2: Events

    @discord.ui.button(emoji="🏆", style=discord.ButtonStyle.primary, row=0)
    async def btn_rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 7) # Page 7: Rank

    # --- LIGNE 2 (row=1) ---
    @discord.ui.button(emoji="👥", style=discord.ButtonStyle.primary, row=1)
    async def btn_profils(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 5) # Page 5: Profils

    @discord.ui.button(emoji="⚔️", style=discord.ButtonStyle.primary, row=1)
    async def btn_guerre(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 4) # Page 4: Guerre

    @discord.ui.button(emoji="📡", style=discord.ButtonStyle.primary, row=1)
    async def btn_radar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 6) # Page 6: Radar

    @discord.ui.button(emoji="🏯", style=discord.ButtonStyle.primary, row=1)
    async def btn_fort(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 3) # Page 3: Forteresses


# ==========================================
# 🤖 MODULE PRINCIPAL (COMMANDES DU BOT)
# ==========================================
class AideCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        self.clr_sommaire    = discord.Color.from_rgb(255,220,115) #Gold
        self.clr_aide        = discord.Color.from_rgb(254,255,184) #Yellow
        self.clr_events      = discord.Color.from_rgb(141,144,226) #Purple
        self.clr_forteresses = discord.Color.from_rgb(255,237,193) #Orange
        self.clr_guerre      = discord.Color.from_rgb(255,202,202) #Red
        self.clr_profils     = discord.Color.from_rgb(200,229,255) #Blue
        self.clr_radar       = discord.Color.from_rgb(243,198,242) #Pink
        self.clr_rank        = discord.Color.from_rgb(196,255,203) #Green
        self.clr_statut      = discord.Color.from_rgb(255,207,64) #Gold1
        self.clr_news   = discord.Color.from_rgb(255,191,0) #Gold2
        self.clr_vote   = discord.Color.from_rgb(232,198,112) #Gold3
        self.clr_support   = discord.Color.from_rgb(241,213,143) #Gold4
        self.clr_contact     = discord.Color.from_rgb(247,226,173) #Gold5

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
        # 1. Aide (Clé f1)
        embed0.add_field(name=t(langue, "aide_p0_f1_n", defaut="<:parameters:1512573735390154986> 1. Module Aide"), value=t(langue, "aide_p0_f1_v", defaut="Outils système, diagnostics, nouveautés et support."), inline=False)
        # 2. Events (Clé f2)
        embed0.add_field(name=t(langue, "aide_p0_f2_n", defaut="<:events4:1532431480398286878> 2. Module Événements"), value=t(langue, "aide_p0_f2_v", defaut="Liaison de comptes, scores en direct, objectifs, calendrier."), inline=False)
        # 3. Ranks (Clé f7)
        embed0.add_field(name=t(langue, "aide_p0_f7_n", defaut="<:ranking:1512438311132729525> 3. Module Classements"), value=t(langue, "aide_p0_f7_v", defaut="Tableaux de scores en direct pour tous les événements du jeu."), inline=False)
        # 4. Profils (Clé f5)
        embed0.add_field(name=t(langue, "aide_p0_f5_n", defaut="<:listitem:1512573892596858960> 4. Module Profils"), value=t(langue, "aide_p0_f5_v", defaut="Profilages détaillés alliance et joueur, historique et colombe."), inline=False)
        # 5. PvP (Clé f4)
        embed0.add_field(name=t(langue, "aide_p0_f4_n", defaut="<:2_:1512574740915818527> 5. Module PvP"), value=t(langue, "aide_p0_f4_v", defaut="Analyse tactique joueur et alliance, comparatifs, arbitrage RoE."), inline=False)
        # 6. Radar (Clé f6)
        embed0.add_field(name=t(langue, "aide_p0_f6_n", defaut="<:icon_analyze:1512573874150314005> 6. Module Radar Personnel"), value=t(langue, "aide_p0_f6_v", defaut="Système de surveillance furtif par alertes automatisées en MP."), inline=False)
        # 7. Forteresses (Clé f3)
        embed0.add_field(name=t(langue, "aide_p0_f3_n", defaut="<:fortresses:1512574700839239892> 7. Module Forteresses"), value=t(langue, "aide_p0_f3_v", defaut="Scanners automatisés des forteresses prêtes sur la carte du monde."), inline=False)
        embeds.append(embed0)

        # 📑 PAGE 1 : COG AIDE
        embed1 = discord.Embed(title=t(langue, "aide_p1_title", defaut="<:parameters:1512573735390154986> 1. Module Aide"), color=self.clr_aide)
        embed1.description = t(langue, "aide_p1_f0", defaut="⚠️ **Avant de lancer une commande, vous devez utiliser : \n`/setup scope:👤 For me only (Personal) language: server: `\nSans cela, le bot ne fonctionnera pas** ⚠️")
        embed1.add_field(name="• `/help`", value=t(langue, "aide_p1_f1", defaut="> Affiche le manuel d'utilisation complet du bot."), inline=False)
        embed1.add_field(name="• `/status`", value=t(langue, "aide_p1_f2", defaut="> Vérifie l'état de santé du système (latence, espace disque, statut de l'API)."), inline=False)
        embed1.add_field(name="• `/support` | `/vote` | `/news`", value=t(langue, "aide_p1_f3", defaut="> Liens utiles pour rejoindre la communauté, voter pour le bot ou lire les mises à jour."), inline=False)
        embed1.add_field(name="• `/contact [message]`", value=t(langue, "aide_p1_f4", defaut="> Permet aux joueurs d'envoyer un rapport ou une suggestion directement au développeur."), inline=False)
        embeds.append(embed1)

        # 📑 PAGE 2 : COG EVENTS
        embed2 = discord.Embed(title=t(langue, "aide_p2_title", defaut="<:events4:1532431480398286878> 2. Module Événements"), color=self.clr_events)
        embed2.description = t(langue, "aide_p2_desc", defaut="Historique des résultats aux évents, informations serveur et classements.")
        embed2.add_field(name=t(langue, "aide_p2_g1", defaut="<:cartography:1512574691766964386> Configuration & Général"), value=t(langue, "aide_p2_v1", defaut=
            "• `/link_account [player]`\n> Lie un compte Discord à un pseudo de jeu GGE.\n\n"
            "• `/server`\n> Affiche les statistiques du serveur GGE et le Top 15 Alliances."), inline=False)
        embed2.add_field(name=t(langue, "aide_p2_g2", defaut="<:grandtournament:1514704234128343040> Suivi des Scores"), value=t(langue, "aide_p2_v2", defaut=
            "• `/event player [event_name] [player] [mode: latest/total]`\n> Affiche le score live ou le cumul d'un joueur.\n\n"
            "• `/event alliance [event_name] [alliance_name] [display_mode]`\n> Génère le classement interne complet des membres.\n\n"), inline=False)
        embed2.add_field(name=t(langue, "aide_p2_g3", defaut="<:ranking:1512438311132729525> Groupe de commandes /leaderboard"), value=t(langue, "aide_p2_v3", defaut=
            "• `/leaderboard woa`\n> Affiche le top de la dernière Roue de la Fortune.\n\n"
            "• `/leaderboard storm_islands`\n> Affiche le top des meilleurs pilleurs d'Aquamarine."), inline=False)
        embed2.add_field(name=t(langue, "aide_p2_g4", defaut="<:woaicon:1512165794740572292> Groupe de commandes /woa"), value=t(langue, "aide_p2_v4", defaut=
            "• `/woa history [player]`\n> Consulte l'historique des tickets dépensés.\n\n"
            "• `/woa summary`\n> Affiche l'activité économique globale du serveur."), inline=False)
        embed2.add_field(name=t(langue, "aide_p2_g5", defaut="\<:attaque:1512570903886692474> Groupe de commandes /rival"), value=t(langue, "aide_p2_v5", defaut=
            "• `/rival start [event_name] [threshold]` / `/rival stop`\n> Active ou désactive le radar secret en MP.\n\n"
            "• `/rival add [players]` / `/rival list`\n> Gère tes cibles de rival en direct."), inline=False)
        embed2.add_field(name=t(langue, "aide_p2_g6", defaut="<:events4:1532431480398286878> Groupe de commandes Calendar"), value=t(langue, "aide_p2_v6", defaut=
            "• `/calendar setup [channel]`\n> Permets de choisir le channel ou seront envoyés les informations d'événements.\n\n"
            "• `/calendar track [alliance_name]`\n> Permet de suivre une alliance sur les événements.\n\n"
            "• `/calendar untrack [alliance_name]`\n> Retire une alliance du suivi événement.\n\n"
            "• `/calendar current`\n> Renvoie les événements ayant lieu ce mois."), inline=False)
        embeds.append(embed2)

        # 📑 PAGE 3 : COG FORTERESSES
        embed3 = discord.Embed(title=t(langue, "aide_p3_title", defaut="<:fortresses:1512574700839239892> 3. Module Forteresses"), color=self.clr_forteresses)
        embed3.add_field(name="• `/fortress scan [player] [ice] [sands] [peaks]`", value=t(langue, "aide_p3_f1", defaut="> Recherche automatique en cascade des forteresses prêtes."), inline=False)
        embed3.add_field(name="• `/fortress stop`", value=t(langue, "aide_p3_f2", defaut="> Interrompt la session de recherche active."), inline=False)
        embeds.append(embed3)

        # 📑 PAGE 4 : COG GUERRE
        embed4 = discord.Embed(title=t(langue, "aide_p4_title", defaut="<:2_:1512574740915818527> 4. Module PVP"), color=self.clr_guerre)
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
        embed5 = discord.Embed(title=t(langue, "aide_p5_title", defaut="<:listitem:1512573892596858960> 5. Module Profils"), color=self.clr_profils)
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
        embed6.add_field(name=t(langue, "aide_p6_g1", defaut="<:Information:1533430015264555099> Surveillance Individuelle"), value=t(langue, "aide_p6_v1", defaut=
            "• `/radar add [player] (reason)`\n> Place une cible sous surveillance étroite (Alertes MP).\n\n"
            "• `/radar remove [player]`\n> Retire un joueur de ta surveillance personnelle.\n\n"
            "• `/radar list`\n> Tableau de bord centralisé de tous tes suivis actifs."), inline=False)
        embed6.add_field(name=t(langue, "aide_p6_g2", defaut="<:Information:1533430015264555099> Sous-groupe /radar alliance"), value=t(langue, "aide_p6_v2", defaut=
            "• `/radar alliance add [alliance_name] (reason)`\n> Alerte MP si Entrée, Sortie, Promotion ou Rétrogradation.\n\n"
            "• `/radar alliance remove [alliance_name]`\n> Coupe la surveillance globale sur l'alliance."), inline=False)
        embeds.append(embed6)

        # 📑 PAGE 7 : COG RANK
        embed7 = discord.Embed(title=t(langue, "aide_p7_title", defaut="<:ranking:1512438311132729525> 7. Module Classements"), color=self.clr_rank)
        embed7.description = t(langue, "aide_p7_desc", defaut="Affichez les classements mondiaux en direct pour tous les événements du jeu.")
        embed7.add_field(name=t(langue, "aide_p7_g1", defaut="<:icon_points:1512502439339888820> Événements & Ligues"), value=t(langue, "aide_p7_v1", defaut=
            "• `/rank event [event_name]`\n> Invasions (Nomades, Samouraïs, Corbeaux, Étrangers) & Bérimond.\n\n"
            "• `/rank league [league_name]`\n> Saison des festivals, Ligue du Royaume.\n\n"
            "• `/rank contests [contest_name]`\n> Métamorphes, Noblesse, Guerre des Alliances, Patronage."
        ), inline=False)
        embed7.add_field(name=t(langue, "aide_p7_g2", defaut="<:woaicon:1512165794740572292> Statistiques & Gacha"), value=t(langue, "aide_p7_v2", defaut=
            "• `/rank statistics [stat_name]`\n> Honneur, Puissance, Pillage, Construction, Niv Légendaire.\n\n"
            "• `/rank gacha [event_name]`\n> Flora, Boule à Neige, Lune Creuse, Sables, Banquet, Minuit."
        ), inline=False)
        embed7.add_field(name=t(langue, "aide_p7_g3", defaut="<:outerrealmsicon:1512573734404231329> Alliances & Inter-Serveurs"), value=t(langue, "aide_p7_v3", defaut=
            "• `/rank realms [realm_name]`\n> Royaumes Extérieurs et Au-delà de l'Horizon.\n\n"
            "• `/rank alliance [catégorie]`\n> Top des Alliances par événement et statut de groupe."
        ), inline=False)
        embeds.append(embed7)

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
            
            storage_txt = t(langue, "statut_storage_desc", total=f"{total_gb:.2f}", used=f"{used_gb:.2f}", pct=f"{pourcentage_plein:.1f}", free=f"{free_gb:.2f}", last=last_scan_txt, defaut=(
                f"**Disque Local** : 🟢 Connecté (Lecture/Écriture OK)\n"
                f"**Espace Utilisé** : `{used_gb:.2f} Go` ({pourcentage_plein:.1f}%)\n"
                f"**Dernier Scan Serveur** : {last_scan_txt}\n"
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

    # ========================================================
    # 🆘 COMMANDE : SUPPORT
    # ========================================================
    @app_commands.command(name="support", description="Get the invite link to the official support server")
    async def support(self, interaction: discord.Interaction):
        langue, _ = await get_server_config(interaction)

        embed = discord.Embed(
            title=t(langue, "cmd_support_title", defaut="<:Information:1533430015264555099> Support & Communauté"),
            description=t(langue, "cmd_support_desc", defaut="Vous avez une question, une suggestion ou vous avez trouvé un bug ? Rejoignez le serveur Discord officiel de **GGE Assistant** !"),
            color=self.clr_support
        )

        view = discord.ui.View()
        # 🔗 Remplace par ton vrai lien d'invitation Discord :
        invite_url = "https://discord.gg/zrrhxp6wDj"
        btn = discord.ui.Button(label=t(langue, "cmd_support_btn", defaut="Rejoindre le serveur"), url=invite_url, emoji="💬")
        view.add_item(btn)

        await interaction.response.send_message(embed=embed, view=view)

    # ========================================================
    # ⭐ COMMANDE : VOTE
    # ========================================================
    @app_commands.command(name="vote", description="Support the bot by voting on Top.gg")
    async def vote(self, interaction: discord.Interaction):
        langue, _ = await get_server_config(interaction)

        embed = discord.Embed(
            title=t(langue, "cmd_vote_title", defaut="⭐ Soutenir GGE Assistant"),
            description=t(langue, "cmd_vote_desc", defaut="Le bot est 100% gratuit ! Le meilleur moyen de soutenir le projet et de l'aider à grandir est de voter sur Top.gg (possible toutes les 12 heures). Merci ! ❤️"),
            color=self.clr_vote
        )

        view = discord.ui.View()
        # 🔗 Remplace par l'ID de ton bot pour le lien de vote :
        bot_id = "1472309793065533493"
        vote_url = f"https://top.gg/bot/{bot_id}/vote"
        btn = discord.ui.Button(label=t(langue, "cmd_vote_btn", defaut="Voter sur Top.gg"), url=vote_url, emoji="⭐")
        view.add_item(btn)

        await interaction.response.send_message(embed=embed, view=view)

    # ========================================================
    # 📰 COMMANDE : NEWS
    # ========================================================
    @app_commands.command(name="news", description="Read the latest bot updates and patch notes")
    async def news(self, interaction: discord.Interaction):
        langue, _ = await get_server_config(interaction)

        embed = discord.Embed(
            title=t(langue, "cmd_news_title", defaut="📰 Dernières Nouveautés"),
            description=t(langue, "cmd_news_desc", defaut="Découvrez les dernières annonces, les serveurs ajoutés et les nouvelles fonctionnalités du bot directement sur notre page Top.gg !"),
            color=self.clr_news
        )

        view = discord.ui.View()
        # 🔗 Remplace par l'ID de ton bot pour le lien de la page :
        bot_id = "1472309793065533493"
        news_url = f"https://top.gg/bot/{bot_id}/announcements" # Le #articles descend la page directement au bon endroit
        btn = discord.ui.Button(label=t(langue, "cmd_news_btn", defaut="Voir les annonces"), url=news_url, emoji="🗞️")
        view.add_item(btn)

        await interaction.response.send_message(embed=embed, view=view)

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
                logger.error(f"❌ [Contact] Erreur écriture JSON : {e}")
                err_msg = t(langue, "contact_err_write", defaut="❌ Une erreur technique interne a empêché l'enregistrement de ton message.")
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
            logger.warning(f"⚠️ [Contact] Impossible d'envoyer le MP à {MON_ID_DISCORD} (DMs fermés).")
        except Exception as e:
            logger.error(f"❌ [Contact] Erreur lors de l'alerte MP : {e}")

        succ_msg = t(langue, "contact_success", defaut="<:players:1512504277392953426> **Merci !** Ton message a bien été enregistré et transmis au développeur.")
        await interaction.followup.send(succ_msg)

async def setup(bot: commands.Bot):
    await bot.add_cog(AideCog(bot))