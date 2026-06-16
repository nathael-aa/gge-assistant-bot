# -*- coding: utf-8 -*-
import os
import json
import logging
import asyncio
import aiohttp
import urllib.parse
import re
from datetime import datetime
from bs4 import BeautifulSoup
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import alliance_autocomplete, setup_embed_footer, BASE_DATA_PATH, format_num, generer_rapport_alliance_embed, TRACKER_EVENTS

logger = logging.getLogger("GGE_Bot")

CALENDRIER_FILE = BASE_DATA_PATH / 'calendrier_config.json'

async def load_calendrier_async():
    if not CALENDRIER_FILE.exists():
        return {"guilds": {}, "notified": []}
    try:
        with open(CALENDRIER_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if "guilds" not in data:
                return {"guilds": {}, "notified": data.get("notified", [])}
            return data
    except:
        return {"guilds": {}, "notified": []}

async def save_calendrier_async(data):
    with open(CALENDRIER_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

class CalendrierCog(commands.GroupCog, group_name="calendrier", group_description="Gestion du calendrier des événements"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cached_events = []
        self.last_scrape_time = None
        self.event_mapping = {
            "samurai invasion": {"name": "Samouraï", "emoji": "<:samurai:1512430844935929868>", "color": 0xE17055, "tracker_name": "Samouraïs", "start": "11:00", "end": "09:00"},
            "nomad invasion": {"name": "Nomade", "emoji": "<:nomads:1512431070719774750>", "color": 0xF1C40F, "tracker_name": "Nomades", "start": "11:00", "end": "09:00"},
            "bloodcrow invasion": {"name": "Corbeaux de Sang", "emoji": "<:bloodcrow:1512430942990368928>", "color": 0x2C3E50, "tracker_name": "Corbeaux de Sang", "start": "11:00", "end": "09:00"},
            "war of the realms": {"name": "Guerre des Royaumes", "emoji": "<:war_realms:1512573773658980504>", "color": 0xC0392B, "tracker_name": "Guerre des Royaumes", "start": "11:00", "end": "09:00"},
            "berimond": {"name": "Berimond", "emoji": "<:berimond:1512430901756428390>", "color": 0x2980B9, "tracker_name": "Bataille de Bérimond", "start": "11:00", "end": "09:00"},
            "bladecoast": {"name": "Côte Tranchante", "emoji": "<:bladecoast:1514704235894407399>", "color": 0x16A085, "tracker_name": None, "start": "11:00", "end": "09:00"},
            "rift raid": {"name": "Raid de la Faille", "emoji": "<:riftraid:1514704237206966272>", "color": 0x8E44AD, "tracker_name": None, "start": "11:00", "end": "09:00"},
            "grand tournament": {"name": "Grand Tournoi", "emoji": "<:grandtournament:1514704234128343040>", "color": 0xD35400, "tracker_name": None, "start": "11:00", "end": "09:00"},
            "beyond the horizon": {"name": "Au-delà de l'horizon", "emoji": "<:beyondthehorizonicon:1512573808379301919>", "color": 0x1ABC9C, "tracker_name": None, "start": "11:00", "end": "00:40"},
            "outer realms": {"name": "Royaumes extérieurs", "emoji": "<:outerrealmsicon:1512573734404231329>", "color": 0x34495E, "tracker_name": None, "start": "11:00", "end": "00:40"},
            "imperial patronage": {"name": "Patronage impérial", "emoji": "<:patronage:1514704230106140874>", "color": 0xF39C12, "tracker_name": None, "start": "11:00", "end": "09:00"},
            "grand nobility contest": {"name": "Grand concours de noblesse", "emoji": "<:ltpe:1514704228801708052>", "color": 0x7F8C8D, "tracker_name": None, "start": "11:00", "end": "09:00"}
        }

    async def cog_load(self):
        if not self.check_newshub_calendar_task.is_running():
            self.check_newshub_calendar_task.start()

    async def cog_unload(self):
        self.check_newshub_calendar_task.cancel()

    # ==========================================
    # ⚙️ COMMANDES DE CONFIGURATION DU SUIVI
    # ==========================================
    @app_commands.command(name="setup", description="Définit le salon où seront envoyées les alertes du calendrier")
    @app_commands.describe(salon="Le salon textuel pour les événements")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def c_setup(self, interaction: discord.Interaction, salon: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        data = await load_calendrier_async()
        guild_id = str(interaction.guild_id)
        
        if guild_id not in data["guilds"]:
            data["guilds"][guild_id] = {"channel_id": None, "tracked_alliances": []}
            
        data["guilds"][guild_id]["channel_id"] = salon.id
        await save_calendrier_async(data)
        
        await interaction.followup.send(f"✅ Le salon des annonces d'événements a été défini sur {salon.mention}.")

    @app_commands.command(name="suivre", description="Ajoute une alliance au rapport automatique de fin d'événement")
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.describe(alliance="Nom de l'alliance à suivre")
    @app_commands.guild_only()
    async def c_track(self, interaction: discord.Interaction, alliance: str):
        await interaction.response.defer(ephemeral=True)
        data = await load_calendrier_async()
        guild_id = str(interaction.guild_id)
        
        if guild_id not in data["guilds"]:
            data["guilds"][guild_id] = {"channel_id": None, "tracked_alliances": []}
            
        tracked = data["guilds"][guild_id]["tracked_alliances"]
        if alliance.lower() in [a.lower() for a in tracked]:
            return await interaction.followup.send(f"<:error:1512505075220611172> L'alliance **{alliance}** est déjà dans la liste de suivi de ce serveur.")
            
        tracked.append(alliance)
        await save_calendrier_async(data)
        await interaction.followup.send(f"✅ L'alliance **{alliance}** a été ajoutée ! Ses résultats seront envoyés à la fin des événements majeurs.")

    @app_commands.command(name="retirer", description="Retire une alliance du rapport de fin d'événement")
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.describe(alliance="Nom de l'alliance à retirer")
    @app_commands.guild_only()
    async def c_untrack(self, interaction: discord.Interaction, alliance: str):
        await interaction.response.defer(ephemeral=True)
        data = await load_calendrier_async()
        guild_id = str(interaction.guild_id)
        
        if guild_id not in data["guilds"]:
            return await interaction.followup.send("<:error:1512505075220611172> Aucune configuration trouvée pour ce serveur.")
            
        tracked = data["guilds"][guild_id]["tracked_alliances"]
        if alliance.lower() not in [a.lower() for a in tracked]:
            return await interaction.followup.send(f"<:error:1512505075220611172> L'alliance **{alliance}** n'est pas dans la liste de suivi.")
            
        data["guilds"][guild_id]["tracked_alliances"] = [a for a in tracked if a.lower() != alliance.lower()]
        await save_calendrier_async(data)
        await interaction.followup.send(f"❌ L'alliance **{alliance}** a été retirée de la liste de suivi.")

    @app_commands.command(name="actuelle", description="Affiche le calendrier complet des événements du mois")
    async def c_mois(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        
        maintenant = datetime.now()
        
        # 🛠️ LA PROTECTION : La commande manuelle utilise le cache elle aussi !
        if not self.cached_events or self.last_scrape_time is None or (maintenant - self.last_scrape_time).total_seconds() > 7200:
            nouveaux_events = await self.parse_live_calendar()
            if nouveaux_events:
                self.cached_events = nouveaux_events
                self.last_scrape_time = maintenant
            else:
                if not self.cached_events:
                    return await interaction.followup.send("<:error:1512505075220611172> Impossible de récupérer le calendrier officiel pour le moment.")

        events = self.cached_events
            
        events.sort(key=lambda x: x["start"])
        
        lignes_calendrier = []
        for ev in events:
            meta = self.event_mapping[ev["key"]]
            ts_start = int(ev["start"].timestamp())
            ts_end = int(ev["end"].timestamp())
            
            lignes_calendrier.append(f"{meta['emoji']} **{meta['name']}** : <t:{ts_start}:d> ➔ <t:{ts_end}:d>")
            
        # 🕒 Création du timestamp pour le dernier scan
        ts_last_scan = int(self.last_scrape_time.timestamp()) if self.last_scrape_time else int(maintenant.timestamp())
        
        # 🛠️ CORRECTION : On utilise un + classique au lieu de la f-string pour l'antislash
        texte_description = f"<:info:1512502828193808537> Dernière mise à jour : <t:{ts_last_scan}:R>\n\n" + "\n".join(lignes_calendrier)
            
        embed = discord.Embed(
            title="📅 Calendrier des Événements",
            description=texte_description,
            color=0x3498DB
        )
        setup_embed_footer(embed, interaction)
        await interaction.followup.send(embed=embed)

    # ==========================================
    # 🕵️‍♂️ MOTEUR D'EXTRACTION HTML (BS4)
    # ==========================================
    async def parse_live_calendar(self):
        url = "https://communityhub.goodgamestudios.com/newshube4k"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        
        try:
            async with self.bot.session.get(url, headers=headers, timeout=15) as r:
                if r.status != 200: return []
                
                html_content = await r.text()
                soup = BeautifulSoup(html_content, 'html.parser')
                
                found_events = []
                current_year = datetime.now().year
                paragraphs = soup.find_all('p')
                
                for idx, p in enumerate(paragraphs):
                    text = p.get_text().strip().lower()
                    
                    matched_key = next((key for key in self.event_mapping.keys() if key in text), None)
                    
                    if matched_key and idx + 1 < len(paragraphs):
                        date_text = paragraphs[idx + 1].get_text().strip()
                        matches = re.findall(r"(\d{2}/\d{2})\s*-\s*(\d{2}/\d{2})", date_text)
                        
                        for start_str, end_str in matches:
                            try:
                                h_start = self.event_mapping[matched_key]["start"]
                                h_end = self.event_mapping[matched_key]["end"]
                                
                                start_dt = datetime.strptime(f"{start_str}/{current_year} {h_start}", "%d/%m/%Y %H:%M")
                                end_dt = datetime.strptime(f"{end_str}/{current_year} {h_end}", "%d/%m/%Y %H:%M")
                                
                                if end_dt < start_dt:
                                    end_dt = end_dt.replace(year=current_year + 1)
                                    
                                found_events.append({"key": matched_key, "start": start_dt, "end": end_dt})
                            except:
                                continue
                return found_events
        except Exception as e:
            logger.error(f"❌ [Calendrier] Erreur BS4 : {e}")
            return []

    # ==========================================
    # 🛰️ LA TÂCHE DE FOND
    # ==========================================
    @tasks.loop(minutes=1)
    async def check_newshub_calendar_task(self):
        maintenant = datetime.now()
        
        # 🛠️ LE CACHE : On ne scrape le site web que si on ne l'a pas fait depuis 2 heures (7200 secondes)
        if not self.cached_events or self.last_scrape_time is None or (maintenant - self.last_scrape_time).total_seconds() > 7200:
            nouveaux_events = await self.parse_live_calendar()
            if nouveaux_events:
                self.cached_events = nouveaux_events
                self.last_scrape_time = maintenant
                logger.info(f"🔄 [Calendrier] Cache mis à jour avec {len(nouveaux_events)} événements.")
            else:
                # Si le site bug (ou si l'IP est encore bannie), on utilise ce qu'on avait gardé en mémoire !
                if not self.cached_events:
                    return

        # On utilise la mémoire du bot au lieu de refaire une requête HTML
        events = self.cached_events

        data = await load_calendrier_async()
        notified = data.get("notified", [])
        modifie = False

        for ev in events:
            meta = self.event_mapping[ev["key"]]
            
            uid_start = f"{ev['key']}_{ev['start'].strftime('%Y-%m-%d')}_start"
            uid_end = f"{ev['key']}_{ev['end'].strftime('%Y-%m-%d')}_end"

            # 🟢 DÉBUT D'ÉVÉNEMENT (11h00)
            if maintenant >= ev["start"] and uid_start not in notified:
                logger.info(f"🔎 [Calendrier] DÉCLENCHEMENT DÉBUT de {meta['name']}")
                
                if ev["start"].date() == maintenant.date():
                    ts_fin = int(ev["end"].timestamp())
                    embed_start = discord.Embed(
                        title=f"{meta['emoji']} DÉBUT D'ÉVÉNEMENT : {meta['name']}",
                        description=f"L'heure a sonné ! Un nouvel événement vient d'ouvrir ses portes sur nos terres.\n\n"
                                    f"⏳ **Fermeture prévue** : <t:{ts_fin}:f> (<t:{ts_fin}:R>)",
                        color=meta["color"]
                    )
                    setup_embed_footer(embed_start, None)
                    
                    for guild_id, g_info in data.get("guilds", {}).items():
                        channel_id = g_info.get("channel_id")
                        if channel_id:
                            channel = self.bot.get_channel(channel_id)
                            if not channel:
                                try: channel = await self.bot.fetch_channel(channel_id)
                                except: pass
                            
                            if channel:
                                try: await channel.send(embed=embed_start)
                                except: pass

                notified.append(uid_start)
                modifie = True

            # 🔴 FIN D'ÉVÉNEMENT (09h00)
            if maintenant >= ev["end"] and uid_end not in notified:
                logger.info(f"🔎 [Calendrier] DÉCLENCHEMENT FIN de {meta['name']}")
                
                if ev["end"].date() == maintenant.date():
                    for guild_id, g_info in data.get("guilds", {}).items():
                        channel_id = g_info.get("channel_id")
                        if channel_id:
                            channel = self.bot.get_channel(channel_id)
                            if not channel:
                                try: channel = await self.bot.fetch_channel(channel_id)
                                except: pass

                            if channel:
                                embed_end = discord.Embed(
                                    title=f"🛑 FIN D'ÉVÉNEMENT : {meta['name']}",
                                    description=f"Le calme revient sur le serveur. L'événement est officiellement terminé !",
                                    color=0x2C3E50
                                )
                                setup_embed_footer(embed_end, None)
                                try: await channel.send(embed=embed_end)
                                except: pass

                                tracked = g_info.get("tracked_alliances", [])
                                if tracked and meta["tracker_name"]:
                                    event_keys = TRACKER_EVENTS.get(meta["tracker_name"])
                                    if event_keys:
                                        for alliance_nom in tracked:
                                            embed_rapport, error, _, _ = await generer_rapport_alliance_embed(
                                                self.bot, meta['name'], event_keys, alliance_nom, meta['color']
                                            )
                                            if embed_rapport:
                                                setup_embed_footer(embed_rapport, None)
                                                try: await channel.send(embed=embed_rapport)
                                                except: pass
                                            else:
                                                try: await channel.send(f"⚠️ Erreur de rapport pour **{alliance_nom}** : {error}")
                                                except: pass

                notified.append(uid_end)
                modifie = True

        if modifie:
            if len(notified) > 60: notified = notified[-60:]
            data["notified"] = notified
            await save_calendrier_async(data)

    @check_newshub_calendar_task.before_loop
    async def before_check_newshub_calendar_task(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(CalendrierCog(bot))