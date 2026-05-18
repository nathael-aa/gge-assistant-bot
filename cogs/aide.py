# -*- coding: utf-8 -*-
import os
import json
import logging
import shutil
import aiohttp
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands

# 🛠️ On importe nos outils depuis utils.py
from utils import BOT_VERSION, BASE_DATA_PATH

logger = logging.getLogger("GGE_Bot")
CONTACTS_FILE = BASE_DATA_PATH / 'contacts.json'
CHANGELOG_FILE = BASE_DATA_PATH / 'changelog.json'
MON_ID_DISCORD = 1166375576685265040  # 🎯 Ton ID cible pour les alertes MP

class AideCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ==========================================
    # 📖 COMMANDE : AIDE
    # ==========================================
    @app_commands.command(name="aide", description="📖 Affiche le manuel d'utilisation complet du bot GGE Assistant")
    async def aide_commande(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        embed = discord.Embed(
            title="📖 Manuel d'Utilisation - GGE Assistant",
            description=f"Voici la liste complète des commandes disponibles pour dominer le serveur E4K FR1.\n*Version du bot : {BOT_VERSION}*",
            color=discord.Color.gold()
        )

        # 🌍 1. PROFIL ET SERVEUR
        profil_txt = (
            "▶️ `/set pseudo [pseudo]` : 📩 Lie ton compte Discord à ton pseudo GGE.\n"
            "▶️ `/joueur [nom]` : 🌍📩 Affiche le profil détaillé (Coords, Niveaux, Points).\n"
            "▶️ `/alliance [nom]` : 🌍📩 Affiche le roster complet trié par puissance.\n"
            "▶️ `/historique [choix] [joueur]` : 🌍📩 Traque les anciens pseudos, alliances et déménagements.\n"
            "▶️ `/serveur` : 🌍📩 Affiche le Top 15 des alliances et les statistiques globales du serveur.\n"
            "▶️ `/alliance_pp [nom] [jours]` : 🌍📩 Affiche l'évolution de la puissance sur plusieurs jours."
        )
        embed.add_field(name="🌍 Profil & Serveur", value=profil_txt, inline=False)

        # ⚔️ 2. GUERRE ET COMBAT
        guerre_txt = (
            "▶️ `/alliance_scanner [nom]` : 🌍📩 Analyse le roster ennemi en temps réel (Colombes, PP, Cibles).\n"
            "▶️ `/proximite [toi] [alliance_ennemie]` : 🌍📩 Trouve les châteaux ennemis les plus proches de tes positions.\n"
            "▶️ `/cible [attaquant] [tri] (alliance)` : 🌍📩 Trouve des cibles légales autour de toi (respect charte RoE).\n"
            "▶️ `/colombe [joueur]` : 🌍📩 Vérifie l'heure exacte de fin de protection d'un joueur.\n"
            "▶️ `/hr [attaquant] [défenseur]` : 🌍📩 L'arbitre : Vérifie si une attaque est légale selon les règles."
        )
        embed.add_field(name="⚔️ Renseignement & Guerre", value=guerre_txt, inline=False)

        # 🏟️ 3. DIVERTISSEMENT
        versus_txt = (
            "▶️ `/compare_joueur [J1] [J2]` : 🌍📩 Duel statistique entre deux guerriers (7 rounds).\n"
            "▶️ `/compare_alliance [A1] [A2]` : 🌍📩 Comparaison détaillée entre deux alliances."
        )
        embed.add_field(name="🏟️ Arène & Comparaisons", value=versus_txt, inline=False)

        # 🎪 4. ÉVÉNEMENTS
        events_txt = (
            "▶️ `/event_joueur [event] [nom] [mode]` : 🌍📩 Consulte le dernier score ou le cumul d'un joueur.\n"
            "▶️ `/event_alliance [event] [nom]` : 🌍📩 Génère le classement interne des membres d'une alliance.\n"
            "▶️ `/event_bilan [event] [alliance]` : 🌍📩 Vérifie qui a atteint les objectifs d'alliance (avec lissage possible).\n"
            "▶️ `/rival start [event] [seuil]` : 📩 Lance ton radar de compétition pour un événement.\n"
            "▶️ `/rival add [joueurs]` : 📩 Ajoute jusqu'à 5 joueurs d'un coup à surveiller (Max 10).\n"
            "▶️ `/rival list` : 📩 Affiche l'état de ton radar de rivaux.\n"
            "▶️ `/rival stop` : 📩 Coupe ton radar de rivaux."
        )
        embed.add_field(name="🎪 Événements (Bérimond, Étrangers, Corbeaux...)", value=events_txt, inline=False)

        # 🏰 5. RADAR FORTERESSES
        fort_txt = (
            "▶️ `/forteresse scan` : 📩 Ouvre un formulaire pour traquer les forteresses libres (Alertes MP).\n"
            "▶️ `/forteresse stop` : 📩 Arrête ta session de tracking."
        )
        embed.add_field(name="🏰 Radar à Forteresses (Farm)", value=fort_txt, inline=False)

        # 🕵️‍♂️ 6. RADAR PERSONNEL
        radar_txt = (
            "▶️ `/radar add [joueur]` : 📩 Place un joueur sous surveillance (MP).\n"
            "▶️ `/radar remove [joueur]` : 📩 Retire un joueur de ta liste.\n"
            "▶️ `/radar alliance add [alliance]` : 📩 Place une alliance sous surveillance complète (Mouvements, Rangs, Infos en MP).\n"
            "▶️ `/radar alliance remove [alliance]` : 📩 Retire une alliance de ta liste.\n"
            "▶️ `/radar list` : 📩 Affiche ton tableau de bord de surveillance centralisé."
        )
        embed.add_field(name="🕵️‍♂️ Radar Personnel (Surveillance)", value=radar_txt, inline=False)

        # 🤝 7. ADMINISTRATION
        admin_txt = (
            "▶️ `/event_objectif_set` : 🛠️🌍 (Admin) Définit les objectifs de points par tranche de niveau.\n"
            "▶️ `/diplomatie add` : 🛠️🌍 (Admin) Déclare une alliance en Allié, PNA ou Guerre.\n"
            "▶️ `/diplomatie remove` : 🛠️🌍 (Admin) Retire une alliance alliée, PNA ou Guerre.\n"
            "▶️ `/diplomatie list` : 🛠️🌍 (Admin) Affiche le registre diplomatique en message privé.\n"
            "▶️ `/diplo_hr` : 🛠️📩🌍 (Admin) Outil d'arbitrage avancé."
        )
        embed.add_field(name="🤝 Administration (Réservé aux Chefs)", value=admin_txt, inline=False)

        # 📩 8. SUPPORT & DIAGNOSTIC
        contact_txt = (
            "▶️ `/contact [message]` : 📩 Une remarque, un bug ou une suggestion ? Rapport direct au développeur.\n"
            "▶️ `/statut` : 🌍📩 Vérifie la santé du bot, l'état du stockage NAS et la fraîcheur de l'API live.\n"
            "▶️ `/changelog` : 🌍📩 Découvre les dernières nouveautés et améliorations du bot."
        )
        embed.add_field(name="📩 Support & Diagnostic", value=contact_txt, inline=False)

        embed.set_footer(text="Légende : 🌍 = Utilisable sur un Serveur | 📩 = Utilisable en Message Privé | 🛠️ = Réservé aux Administrateurs")

        await interaction.followup.send(embed=embed)

    # ==========================================
    # 📡 COMMANDE : STATUT SYSTÈME
    # ==========================================
    @app_commands.command(name="statut", description="📡 Vérifie l'état de santé global du système (Bot, Stockage NAS, API GGE-Tracker)")
    async def statut_commande(self, interaction: discord.Interaction):
        try: await interaction.response.defer(thinking=True)
        except: return

        logger.info(f"📡 [Statut] Demande de diagnostic par {interaction.user.name}")

        embed = discord.Embed(title="📡 Diagnostic et Santé du Système", color=discord.Color.brand_green(), timestamp=discord.utils.utcnow())

        # 🤖 1. DIAGNOSTIC BOT DISCORD
        ping = round(self.bot.latency * 1000)
        maintenance_txt = "🔴 Oui" if getattr(self.bot, 'maintenance_mode', False) else "🟢 Non"
        bot_txt = (
            f"**Statut** : 🟢 Opérationnel\n"
            f"**Latence (Ping)** : `{ping} ms`\n"
            f"**Version Core** : `{BOT_VERSION}`\n"
            f"**Mode Maintenance** : {maintenance_txt}"
        )
        embed.add_field(name="🤖 Bot Discord", value=bot_txt, inline=False)

        # 💾 2. DIAGNOSTIC STOCKAGE LOCAL (NAS)
        try:
            total, used, free = shutil.disk_usage("/app/data")
            total_gb = total / (1024**3)
            used_gb = used / (1024**3)
            free_gb = free / (1024**3)
            pourcentage_plein = (used / total) * 100
            
            storage_txt = (
                f"**Disque Local** : 🟢 Connecté (Lecture/Écriture OK)\n"
                f"**Espace Total** : `{total_gb:.2f} Go`\n"
                f"**Espace Utilisé** : `{used_gb:.2f} Go` ({pourcentage_plein:.1f}%)\n"
                f"**Espace Libre** : `{free_gb:.2f} Go`"
            )
        except Exception as e:
            storage_txt = f"⚠️ Erreur de lecture de l'espace de stockage : {e}"
            
        embed.add_field(name="💾 Stockage Interne (NAS)", value=storage_txt, inline=False)

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

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url_api, headers=headers, timeout=10) as r:
                    if r.status == 200:
                        api_data = await r.json()
                        v_api = api_data.get("version", "Inconnue")
                        in_progress = "⚠️ En cours..." if api_data.get("update_in_progress", False) else "🟢 Terminé / À jour"
                        m_count = api_data.get("discord_member_count", "Inconnu")
                        updates = api_data.get("last_update", {})
                        
                        api_txt = (
                            f"**Statut API** : 🟢 En ligne (Code 200)\n"
                            f"**Version Distante** : `{v_api}`\n"
                            f"**Synchro Serveur GGE** : {in_progress}\n"
                            f"**Membres Hub Discord** : `{m_count} joueurs`"
                        )
                        embed.add_field(name="📡 API GGE-Tracker (Live)", value=api_txt, inline=False)
                        
                        events_txt = (
                            f"💪 **Puissances (PP)** : {to_discord_ts(updates.get('might'))}\n"
                            f"💰 **Pillages (Loot)** : {to_discord_ts(updates.get('loot'))}\n"
                            f"👹 **Samouraïs** : {to_discord_ts(updates.get('samurai'))}\n"
                            f"⛺ **Nomades** : {to_discord_ts(updates.get('nomad'))}\n"
                            f"🦅 **Corbeaux** : {to_discord_ts(updates.get('bloodcrow'))}"
                        )
                        embed.add_field(name="⏱️ Dernière capture des données GGE-Tracker", value=events_txt, inline=False)
                    else:
                        embed.add_field(name="📡 API GGE-Tracker (Live)", value=f"⚠️ L'API répond mais rencontre une instabilité (Code : {r.status}).", inline=False)
            except Exception as e:
                embed.add_field(name="📡 API GGE-Tracker (Live)", value=f"🔴 **Hors ligne** : Impossible de joindre l'API de suivi du jeu ({e}).", inline=False)

        embed.set_footer(text=f"{BOT_VERSION} | Analyse temps réel")
        await interaction.followup.send(embed=embed)

    # ==========================================
    # 🚀 COMMANDE : CHANGELOG
    # ==========================================
    @app_commands.command(name="changelog", description="🚀 Découvre les dernières nouveautés, corrections et améliorations appliquées au bot")
    async def changelog_commande(self, interaction: discord.Interaction):
        try: await interaction.response.defer(thinking=True)
        except: return

        logger.info(f"🚀 [Changelog] Consultation par {interaction.user.name}")

        embed = discord.Embed(title="🚀 Notes de Mise à Jour (Changelog)", color=discord.Color.blue(), timestamp=discord.utils.utcnow())
        
        # Sûreté si le fichier changelog.json n'existe pas encore sur le NAS
        if not os.path.exists(CHANGELOG_FILE):
            embed.description = "📭 Aucune note de mise à jour n'est disponible pour le moment."
            embed.set_footer(text=BOT_VERSION)
            return await interaction.followup.send(embed=embed)

        try:
            with open(CHANGELOG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            embed.description = data.get("description", f"Historique des modifications de GGE Assistant (Build: {BOT_VERSION})")
            
            for bloc in data.get("patches", []):
                version = bloc.get('version', 'Version inconnue')
                date = bloc.get('date', 'Date inconnue')
                content = bloc.get('content', '*Aucun détail fourni.*')
                embed.add_field(name=f"📦 {version} ({date})", value=content, inline=False)
            
            embed.set_footer(text=f"{BOT_VERSION}")
            
        except Exception as e:
            logger.error(f"❌ Erreur lecture changelog.json : {e}")
            embed.color = discord.Color.red()
            embed.description = "⚠️ Impossible de lire correctement le fichier des notes de mise à jour (`changelog.json`)."
            embed.set_footer(text=BOT_VERSION)

        await interaction.followup.send(embed=embed)

    # ==========================================
    # 📩 COMMANDE : CONTACT / RECOMMANDATIONS
    # ==========================================
    @app_commands.command(name="contact", description="📩 Envoie un problème, un bug ou une suggestion directement au développeur")
    @app_commands.describe(message="Rédige ton problème ou ta suggestion en détail ici")
    async def contact_commande(self, interaction: discord.Interaction, message: str):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return

        logger.info(f"📩 [Contact] Nouveau message de {interaction.user.name} ({interaction.user.id})")
        maintenant_iso = discord.utils.utcnow().isoformat().replace('+00:00', 'Z')
        
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
            return await interaction.followup.send("⚠️ Une erreur technique interne a empêché l'enregistrement de ton message.")

        try:
            developpeur = self.bot.get_user(MON_ID_DISCORD)
            if not developpeur:
                developpeur = await self.bot.fetch_user(MON_ID_DISCORD)

            embed_mp = discord.Embed(title="📩 Nouveau Ticket Reçu !", color=discord.Color.orange(), timestamp=discord.utils.utcnow())
            embed_mp.add_field(name="👤 Expéditeur", value=f"**{interaction.user.name}** (`{interaction.user.id}`)", inline=True)
            embed_mp.add_field(name="🏰 Provenance", value=f"*{nouveau_ticket['serveur']}*", inline=True)
            embed_mp.add_field(name="💬 Message", value=f"```text\n{message}\n```", inline=False)
            embed_mp.set_footer(text=BOT_VERSION)

            await developpeur.send(embed=embed_mp)
            
        except discord.Forbidden:
            logger.warning(f"⚠️ [Contact] Impossible d'envoyer le MP à {MON_ID_DISCORD} (DMs fermés).")
        except Exception as e:
            logger.error(f"❌ [Contact] Erreur lors de l'alerte MP : {e}")

        await interaction.followup.send("✅ **Merci !** Ton message a bien été enregistré et transmis au développeur.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AideCog(bot))