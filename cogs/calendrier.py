import json
import logging
import re
import traceback
from datetime import datetime, timedelta

import discord
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands, tasks

from utils import (
    SERVEURS_DIR,
    TRACKER_EVENTS,
    alliance_autocomplete,
    generer_rapport_alliance_embed,
    get_server_config,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")

CALENDRIER_FILE = SERVEURS_DIR / "calendrier.json"


# ==========================================
# 🛠️ CLASSE DE NAVIGATION UI (BOUTONS)
# ==========================================
class CalendarNavView(discord.ui.View):
    def __init__(self, embeds_dict, current_page, langue="fr"):
        super().__init__(timeout=None)
        self.embeds = embeds_dict
        self.current_page = current_page
        self.langue = langue

        self.btn_past = discord.ui.Button(
            label=t(langue, "cal_btn_past", defaut="Historique"),
            emoji="<:lastpage:1533554126984581283>",
            custom_id="cal_past",
        )
        self.btn_past.callback = self.callback_past

        self.btn_main = discord.ui.Button(
            label=t(langue, "cal_btn_main", defaut="Actuels & À venir"),
            emoji="<:main:1535282885769171006>",
            custom_id="cal_main",
        )
        self.btn_main.callback = self.callback_main

        self.btn_future = discord.ui.Button(
            label=t(langue, "cal_btn_future", defaut="À venir (Uniquement)"),
            emoji="<:nextpage:1533554128230420590>",
            custom_id="cal_future",
        )
        self.btn_future.callback = self.callback_future

        self.add_item(self.btn_past)
        self.add_item(self.btn_main)
        self.add_item(self.btn_future)

        self.update_buttons()

    def update_buttons(self):
        for child in self.children:
            if child.custom_id == f"cal_{self.current_page}":
                child.disabled = True
                child.style = discord.ButtonStyle.primary
            else:
                child.disabled = False
                child.style = discord.ButtonStyle.secondary

    async def callback_past(self, interaction: discord.Interaction):
        self.current_page = "past"
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds["past"], view=self)

    async def callback_main(self, interaction: discord.Interaction):
        self.current_page = "main"
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds["main"], view=self)

    async def callback_future(self, interaction: discord.Interaction):
        self.current_page = "future"
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds["future"], view=self)


async def load_calendrier_async():
    if not CALENDRIER_FILE.exists():
        return {"guilds": {}, "notified": []}
    try:
        with open(CALENDRIER_FILE, encoding="utf-8") as f:
            data = json.load(f)
            if "guilds" not in data:
                return {"guilds": {}, "notified": data.get("notified", [])}
            return data
    except:
        return {"guilds": {}, "notified": []}


async def save_calendrier_async(data):
    with open(CALENDRIER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class CalendrierCog(commands.GroupCog, group_name="calendar", group_description="Event calendar management"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cached_events = []
        self.last_scrape_time = None
        self.event_mapping = {
            "samurai invasion": {
                "name_key": "cal_ev_samurai",
                "name_default": "Samouraï",
                "emoji": "<:samurai:1512430844935929868>",
                "color": 0xBF0000,
                "tracker_name": "Samouraïs",
                "start": "11:00",
                "end": "09:00",
            },
            "nomad invasion": {
                "name_key": "cal_ev_nomad",
                "name_default": "Nomade",
                "emoji": "<:nomads:1512431070719774750>",
                "color": 0xEDC951,
                "tracker_name": "Nomades",
                "start": "11:00",
                "end": "09:00",
            },
            "bloodcrow invasion": {
                "name_key": "cal_ev_bloodcrow",
                "name_default": "Corbeaux de Sang",
                "emoji": "<:bloodcrow:1512430942990368928>",
                "color": 0xEDC951,
                "tracker_name": "Corbeaux de Sang",
                "start": "11:00",
                "end": "09:00",
            },
            "war of the realms": {
                "name_key": "cal_ev_realms",
                "name_default": "Guerre des Royaumes",
                "emoji": "<:war_realms:1512573773658980504>",
                "color": 0xA69EB0,
                "tracker_name": "Guerre des Royaumes",
                "start": "11:00",
                "end": "09:00",
            },
            "berimond": {
                "name_key": "cal_ev_berimond",
                "name_default": "Bérimond",
                "emoji": "<:berimond:1512430901756428390>",
                "color": 0x4B86B4,
                "tracker_name": "Bataille de Bérimond",
                "start": "11:00",
                "end": "08:30",
            },
            "bladecoast": {
                "name_key": "cal_ev_bladecoast",
                "name_default": "Côte Tranchante",
                "emoji": "<:bladecoast:1514704235894407399>",
                "color": 0xBFB5B2,
                "tracker_name": None,
                "start": "11:00",
                "end": "09:00",
            },
            "rift raid": {
                "name_key": "cal_ev_rift",
                "name_default": "Raid de la Faille",
                "emoji": "<:riftraid:1514704237206966272>",
                "color": 0xFB2E01,
                "tracker_name": None,
                "start": "11:00",
                "end": "09:00",
            },
            "grand tournament": {
                "name_key": "cal_ev_tournament",
                "name_default": "Grand Tournoi",
                "emoji": "<:grandtournament:1514704234128343040>",
                "color": 0x03396C,
                "tracker_name": None,
                "start": "11:00",
                "end": "12:00",
            },
            "beyond the horizon": {
                "name_key": "cal_ev_horizon",
                "name_default": "Au-delà de l'horizon",
                "emoji": "<:bth:1512574690441302026>",
                "color": 0x006666,
                "tracker_name": None,
                "start": "11:00",
                "end": "00:40",
            },
            "outer realms": {
                "name_key": "cal_ev_outer",
                "name_default": "Royaumes extérieurs",
                "emoji": "<:outerrealmsicon:1512573734404231329>",
                "color": 0xFFE28A,
                "tracker_name": None,
                "start": "11:00",
                "end": "00:40",
            },
            "imperial patronage": {
                "name_key": "cal_ev_patronage",
                "name_default": "Patronage impérial",
                "emoji": "<:patronage:1514704230106140874>",
                "color": 0xE8702A,
                "tracker_name": None,
                "start": "11:00",
                "end": "09:30",
            },
            "grand nobility contest": {
                "name_key": "cal_ev_nobility",
                "name_default": "Grand concours de noblesse",
                "emoji": "<:ltpe:1514704228801708052>",
                "color": 0xE8702A,
                "tracker_name": None,
                "start": "11:00",
                "end": "09:00",
            },
        }

    async def load_cache_from_file(self):
        data = await load_calendrier_async()
        saved_events = data.get("cached_events", [])
        events_actifs = []
        maintenant = datetime.now()
        limite_retention = maintenant - timedelta(days=30)

        seen_uids = set()

        for ev in saved_events:
            try:
                start_dt = datetime.fromisoformat(ev["start"])
                end_dt = datetime.fromisoformat(ev["end"])

                if end_dt >= limite_retention:
                    uid = f"{ev['key']}_{int(start_dt.timestamp())}"
                    if uid not in seen_uids:
                        events_actifs.append({"key": ev["key"], "start": start_dt, "end": end_dt})
                        seen_uids.add(uid)
            except Exception as e:
                logger.error(f"❌ Erreur lecture date: {e}")

        self.cached_events = events_actifs
        logger.info(f"📂 [Calendrier] {len(self.cached_events)} événements chargés (doublons purgés).")

    async def save_cache_to_file(self):
        data = await load_calendrier_async()
        serialized = []

        for ev in self.cached_events:
            serialized.append({"key": ev["key"], "start": ev["start"].isoformat(), "end": ev["end"].isoformat()})

        data["cached_events"] = serialized
        await save_calendrier_async(data)

    async def cog_load(self):
        await self.load_cache_from_file()

        if not self.check_newshub_calendar_task.is_running():
            self.check_newshub_calendar_task.start()

    async def cog_unload(self):
        self.check_newshub_calendar_task.cancel()

    @app_commands.command(name="setup", description="Defines the room where calendar alerts will be sent")
    @app_commands.describe(channel="The text-based event lounge")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def c_setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)

        langue, serveur = await get_server_config(interaction)

        data = await load_calendrier_async()
        guild_id = str(interaction.guild_id)

        if guild_id not in data["guilds"]:
            data["guilds"][guild_id] = {"channel_id": None, "tracked_alliances": []}

        data["guilds"][guild_id]["channel_id"] = channel.id
        data["guilds"][guild_id]["gge_server"] = serveur
        data["guilds"][guild_id]["langue"] = langue

        await save_calendrier_async(data)

        msg = t(
            langue,
            "cal_setup_success",
            salon=channel.mention,
            defaut=f"✅ Le salon des alertes a été défini sur {channel.mention} pour le serveur **{serveur}**.",
        )
        await interaction.followup.send(msg)

    @app_commands.command(name="track", description="Adds an alliance to the automatic end-of-event report")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    @app_commands.describe(alliance_name="Name of the alliance to follow")
    @app_commands.guild_only()
    async def c_track(self, interaction: discord.Interaction, alliance_name: str):
        await interaction.response.defer(ephemeral=True)
        langue, _ = await get_server_config(interaction)

        data = await load_calendrier_async()
        guild_id = str(interaction.guild_id)

        if guild_id not in data["guilds"]:
            data["guilds"][guild_id] = {"channel_id": None, "tracked_alliances": []}

        tracked = data["guilds"][guild_id]["tracked_alliances"]
        if alliance_name.lower() in [a.lower() for a in tracked]:
            msg = t(
                langue,
                "cal_track_already",
                alliance=alliance_name,
                defaut=f"<:error:1512505075220611172> L'alliance **{alliance_name}** est déjà dans la liste de suivi de ce serveur.",
            )
            return await interaction.followup.send(msg)

        tracked.append(alliance_name)
        await save_calendrier_async(data)
        msg_success = t(
            langue,
            "cal_track_success",
            alliance=alliance_name,
            defaut=f"✅ L'alliance **{alliance_name}** a été ajoutée ! Ses résultats seront envoyés à la fin des événements majeurs.",
        )
        await interaction.followup.send(msg_success)

    @app_commands.command(name="untrack", description="Remove one alliance from the end-of-event report")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    @app_commands.describe(alliance_name="Name of the alliance to be withdrawn")
    @app_commands.guild_only()
    async def c_untrack(self, interaction: discord.Interaction, alliance_name: str):
        await interaction.response.defer(ephemeral=True)
        langue, _ = await get_server_config(interaction)

        data = await load_calendrier_async()
        guild_id = str(interaction.guild_id)

        if guild_id not in data["guilds"]:
            msg_err = t(
                langue,
                "cal_untrack_no_config",
                defaut="<:error:1512505075220611172> Aucune configuration trouvée pour ce serveur.",
            )
            return await interaction.followup.send(msg_err)

        tracked = data["guilds"][guild_id]["tracked_alliances"]
        if alliance_name.lower() not in [a.lower() for a in tracked]:
            msg_not_found = t(
                langue,
                "cal_untrack_not_found",
                alliance=alliance_name,
                defaut=f"<:error:1512505075220611172> L'alliance **{alliance_name}** n'est pas dans la liste de suivi.",
            )
            return await interaction.followup.send(msg_not_found)

        data["guilds"][guild_id]["tracked_alliances"] = [a for a in tracked if a.lower() != alliance_name.lower()]
        await save_calendrier_async(data)

        msg_success = t(
            langue,
            "cal_untrack_success",
            alliance=alliance_name,
            defaut=f"❌ L'alliance **{alliance_name}** a été retirée de la liste de suivi.",
        )
        await interaction.followup.send(msg_success)

    @app_commands.command(name="stop", description="Disable calendar alerts and event reports for this server")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def c_stop(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        langue, _ = await get_server_config(interaction)

        data = await load_calendrier_async()
        guild_id = str(interaction.guild_id)

        # On vérifie s'il y a bien un salon configuré
        if guild_id in data.get("guilds", {}) and data["guilds"][guild_id].get("channel_id") is not None:
            # On supprime juste l'ID du salon (les alliances suivies sont conservées)
            data["guilds"][guild_id]["channel_id"] = None
            await save_calendrier_async(data)

            msg = t(
                langue,
                "cal_stop_success",
                defaut="🛑 **Calendrier désactivé.** Ce serveur ne recevra plus les alertes d'événements et les rapports d'alliance.",
            )
            await interaction.followup.send(msg)
        else:
            msg_fail = t(langue, "cal_stop_fail", defaut="⚠️ Le calendrier n'était pas activé sur ce serveur.")
            await interaction.followup.send(msg_fail)

    # ==========================================
    # 📆 COMMANDE CURRENT (NAVIGATION INTÉGRÉE)
    # ==========================================
    @app_commands.command(name="current", description="Displays the complete calendar of events")
    async def c_current(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        langue, _ = await get_server_config(interaction)

        events = getattr(self, "cached_events", [])

        if not events:
            msg_err = t(
                langue,
                "cal_actuelle_error",
                defaut="<:error:1512505075220611172> Le calendrier est en cours de synchronisation ou vide. Réessayez dans une minute.",
            )
            return await interaction.followup.send(msg_err)

        maintenant = datetime.now()

        events_past = []
        events_main = []
        events_future = []

        events_tries = sorted(events, key=lambda x: x["start"])

        for ev in events_tries:
            meta = self.event_mapping.get(ev["key"])
            if not meta:
                continue

            ts_start = int(ev["start"].timestamp())
            ts_end = int(ev["end"].timestamp())

            nom_event_traduit = t(langue, meta["name_key"], defaut=meta["name_default"])
            ligne = f"{meta['emoji']} **{nom_event_traduit}** : <t:{ts_start}:d> ➔ <t:{ts_end}:d>"

            if ev["end"] < maintenant:
                events_past.append(ligne)
            else:
                events_main.append(ligne)
                if ev["start"] > maintenant:
                    events_future.append(ligne)

        last_scrape = getattr(self, "last_scrape_time", None)
        ts_last_scan = int(last_scrape.timestamp()) if last_scrape else int(datetime.now().timestamp())

        texte_maj = t(
            langue,
            "cal_last_update",
            ts_last_scan=ts_last_scan,
            defaut=f"<:Information:1533430015264555099> Dernière mise à jour : <t:{ts_last_scan}:R>",
        )
        empty_txt = t(langue, "cal_empty_cat", defaut="*Aucun événement dans cette catégorie pour le moment.*")

        async def build_embed(titre, liste_lignes, color):
            desc = f"{texte_maj}\n\n" + ("\n".join(liste_lignes) if liste_lignes else empty_txt)
            emb = discord.Embed(title=titre, description=desc, color=color)
            await setup_embed_footer(emb, interaction, langue)
            return emb

        embeds_dict = {
            "past": await build_embed(
                t(langue, "cal_title_past", defaut="⏳ Événements Passés (30 derniers jours)"), events_past, 0x95A5A6
            ),
            "main": await build_embed(
                t(langue, "cal_title_main", defaut="📅 Événements Actuels & À venir"), events_main, 0x3498DB
            ),
            "future": await build_embed(
                t(langue, "cal_title_future", defaut="🚀 Événements Futurs Uniquement"), events_future, 0x2ECC71
            ),
        }

        view = CalendarNavView(embeds_dict, "main", langue)
        await interaction.followup.send(embed=embeds_dict["main"], view=view)

    # ==========================================
    # 🕵️‍♂️ MOTEUR D'EXTRACTION HTML (BS4 LECTURE PLATE)
    # ==========================================
    async def parse_live_calendar(self):
        url = "https://communityhub.goodgamestudios.com/newshube4k"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

        try:
            async with self.bot.session.get(url, headers=headers, timeout=15) as r:
                if r.status != 200:
                    return []

                html_content = await r.text()
                soup = BeautifulSoup(html_content, "html.parser")

                found_events = []
                seen_signatures = set()
                current_year = datetime.now().year

                strings = list(soup.stripped_strings)
                current_event = None

                for text in strings:
                    text_lower = text.lower()

                    matched_key = next((key for key in self.event_mapping.keys() if key in text_lower), None)
                    if matched_key:
                        current_event = matched_key

                    if current_event:
                        matches = re.findall(r"(\d{2}[/.-]\d{2})\s*(?:-|à|to|au)\s*(\d{2}[/.-]\d{2})", text_lower)
                        for start_str, end_str in matches:
                            try:
                                start_str = start_str.replace(".", "/").replace("-", "/")
                                end_str = end_str.replace(".", "/").replace("-", "/")

                                h_start = self.event_mapping[current_event]["start"]
                                h_end = self.event_mapping[current_event]["end"]

                                start_dt = datetime.strptime(f"{start_str}/{current_year} {h_start}", "%d/%m/%Y %H:%M")
                                end_dt = datetime.strptime(f"{end_str}/{current_year} {h_end}", "%d/%m/%Y %H:%M")

                                if end_dt < start_dt:
                                    end_dt = end_dt.replace(year=current_year + 1)

                                uid = f"{current_event}_{int(start_dt.timestamp())}"
                                if uid not in seen_signatures:
                                    found_events.append({"key": current_event, "start": start_dt, "end": end_dt})
                                    seen_signatures.add(uid)
                            except Exception:
                                continue

                return found_events
        except Exception as e:
            logger.error(f"❌ [Calendrier] Erreur BS4 : {e}")
            return []

    @tasks.loop(minutes=1)
    async def check_newshub_calendar_task(self):
        # Garde-fou obligatoire : discord.py arrête définitivement une tasks.loop
        # sur exception non rattrapée, sans jamais la relancer. Une réponse
        # inattendue de l'API (points à null, date malformée) suffisait à couper
        # les alertes d'événements pour TOUS les serveurs jusqu'au redémarrage
        try:
            await self._run_calendar_check()
        except Exception:
            logger.error(f"❌ [CALENDRIER CRASH] : {traceback.format_exc()}")

    async def _run_calendar_check(self):
        maintenant = datetime.now()

        if (
            getattr(self, "last_scrape_time", None) is None
            or (maintenant - self.last_scrape_time).total_seconds() > 7200
        ):
            nouveaux_events = await self.parse_live_calendar()

            limite_retention = maintenant - timedelta(days=30)
            events_actifs = [ev for ev in getattr(self, "cached_events", []) if ev["end"] >= limite_retention]

            if nouveaux_events:
                ids_existants = {f"{ev['key']}_{int(ev['start'].timestamp())}" for ev in events_actifs}

                for nev in nouveaux_events:
                    uid_nev = f"{nev['key']}_{int(nev['start'].timestamp())}"
                    if uid_nev not in ids_existants:
                        events_actifs.append(nev)
                        ids_existants.add(uid_nev)

            self.cached_events = events_actifs
            self.last_scrape_time = maintenant

            await self.save_cache_to_file()
            logger.info(
                f"📝 [Calendrier] Cache mis à jour et sauvegardé : {len(self.cached_events)} événements actifs/récents."
            )

        events = getattr(self, "cached_events", [])
        if not events:
            return

        data = await load_calendrier_async()
        notified = data.get("notified", [])
        modifie = False

        for ev in events:
            meta = self.event_mapping[ev["key"]]

            uid_start = f"{ev['key']}_{ev['start'].strftime('%Y-%m-%d')}_start"
            uid_end = f"{ev['key']}_{ev['end'].strftime('%Y-%m-%d')}_end"

            # 🟢 DÉBUT D'ÉVÉNEMENT
            if maintenant >= ev["start"] and uid_start not in notified:
                # On limite l'envoi Discord ET les logs aux événements d'aujourd'hui !
                if ev["start"].date() == maintenant.date():
                    logger.info(f"🔎 [Calendrier] DÉCLENCHEMENT DÉBUT de {meta['name_default']}")
                    ts_fin = int(ev["end"].timestamp())

                    for guild_id, g_info in data.get("guilds", {}).items():
                        channel_id = g_info.get("channel_id")
                        if channel_id:
                            channel = self.bot.get_channel(channel_id)
                            if not channel:
                                try:
                                    channel = await self.bot.fetch_channel(channel_id)
                                except:
                                    pass

                            if channel:
                                langue = g_info.get("langue", "fr")
                                serveur_cible = g_info.get("gge_server", "E4K_FR1")

                                nom_traduit = t(langue, meta["name_key"], defaut=meta["name_default"])

                                titre_start = t(
                                    langue,
                                    "cal_event_start_title",
                                    emoji=meta["emoji"],
                                    name=nom_traduit,
                                    defaut=f"{meta['emoji']} DÉBUT D'ÉVÉNEMENT : {nom_traduit}",
                                )
                                desc_start = t(
                                    langue,
                                    "cal_event_start_desc",
                                    ts_fin=ts_fin,
                                    defaut=f"L'heure a sonné ! Un nouvel événement vient d'ouvrir ses portes sur nos terres.\n\n⏳ **Fermeture prévue** : <t:{ts_fin}:f> (<t:{ts_fin}:R>)",
                                )

                                embed_start = discord.Embed(
                                    title=titre_start, description=desc_start, color=meta["color"]
                                )
                                await setup_embed_footer(embed_start, None, langue)

                                try:
                                    await channel.send(embed=embed_start)
                                except:
                                    pass

                # L'ajout à la mémoire se fait dans tous les cas pour ignorer les vieux événements
                notified.append(uid_start)
                modifie = True

            # 🔴 FIN D'ÉVÉNEMENT
            if maintenant >= ev["end"] and uid_end not in notified:
                # On limite l'envoi Discord ET les logs aux événements d'aujourd'hui !
                if ev["end"].date() == maintenant.date():
                    logger.info(f"🔎 [Calendrier] DÉCLENCHEMENT FIN de {meta['name_default']}")

                    for guild_id, g_info in data.get("guilds", {}).items():
                        channel_id = g_info.get("channel_id")
                        if channel_id:
                            channel = self.bot.get_channel(channel_id)
                            if not channel:
                                try:
                                    channel = await self.bot.fetch_channel(channel_id)
                                except:
                                    pass

                            if channel:
                                langue = g_info.get("langue", "fr")
                                serveur_cible = g_info.get("gge_server", "E4K_FR1")

                                nom_traduit = t(langue, meta["name_key"], defaut=meta["name_default"])

                                titre_end = t(
                                    langue,
                                    "cal_event_end_title",
                                    name=nom_traduit,
                                    defaut=f"🛑 FIN D'ÉVÉNEMENT : {nom_traduit}",
                                )
                                desc_end = t(
                                    langue,
                                    "cal_event_end_desc",
                                    defaut="Le calme revient sur le serveur. L'événement est officiellement terminé !",
                                )

                                embed_end = discord.Embed(title=titre_end, description=desc_end, color=0x2E4045)
                                await setup_embed_footer(embed_end, None, langue)
                                try:
                                    await channel.send(embed=embed_end)
                                except:
                                    pass

                                tracked = g_info.get("tracked_alliances", [])
                                if tracked and meta["tracker_name"]:
                                    event_keys = TRACKER_EVENTS.get(meta["tracker_name"])
                                    if event_keys:
                                        for alliance_nom in tracked:
                                            embed_rapport, error, _, _ = await generer_rapport_alliance_embed(
                                                self.bot,
                                                nom_traduit,
                                                event_keys,
                                                alliance_nom,
                                                meta["color"],
                                                custom_server=serveur_cible,
                                            )
                                            if embed_rapport:
                                                await setup_embed_footer(embed_rapport, None, langue)
                                                try:
                                                    await channel.send(embed=embed_rapport)
                                                except:
                                                    pass
                                            else:
                                                err_msg = t(
                                                    langue,
                                                    "cal_report_error",
                                                    alliance=alliance_nom,
                                                    error=error,
                                                    defaut=f"⚠️ Erreur de rapport pour **{alliance_nom}** : {error}",
                                                )
                                                try:
                                                    await channel.send(err_msg)
                                                except:
                                                    pass

                # L'ajout à la mémoire se fait dans tous les cas pour ignorer les vieux événements
                notified.append(uid_end)
                modifie = True

        if modifie:
            # 💡 LE FIX EST ICI : On passe la mémoire de 60 à 500 événements !
            if len(notified) > 500:
                notified = notified[-500:]
            data["notified"] = notified
            await save_calendrier_async(data)

    @check_newshub_calendar_task.before_loop
    async def before_check_newshub_calendar_task(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(CalendrierCog(bot))
