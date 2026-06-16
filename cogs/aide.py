# -*- coding: utf-8 -*-
import os
import json
import logging
import shutil
import aiohttp
from datetime import datetime, timedelta
import discord
from discord import app_commands
from discord.ext import commands

# 🛠️ On importe nos outils depuis utils.py (Nettoyage des doublons et ajout du verrou)
from utils import BOT_VERSION, BASE_DATA_PATH, PaginationView, MON_ID_DISCORD, get_file_lock, setup_embed_footer

logger = logging.getLogger("GGE_Bot")
CONTACTS_FILE = BASE_DATA_PATH / 'contacts.json'
CHANGELOG_FILE = BASE_DATA_PATH / 'changelog.json'

# ==========================================
# 🎛️ CLASSE DU MENU INTERACTIF (BOUTONS)
# ==========================================
class MenuAideView(discord.ui.View):
    def __init__(self, embeds):
        super().__init__(timeout=300)
        self.embeds = embeds

    async def update_menu(self, interaction: discord.Interaction, page: int):
        await interaction.response.edit_message(embed=self.embeds[page], view=self)

    # Ligne 1 : Sommaire + Cogs 1, 2, 3
    @discord.ui.button(label="Sommaire", emoji="<:listitem:1512573892596858960>", style=discord.ButtonStyle.success, row=0)
    async def btn_home(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 0)

    @discord.ui.button(label="Aide", emoji="<:parameters:1512573735390154986>", style=discord.ButtonStyle.primary, row=0)
    async def btn_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 1)

    @discord.ui.button(label="Events", emoji="<:events:1512574699555782666>", style=discord.ButtonStyle.primary, row=0)
    async def btn_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 2)

    @discord.ui.button(label="Fort.", emoji="<:fortresses:1512574700839239892>", style=discord.ButtonStyle.primary, row=0)
    async def btn_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 3)

    # Ligne 2 : Cogs 4, 5, 6
    @discord.ui.button(label="Guerre", emoji="<:2_:1512574740915818527>", style=discord.ButtonStyle.primary, row=1)
    async def btn_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 4)

    @discord.ui.button(label="Profils", emoji="<:renames:1512574708913143858>", style=discord.ButtonStyle.primary, row=1)
    async def btn_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 5)

    @discord.ui.button(label="Radar", emoji="<:icon_analyze:1512573874150314005>", style=discord.ButtonStyle.primary, row=1)
    async def btn_6(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_menu(interaction, 6)


# ==========================================
# 🤖 MODULE PRINCIPAL (COMMANDES DU BOT)
# ==========================================
class AideCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        # 🎨 CHARTE GRAPHIQUE OFFICIELLE DES COGS
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
    # 📖 COMMANDE : AIDE
    # ==========================================
    @app_commands.command(name="aide", description="Affiche le manuel d'utilisation complet du bot GGE Assistant")
    async def aide_commande(self, interaction: discord.Interaction):
        try: await interaction.response.defer(ephemeral=False)
        except: return

        embeds = []

        # PAGE 0 : LE SOMMAIRE
        embed0 = discord.Embed(
            title="📖 Manuel d'Utilisation - GGE Assistant",
            description=f"Bienvenue dans l'interface de navigation tactique.\n*Version du bot : {BOT_VERSION}*\n\n**Parcourez la documentation des modules via les boutons ci-dessous :**",
            color=self.clr_sommaire
        )
        embed0.add_field(name="<:parameters:1512573735390154986> 1. Module Aide", value="Outils système, diagnostics, nouveautés et support.", inline=False)
        embed0.add_field(name="<:events:1512574699555782666> 2. Module Événements", value="Liaison de comptes, scores en direct, objectifs, historique sur plusieurs sessions, classement unifié du serveur.", inline=False)
        embed0.add_field(name="<:fortresses:1512574700839239892> 3. Module Forteresses", value="Scanners automatisés des forteresses prêtes sur la carte du monde.", inline=False)
        embed0.add_field(name="<:2_:1512574740915818527> 4. Module Guerre", value="Analyse tactique joueur et alliance, comparatif entre joueur, ciblage légal et arbitrage RoE.", inline=False)
        embed0.add_field(name="<:renames:1512574708913143858> 5. Module Profils", value="Profilages détaillés alliance et joueur, historique et colombe.", inline=False)
        embed0.add_field(name="<:icon_analyze:1512573874150314005> 6. Module Radar Personnel", value="Système de surveillance furtif par alertes automatisées en MP.", inline=False)
        embeds.append(embed0)

        # 📑 PAGE 1 : COG AIDE
        embed1 = discord.Embed(title="<:parameters:1512573735390154986> 1. Module Aide", color=self.clr_aide)
        embed1.add_field(name="• `/aide`", value="> Affiche le manuel d'utilisation complet du bot.", inline=False)
        embed1.add_field(name="• `/statut`", value="> Vérifie l'état de santé du système (latence, espace disque, statut de l'API).", inline=False)
        embed1.add_field(name="• `/changelog`", value="> Découvre les notes de mise à jour et nouveautés.", inline=False)
        embed1.add_field(name="• `/contact [message]`", value="> Permet aux joueurs d'envoyer un rapport ou une suggestion directement au développeur.", inline=False)
        embeds.append(embed1)

        # 📑 PAGE 2 : COG EVENTS
        embed2 = discord.Embed(title="<:events:1512574699555782666> 2. Module Événements", color=self.clr_events)
        embed2.description = "Historique des résultats aux évents, informations serveur et classements."
        embed2.add_field(name="<:cartography:1512574691766964386> Configuration & Général", value=
            "• `/set_pseudo [pseudo]`\n> Lie un compte Discord à un pseudo de jeu GGE.\n\n"
            "• `/serveur`\n> Affiche les statistiques du serveur E4K_FR1 et le Top 15 Alliances.", inline=False)
        embed2.add_field(name="<:gt:1512574701807997119> Suivi des Scores", value=
            "• `/event_joueur [nom_event] [joueur] [mode: dernier/cumul]`\n> Affiche le score live ou le cumul d'un joueur.\n\n"
            "• `/event_alliance [nom_event] [alliance] [affichage]`\n> Génère le classement interne complet des membres.\n\n"
            "• `/event_bilan [nom_event] [alliance] [lissage]`\n> Analyse les objectifs d'alliance.\n\n"
            "• `/event_objectif_set [nom_event]...`\n> *(Admin)* Configure les quotas d'alliance.", inline=False)
        embed2.add_field(name="<:ranking:1512438311132729525> Groupe de commandes /classement", value=
            "• `/classement woa`\n> Affiche le top de la dernière Roue de la Fortune.\n\n"
            "• `/classement iles_orageuses`\n> Affiche le top des meilleurs pilleurs d'Aquamarine.", inline=False)
        embed2.add_field(name="<:woaicon:1512165794740572292> Groupe de commandes /woa", value=
            "• `/woa historique [joueur]`\n> Consulte l'historique des tickets dépensés.\n\n"
            "• `/woa bilan`\n> Affiche l'activité économique globale du serveur.", inline=False)
        embed2.add_field(name="<:cible:1512573711134490775> Groupe de commandes /rival", value=
            "• `/rival start [nom_event] [seuil]` / `/rival stop`\n> Active ou désactive le radar secret en MP.\n\n"
            "• `/rival add [joueurs]` / `/rival list`\n> Gère tes cibles de rival en direct.", inline=False)
        embed2.add_field(name="<:events:1512574699555782666> Groupe de commandes Calendrier", value=
            "• `/calendrier setup [channel]`\n> Permets de choisir le channel ou seront envoyés les informations d'événements.\n\n"
            "• `/calendrier suivre [alliance]`\n> Permet de suivre une alliance sur les événements.\n\n"
            "• `/calendrier retirer [alliance]`\n> Retire une alliance du suivi événement.\n\n"
            "• `/calendrier actuelle`\n> Renvoie les événements ayant lieu ce mois.", inline=False)
        embeds.append(embed2)

        # 📑 PAGE 3 : COG FORTERESSES
        embed3 = discord.Embed(title="<:fortresses:1512574700839239892> 3. Module Forteresses", color=self.clr_forteresses)
        embed3.add_field(name="• `/forteresse scan [joueur] [glaces] [sables] [pics]`", value="> Recherche automatique en cascade des forteresses prêtes.", inline=False)
        embed3.add_field(name="• `/forteresse stop`", value="> Interrompt la session de recherche active.", inline=False)
        embeds.append(embed3)

        # 📑 PAGE 4 : COG GUERRE
        embed4 = discord.Embed(title="<:2_:1512574740915818527> 4. Module Guerre", color=self.clr_guerre)
        embed4.add_field(name="<:icon_analyze:1512573874150314005> Scanners & Opérations", value=
            "• `/alliance_scanner [alliance]`\n> Roster complet en temps réel (Cibles / Ordre des colombes).\n\n"
            "• `/proximite [mon_pseudo] [alliance_ennemie]`\n> Trouve les ennemis les plus proches de ton château.\n\n"
            "• `/cible [attaquant] [tri] (alliance_cible)`\n> Génère une liste de cibles légales selon la charte.\n\n"
            "• `/hr [attaquant] [defenseur]`\n> Vérifie la légalité d'une attaque selon les RoE.", inline=False)
        embed4.add_field(name="<:icon_friends2:1512573878801797271> Comparaison", value=
            "• `/compare_joueur [joueur1] [joueur2]`\n> Duel statistique complet en 7 rounds.\n\n"
            "• `/compare_alliance [alliance1] [alliance2]`\n> Duel complet entre deux alliances.", inline=False)
        embed4.add_field(name="<:4_:1512574743369224303> Diplomatie", value=
            "• `/diplomatie add [mon_alliance] [cible] [statut]`\n> Enregistre un lien Allié, PNA ou Guerre.\n\n"
            "• `/diplomatie remove [mon_alliance] [cible]`\n> Supprime un accord diplomatique.\n\n"
            "• `/diplomatie list [alliance]`\n> Affiche secrètement le registre diplomatique.", inline=False)
        embeds.append(embed4)

        # 📑 PAGE 5 : COG PROFILS
        embed5 = discord.Embed(title="<:renames:1512574708913143858> 5. Module Profils", color=self.clr_profils)
        embed5.add_field(name="<:Le_Hraut_Lumbricus_2:1512573890298380388> Renseignement Tactique", value=
            "• `/joueur [nom]`\n> Fiche complète d'un joueur (Niveau, PP, Positions, Châteaux).\n\n"
            "• `/alliance [nom]`\n> Profil détaillé de l'alliance et roster complet paginé.\n\n"
            "• `/colombe [joueur]`\n> Compte à rebours précis avant la fin de protection.", inline=False)
        embed5.add_field(name="<:memberlistactivity:1512573911257190481> Renseignement Historique", value=
            "• `/historique [choix] [joueur]`\n> Traque les changements de pseudos, d'alliances ou châteaux.\n\n"
            "• `/alliance_pp [alliance] [jours]`\n> Évolution de la puissance globale d'une alliance.", inline=False)
        embeds.append(embed5)

        # 📑 PAGE 6 : COG RADAR
        embed6 = discord.Embed(title="<:icon_analyze:1512573874150314005> 6. Module Radar Personnel", color=self.clr_radar)
        embed6.add_field(name="<:info:1512502828193808537> Surveillance Individuelle", value=
            "• `/radar add [joueur] (raison)`\n> Place une cible sous surveillance étroite (Alertes MP).\n\n"
            "• `/radar remove [joueur]`\n> Retire un joueur de ta surveillance personnelle.\n\n"
            "• `/radar list`\n> Tableau de bord centralisé de tous tes suivis actifs.", inline=False)
        embed6.add_field(name="<:info:1512502828193808537> Sous-groupe /radar alliance", value=
            "• `/radar alliance add [alliance] (raison)`\n> Alerte MP si Entrée, Sortie, Promotion ou Rétrogradation.\n\n"
            "• `/radar alliance remove [alliance]`\n> Coupe la surveillance globale sur l'alliance.", inline=False)
        embeds.append(embed6)

        view = MenuAideView(embeds)
        await interaction.followup.send(embed=embeds[0], view=view)

    # ==========================================
    # 📡 COMMANDE : STATUT SYSTÈME
    # ==========================================
    @app_commands.command(name="statut", description="Vérifie l'état de santé global du système (Bot, Stockage NAS, API GGE-Tracker)")
    async def statut_commande(self, interaction: discord.Interaction):
        try: await interaction.response.defer(thinking=True)
        except: return

        logger.info(f"📡 [Statut] Demande de diagnostic par {interaction.user.name}")
        embed = discord.Embed(title="📡 Diagnostic et Santé du Système", color=self.clr_statut, timestamp=discord.utils.utcnow())

        # 🤖 1. DIAGNOSTIC BOT DISCORD
        ping = round(self.bot.latency * 1000)
        maintenance_txt = "<:parameters:1512573735390154986> Oui" if getattr(self.bot, 'maintenance_mode', False) else "<:icon_friends2:1512573878801797271> Non"
        bot_txt = (
            f"**Statut** : 🟢 Opérationnel\n"
            f"**Latence (Ping)** : `{ping} ms`\n"
            f"**Version Core** : `{BOT_VERSION}`\n"
            f"**Mode Maintenance** : {maintenance_txt}"
        )
        embed.add_field(name="🤖 Bot Discord", value=bot_txt, inline=False)

        # 💾 2. DIAGNOSTIC STOCKAGE LOCAL (NAS) & AGENT DE SCAN
        try:
            total, used, free = shutil.disk_usage("/app/data")
            total_gb = total / (1024**3)
            used_gb = used / (1024**3)
            free_gb = free / (1024**3)
            pourcentage_plein = (used / total) * 100
            
            scans_dir = Path("/app/data/server_scans")
            last_scan_txt = "<:error:1512505075220611172> Aucun fichier trouvé (Scan en panne)"
            
            if scans_dir.exists():
                server_files = list(scans_dir.rglob('server_*.json'))
                if server_files:
                    latest_scan = max(server_files, key=lambda p: p.stat().st_mtime)
                    mtime_ts = int(latest_scan.stat().st_mtime)
                    last_scan_txt = f"<t:{mtime_ts}:F> (<t:{mtime_ts}:R>)"
            
            storage_txt = (
                f"**Disque Local** : 🟢 Connecté (Lecture/Écriture OK)\n"
                f"**Espace Total** : `{total_gb:.2f} Go`\n"
                f"**Espace Utilisé** : `{used_gb:.2f} Go` ({pourcentage_plein:.1f}%)\n"
                f"**Espace Libre** : `{free_gb:.2f} Go`\n"
                f"**Dernier Scan Serveur** : {last_scan_txt}"
            )
        except Exception as e:
            storage_txt = f"<:error:1512505075220611172> Erreur de lecture de l'espace de stockage : {e}"
            
        embed.add_field(name="💾 Stockage Interne", value=storage_txt, inline=False)

        # 📡 3. DIAGNOSTIC API LIVE (GGE-TRACKER)
        url_api = "https://api.gge-tracker.com/api/v1/"
        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        
        def to_discord_ts(iso_str):
            if not iso_str or iso_str == "null": return "Jamais"
            try:
                dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
                return f"<t:{int(dt.timestamp())}:R>"
            except:
                return "Date inconnue"

        # ⚡ Optimisation : Utilisation de la session HTTP persistante globale du bot
        session = self.bot.session if hasattr(self.bot, 'session') and self.bot.session else aiohttp.ClientSession()
        try:
            async with session.get(url_api, headers=headers, timeout=10) as r:
                if r.status == 200:
                    api_data = await r.json()
                    v_api = api_data.get("version", "Inconnue")
                    in_progress = "<:error:1512505075220611172> En cours..." if api_data.get("update_in_progress", False) else "🟢 Terminé / À jour"
                    updates = api_data.get("last_update", {})
                    
                    api_txt = (
                        f"**Statut API** : 🟢 En ligne (Code 200)\n"
                        f"**Version Distante** : `{v_api}`\n"
                        f"**Synchro Serveur GGE** : {in_progress}\n"
                    )
                    embed.add_field(name="📡 API GGE-Tracker (Live)", value=api_txt, inline=False)
                    
                    events_txt = (
                        f"<:pp1:1512438903821570160> **Puissances** : {to_discord_ts(updates.get('might'))}\n"
                        f"<:loot:1512439015570276553> **Pillages** : {to_discord_ts(updates.get('loot'))}\n"
                        f"<:nomads:1512431070719774750> **Nomades** : {to_discord_ts(updates.get('nomad'))}\n"
                        f"<:samurai:1512430844935929868> **Samouraïs** : {to_discord_ts(updates.get('samurai'))}\n"
                        f"<:bloodcrow:1512430942990368928> **Corbeaux** : {to_discord_ts(updates.get('bloodcrow'))}\n"
                        f"<:war_realms:1512573773658980504> **Etrangers** : {to_discord_ts(updates.get('war_realms'))}\n"
                        f"<:berimond:1512430901756428390> **Bérimond** : {to_discord_ts(updates.get('berimond_kingdom'))}"
                    )
                    embed.add_field(name="⏱️ Dernière capture des données GGE-Tracker", value=events_txt, inline=False)
                else:
                    embed.add_field(name="📡 API GGE-Tracker (Live)", value=f"<:error:1512505075220611172> L'API répond mais rencontre une instabilité (Code : {r.status}).", inline=False)
        except Exception as e:
            embed.add_field(name="📡 API GGE-Tracker (Live)", value=f"<:error:1512505075220611172> **Hors ligne** : Impossible de joindre l'API de suivi du jeu ({e}).", inline=False)

        setup_embed_footer(embed, interaction)
        await interaction.followup.send(embed=embed)

    # ==========================================
    # 🚀 COMMANDE : CHANGELOG
    # ==========================================
    @app_commands.command(name="changelog", description="Découvre les dernières nouveautés, corrections et améliorations appliquées au bot")
    async def changelog_commande(self, interaction: discord.Interaction):
        try: await interaction.response.defer(thinking=True)
        except: return

        logger.info(f"🚀 [Changelog] Consultation par {interaction.user.name}")
        
        if not os.path.exists(CHANGELOG_FILE):
            embed = discord.Embed(
                title="🚀 Notes de Mise à Jour (Changelog)", 
                description="📭 Aucune note de mise à jour n'est disponible pour le moment.",
                color=self.clr_changelog,
                timestamp=discord.utils.utcnow()
            )
            setup_embed_footer(embed, interaction)
            return await interaction.followup.send(embed=embed)

        try:
            with open(CHANGELOG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            patches = data.get("patches", [])
            if not patches:
                embed = discord.Embed(
                    title="🚀 Notes de Mise à Jour (Changelog)", 
                    description="📭 L'historique des patchs est vide.",
                    color=self.clr_changelog,
                    timestamp=discord.utils.utcnow()
                )
                setup_embed_footer(embed, interaction)
                return await interaction.followup.send(embed=embed)

            embeds = []
            total_patches = len(patches)

            for i, bloc in enumerate(patches):
                version = bloc.get('version', 'Version inconnue')
                date = bloc.get('date', 'Date inconnue')
                content = bloc.get('content', '')

                lignes_propres = []
                raw_lines = content if isinstance(content, list) else content.split('\n')
                
                for line in raw_lines:
                    line = line.strip()
                    if not line: continue
                    if not any(line.startswith(c) for c in ['🔹', '•', '-', '*', '🚀', '🛠️', '⚙️', '🟢', '❌', '⚠️', '📈', '✨', '🪙']):
                        lignes_propres.append(f"🔹 {line}")
                    else:
                        lignes_propres.append(line)

                description_patch = "\n".join(lignes_propres) if lignes_propres else "*Aucun détail fourni.*"

                embed = discord.Embed(
                    title="🚀 Notes de Mise à Jour",
                    description=f"### 📦 Version {version}\n*Déployée le {date}*\n\n{description_patch}",
                    color=self.clr_changelog,
                    timestamp=discord.utils.utcnow()
                )
                setup_embed_footer(embed, interaction)
                embeds.append(embed)

            if len(embeds) == 1:
                await interaction.followup.send(embed=embeds[0])
            else:
                view = PaginationView(embeds)
                await interaction.followup.send(embed=embeds[0], view=view)
                
        except Exception as e:
            logger.error(f"<:error:1512505075220611172> Erreur lecture changelog.json : {e}")
            embed_err = discord.Embed(
                title="🚀 Notes de Mise à Jour (Changelog)",
                description="<:error:1512505075220611172> Impossible de lire correctement le fichier des notes de mise à jour (`changelog.json`).",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            setup_embed_footer(embed, interaction)
            await interaction.followup.send(embed=embed_err)

    # ==========================================
    # 📩 COMMANDE : CONTACT / RECOMMANDATIONS
    # ==========================================
    @app_commands.command(name="contact", description="Envoie un problème, un bug ou une suggestion directement au développeur")
    @app_commands.describe(message="Rédige ton problème ou ta suggestion en détail ici")
    async def contact_commande(self, interaction: discord.Interaction, message: str):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return

        logger.info(f"📩 [Contact] Nouveau message de {interaction.user.name} ({interaction.user.id})")
        maintenant_iso = discord.utils.utcnow().isoformat().replace('+00:00', 'Z')
        
        # 🔐 Sécurisé : Utilisation du gestionnaire de verrou asynchrone pour éviter les collisions d'écritures
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
                return await interaction.followup.send("<:error:1512505075220611172> Une erreur technique interne a empêché l'enregistrement de ton message.")

        try:
            developpeur = self.bot.get_user(MON_ID_DISCORD)
            if not developpeur:
                developpeur = await self.bot.fetch_user(MON_ID_DISCORD)

            embed_mp = discord.Embed(title="📩 Nouveau Ticket Reçu !", color=self.clr_contact, timestamp=discord.utils.utcnow())
            embed_mp.add_field(name="<:players:1512504277392953426> Expéditeur", value=f"**{interaction.user.name}** (`{interaction.user.id}`)", inline=True)
            embed_mp.add_field(name="<:castles:1512574693859786822> Provenance", value=f"*{nouveau_ticket['serveur']}*", inline=True)
            embed_mp.add_field(name="<:memberlist:1512572899360378971> Message", value=f"```text\n{message}\n```", inline=False)
            setup_embed_footer(embed_mp, interaction)

            await developpeur.send(embed=embed_mp)
            
        except discord.Forbidden:
            logger.warning(f"<:error:1512505075220611172> [Contact] Impossible d'envoyer le MP à {MON_ID_DISCORD} (DMs fermés).")
        except Exception as e:
            logger.error(f"<:error:1512505075220611172> [Contact] Erreur lors de l'alerte MP : {e}")

        await interaction.followup.send("<:players:1512504277392953426> **Merci !** Ton message a bien été enregistré et transmis au développeur.")


# 🔌 Fonction de chargement du module
async def setup(bot: commands.Bot):
    await bot.add_cog(AideCog(bot))