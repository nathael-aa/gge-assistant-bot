import json
import logging
import os
import urllib.parse
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import (
    DICT_EMOJIS,
    SERVEURS_DIR,
    format_num,
    get_api_headers,
    get_server_config,
    joueur_autocomplete,
    prompt_vote_if_lucky,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")

STORM_CONFIG_PATH = SERVEURS_DIR / "storm_alerts.json"


async def load_storm_config():
    if not os.path.exists(STORM_CONFIG_PATH):
        return {"guilds": {}, "notified": []}
    try:
        with open(STORM_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"guilds": {}, "notified": []}


async def save_storm_config(data):
    os.makedirs(os.path.dirname(STORM_CONFIG_PATH), exist_ok=True)
    with open(STORM_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ===========================================
# ⚙️ CONFIGURATION ID DE L'API STORM
# ===========================================
FORT_LEVELS_MAPPING = {
    10: {"lvl": 40, "desc": "40"},
    11: {"lvl": 50, "desc": "50"},
    7: {"lvl": 60, "desc": "60"},
    12: {"lvl": 60, "desc": f"60 {DICT_EMOJIS.get('e_std_shield', '🛡️')}"},
    8: {"lvl": 70, "desc": "70"},
    13: {"lvl": 70, "desc": f"70 {DICT_EMOJIS.get('e_std_shield', '🛡️')}"},
    9: {"lvl": 80, "desc": "80"},
    14: {"lvl": 80, "desc": f"80 {DICT_EMOJIS.get('e_std_shield', '🛡️')}"},
}

ISLE_RESOURCE_MAPPING = {
    1: {"key": "storm_res_wood", "def": "Wood", "qty": f"40,000{DICT_EMOJIS.get('e_wood', '🪵')}"},
    4: {"key": "storm_res_wood", "def": "Wood", "qty": f"20,000{DICT_EMOJIS.get('e_wood', '🪵')}"},
    2: {"key": "storm_res_stone", "def": "Stone", "qty": f"40,000{DICT_EMOJIS.get('e_stone', '🪨')}"},
    5: {"key": "storm_res_stone", "def": "Stone", "qty": f"20,000{DICT_EMOJIS.get('e_stone', '🪨')}"},
    3: {"key": "storm_res_aqua", "def": "Aquamarine", "qty": f"52,000{DICT_EMOJIS.get('e_aquamarine_brut', '💎')}"},
    6: {"key": "storm_res_aqua", "def": "Aquamarine", "qty": f"11,500{DICT_EMOJIS.get('e_aquamarine_brut', '💎')}"},
}


def get_isle_name(isle_id, langue):
    res_data = ISLE_RESOURCE_MAPPING.get(isle_id)
    if not res_data:
        return t(langue, "storm_res_unknown", defaut="Unknown Island")
    name = t(langue, res_data["key"], defaut=res_data["def"])
    return f"{name} ({res_data['qty']})"


# ==========================================
# 🎛️ COMPOSANT UI : VUES LOCALES DE PAGINATION
# ==========================================
class RefreshOnlyView(discord.ui.View):
    def __init__(self, callback_func, langue="fr", timeout=3600):
        super().__init__(timeout=timeout)
        self.message = None

        self.refresh_btn = discord.ui.Button(
            style=discord.ButtonStyle.primary,
            emoji=DICT_EMOJIS.get("e_refresh", "🔄"),
            label=t(langue, "btn_refresh", defaut="Refresh"),
        )
        self.refresh_btn.callback = callback_func
        self.add_item(self.refresh_btn)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class LocalPaginationView(discord.ui.View):
    def __init__(self, embeds, timeout=3600):
        super().__init__(timeout=timeout)
        self.embeds = embeds
        self.current_page = 0
        self.message = None

        self.btn_prev.emoji = DICT_EMOJIS.get("e_last", "⏮️")
        self.btn_next.emoji = DICT_EMOJIS.get("e_next", "⏭️")

        self.update_buttons()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    def update_buttons(self):
        self.btn_prev.disabled = self.current_page == 0
        self.btn_next.disabled = self.current_page == len(self.embeds) - 1

    @discord.ui.button(style=discord.ButtonStyle.secondary, custom_id="page_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        if not self.message:
            self.message = interaction.message
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(style=discord.ButtonStyle.secondary, custom_id="page_next")
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        if not self.message:
            self.message = interaction.message
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)


class StormsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.clr_forts = discord.Color.from_rgb(139, 196, 191)
        self.clr_isles = discord.Color.from_rgb(211, 240, 227)
        self.clr_occupier = discord.Color.from_rgb(137, 196, 199)
        self.clr_status = discord.Color.from_rgb(132, 206, 209)
        self.clr_setup = discord.Color.from_rgb(144, 232, 219)
        self.api_base = "https://api.gge-tracker.com/api/v1/"

        self.active_alerts = []

    # Création du groupe de commande principal /storm
    storm_group = app_commands.Group(name="storm", description="Commands for the Storm Islands event")

    # ========================================================
    # ⚔️ COMMANDE : /storm forts
    # ========================================================
    @storm_group.command(name="forts", description="Search for storm forts based on your criteria")
    @app_commands.choices(
        availability=[
            app_commands.Choice(name="Available Now", value=1),
            app_commands.Choice(name="In < 5 mins", value=2),
            app_commands.Choice(name="In < 1 hour", value=3),
            app_commands.Choice(name="All", value=0),
        ]
    )
    @app_commands.describe(
        availability="When the fort will be attackable (Default: Available Now)",
        lvl40="Include level 40 forts",
        lvl50="Include level 50 forts",
        lvl60="Include level 60 forts",
        lvl70="Include level 70 forts",
        lvl80="Include level 80 forts",
        min_attacks="Minimum attacks left (0-10)",
    )
    async def storm_forts(
        self,
        interaction: discord.Interaction,
        availability: int = 1,
        lvl40: bool = False,
        lvl50: bool = False,
        lvl60: bool = False,
        lvl70: bool = False,
        lvl80: bool = False,
        min_attacks: app_commands.Range[int, 0, 10] = 0,
    ):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

        async def fetch_and_build_view(current_inter: discord.Interaction, is_refresh: bool):
            isle_ids = []
            if lvl40:
                isle_ids.append(10)
            if lvl50:
                isle_ids.append(11)
            if lvl60:
                isle_ids.extend([7, 12])
            if lvl70:
                isle_ids.extend([8, 13])
            if lvl80:
                isle_ids.extend([9, 14])

            if not isle_ids:
                isle_ids = [7, 8, 9, 10, 11, 12, 13, 14]

            params = {
                "page": 1,
                "size": 500,
                "minAttacksLeft": min_attacks,
                "filterByIsleIds": f"[{','.join(map(str, isle_ids))}]",
                "orderDirection": "desc",
            }

            if availability != 0:
                params["filterByAvailability"] = availability

            url = f"{self.api_base}/storms/forts"
            async with self.bot.session.get(url, headers=headers, params=params, timeout=15) as r:
                if r.status != 200:
                    msg = t(
                        langue,
                        "cmd_storm_api_err",
                        defaut="{e_error} Error connecting to the Storms API.",
                    )
                    return (
                        await current_inter.followup.send(msg, ephemeral=True)
                        if is_refresh
                        else await current_inter.followup.send(msg)
                    )
                data = await r.json()

            forts = data.get("forts", [])
            if not forts:
                msg = t(
                    langue,
                    "cmd_storm_no_forts",
                    defaut="{e_information} No forts match your criteria.",
                )
                return (
                    await current_inter.followup.send(msg, ephemeral=True)
                    if is_refresh
                    else await current_inter.followup.send(msg)
                )

            forts_filtres = []
            for fort in forts:
                fort_info = FORT_LEVELS_MAPPING.get(fort.get("isle_id"))
                if fort_info:
                    forts_filtres.append((fort_info["desc"], fort))

            if availability in [2, 3]:

                def sort_by_spawn(item):
                    desc, fort = item
                    raw_time = fort.get("available_at", "")
                    try:
                        dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                        ts = int(dt.timestamp())
                    except:
                        ts = 9999999999
                    return (ts, -fort.get("isle_id", 0))

                forts_filtres.sort(key=sort_by_spawn)
            else:
                forts_filtres.sort(key=lambda x: (x[0], x[1].get("attacks_left", 0)), reverse=True)

            embeds = []
            items_par_page = 15
            total_pages = (len(forts_filtres) - 1) // items_par_page + 1
            titre_base = t(langue, "cmd_storm_forts_title", defaut="{e_aquamarineforts} Available Storm Forts")

            lbl_refreshed = t(langue, "storm_last_refreshed", defaut="Last refreshed:")
            last_update = f"\n*{{e_time}} {lbl_refreshed} <t:{int(datetime.now().timestamp())}:T>*"
            lbl_att_left = t(langue, "storm_att_left", defaut="att. left")

            for i in range(0, len(forts_filtres), items_par_page):
                page_items = forts_filtres[i : i + items_par_page]
                numero_page = (i // items_par_page) + 1

                embed = discord.Embed(title=f"{titre_base} (Page {numero_page}/{total_pages})", color=self.clr_forts)

                lignes_description = []
                for desc, fort in page_items:
                    attaques = fort.get("attacks_left", 0)
                    x, y = fort.get("position_x", 0), fort.get("position_y", 0)

                    time_str = ""
                    if availability in [2, 3]:
                        raw_time = fort.get("available_at", "")
                        try:
                            dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                            time_str = f" | {{e_time}} <t:{int(dt.timestamp())}:R>"
                        except:
                            pass

                    lignes_description.append(
                        f"**Lvl. {desc}** | {{e_compass}} `({x}:{y})` | {{e_attaque}} {attaques} {lbl_att_left}{time_str}"
                    )

                embed.description = "\n".join(lignes_description) + "\n" + last_update
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

            async def refresh_callback(btn_inter: discord.Interaction):
                await btn_inter.response.defer()
                await fetch_and_build_view(btn_inter, is_refresh=True)

            if len(embeds) > 1:
                view = LocalPaginationView(embeds)
                refresh_btn = discord.ui.Button(
                    style=discord.ButtonStyle.primary,
                    emoji=DICT_EMOJIS.get("e_refresh", "🔄"),
                    label=t(langue, "btn_refresh", defaut="Refresh"),
                )
                refresh_btn.callback = refresh_callback
                view.add_item(refresh_btn)
            else:
                view = RefreshOnlyView(refresh_callback, langue)

            if is_refresh:
                await current_inter.edit_original_response(embed=embeds[0], view=view)
            else:
                view.message = await current_inter.followup.send(embed=embeds[0], view=view, wait=True)
                await prompt_vote_if_lucky(interaction, probability_percent=15, langue=langue)

        await fetch_and_build_view(interaction, is_refresh=False)

    # ========================================================
    # 🏝️ COMMANDE : /storm isles
    # ========================================================
    @storm_group.command(name="isles", description="Search for resource islands in the Storm Islands")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="All", value=0),
            app_commands.Choice(name="Free (Ready to capture)", value=1),
            app_commands.Choice(name="Occupied", value=2),
            app_commands.Choice(name="Respawning (Sunk)", value=3),
        ],
        resource=[
            app_commands.Choice(name="All", value=0),
            app_commands.Choice(name="Aquamarine", value=1),
            app_commands.Choice(name="Wood", value=2),
            app_commands.Choice(name="Stone", value=3),
        ],
    )
    async def storm_isles(
        self, interaction: discord.Interaction, status: app_commands.Choice[int], resource: app_commands.Choice[int]
    ):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

        async def fetch_and_build_view(current_inter: discord.Interaction, is_refresh: bool):
            params = {
                "page": 1,
                "size": 500,
            }
            if status.value != 0:
                params["filterByState"] = status.value

            url = f"{self.api_base}/storms/isles"
            async with self.bot.session.get(url, headers=headers, params=params, timeout=15) as r:
                if r.status != 200:
                    msg = t(langue, "cmd_storm_api_err", defaut="{e_error} Error connecting to the Storms API.")
                    return (
                        await current_inter.followup.send(msg, ephemeral=True)
                        if is_refresh
                        else await current_inter.followup.send(msg)
                    )
                data = await r.json()

            isles = data.get("isles", [])

            if resource.value == 1:
                allowed_isles = [3, 6]
            elif resource.value == 2:
                allowed_isles = [1, 4]
            elif resource.value == 3:
                allowed_isles = [2, 5]
            else:
                allowed_isles = []

            if allowed_isles:
                isles = [i for i in isles if i.get("isle_id") in allowed_isles]

            if not isles:
                msg = t(langue, "cmd_storm_no_isles", defaut="{e_information} No islands match your criteria.")
                return (
                    await current_inter.followup.send(msg, ephemeral=True)
                    if is_refresh
                    else await current_inter.followup.send(msg)
                )

            if status.value == 3:

                def sort_by_spawn(isle):
                    raw_time = isle.get("available_at", "")
                    try:
                        dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                        return int(dt.timestamp())
                    except:
                        return 9999999999

                isles.sort(key=sort_by_spawn)
            else:
                isles.sort(key=lambda x: x.get("isle_id", 0))

            embeds = []
            items_par_page = 15
            total_pages = (len(isles) - 1) // items_par_page + 1

            titre_base = t(langue, "cmd_storm_isles_title", defaut="{e_aquamarineiles} Storm Islands")
            lbl_refreshed = t(langue, "storm_last_refreshed", defaut="Last refreshed:")
            last_update = f"\n*{{e_refresh}} {lbl_refreshed} <t:{int(datetime.now().timestamp())}:T>*"

            lbl_free = t(langue, "storm_isle_free", defaut="Free!")
            lbl_respawns = t(langue, "storm_isle_respawns", defaut="Respawns:")
            lbl_unknown_state = t(langue, "storm_isle_unknown", defaut="State unknown")
            lbl_unknown_player = t(langue, "storm_unknown_player", defaut="Unknown")
            lbl_none_alliance = t(langue, "storm_none_alliance", defaut="None")

            for i in range(0, len(isles), items_par_page):
                page_items = isles[i : i + items_par_page]
                numero_page = (i // items_par_page) + 1

                embed = discord.Embed(title=f"{titre_base} (Page {numero_page}/{total_pages})", color=self.clr_isles)

                lignes_description = []
                for isle in page_items:
                    x, y = isle.get("position_x"), isle.get("position_y")
                    res_nom = get_isle_name(isle.get("isle_id"), langue)
                    etat = isle.get("state")

                    if etat == 0:
                        ligne = f"{{e_greencirclebullet}} **{res_nom}** | {{e_compass}} `({x}:{y})` | *{lbl_free}*"
                    elif etat == 1:
                        occupant = isle.get("occupier_name") or lbl_unknown_player
                        alliance = isle.get("occupier_alliance_name") or lbl_none_alliance
                        ligne = f"{{e_std_red_circle}} **{res_nom}** | {{e_compass}} `({x}:{y})` | {{e_std_shield}} {occupant} (*{alliance}*)"
                    elif etat == 2:
                        raw_time = isle.get("available_at", "")
                        try:
                            dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                            ts = int(dt.timestamp())
                            time_str = f"<t:{ts}:R>"
                        except:
                            time_str = t(langue, "storm_isle_soon", defaut="Soon")
                        ligne = f"{{e_time}} **{res_nom}** | {{e_compass}} `({x}:{y})` | {lbl_respawns} {time_str}"
                    else:
                        ligne = f"{{e_information}} **{res_nom}** | {{e_compass}} `({x}:{y})` | {lbl_unknown_state}"

                    lignes_description.append(ligne)

                embed.description = "\n".join(lignes_description) + "\n" + last_update
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

            async def refresh_callback(btn_inter: discord.Interaction):
                await btn_inter.response.defer()
                await fetch_and_build_view(btn_inter, is_refresh=True)

            if len(embeds) > 1:
                view = LocalPaginationView(embeds)
                refresh_btn = discord.ui.Button(
                    style=discord.ButtonStyle.primary,
                    emoji=DICT_EMOJIS.get("e_refresh", "🔄"),
                    label=t(langue, "btn_refresh", defaut="Refresh"),
                )
                refresh_btn.callback = refresh_callback
                view.add_item(refresh_btn)
            else:
                view = RefreshOnlyView(refresh_callback, langue)

            if is_refresh:
                await current_inter.edit_original_response(embed=embeds[0], view=view)
            else:
                view.message = await current_inter.followup.send(embed=embeds[0], view=view, wait=True)
                await prompt_vote_if_lucky(interaction, probability_percent=15, langue=langue)

        await fetch_and_build_view(interaction, is_refresh=False)

    # ========================================================
    # 🕵️‍♂️ COMMANDE : /storm occupier
    # ========================================================
    @storm_group.command(name="occupier", description="List all islands currently held by a specific player")
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.describe(player="Exact player name")
    async def storm_occupier(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

        safe_joueur = urllib.parse.quote(player)

        async def fetch_and_build_view(current_inter: discord.Interaction, is_refresh: bool):
            url = f"{self.api_base}/storms/isles?filterByOccupierName={safe_joueur}&size=4000"

            async with self.bot.session.get(url, headers=headers, timeout=15) as r:
                if r.status != 200:
                    msg = t(langue, "cmd_storm_api_err", defaut="{e_error} Error connecting to the Storms API.")
                    return (
                        await current_inter.followup.send(msg, ephemeral=True)
                        if is_refresh
                        else await current_inter.followup.send(msg)
                    )
                data = await r.json()

            isles = data.get("isles", [])
            if not isles:
                msg = t(
                    langue,
                    "cmd_storm_no_occupier",
                    player=player,
                    defaut=f"{{e_information}} The player **{player}** does not currently hold any resource islands.",
                )
                return (
                    await current_inter.followup.send(msg, ephemeral=True)
                    if is_refresh
                    else await current_inter.followup.send(msg)
                )

            # --- PAGINATION ---
            embeds = []
            items_par_page = 20
            total_pages = (len(isles) - 1) // items_par_page + 1

            titre_base = t(
                langue, "cmd_storm_occupier_title", player=player, defaut=f"{{e_players}} Islands held by {player}"
            )
            desc_total = t(
                langue, "storm_occ_total", count=len(isles), defaut=f"Total islands under control: **{len(isles)}**"
            )

            lbl_refreshed = t(langue, "storm_last_refreshed", defaut="Last refreshed:")
            last_update = f"\n*{{e_refresh}} {lbl_refreshed} <t:{int(datetime.now().timestamp())}:T>*"

            for i in range(0, len(isles), items_par_page):
                page_items = isles[i : i + items_par_page]
                numero_page = (i // items_par_page) + 1

                embed = discord.Embed(
                    title=f"{titre_base} (Page {numero_page}/{total_pages})",
                    description=f"{desc_total}\n\n",
                    color=self.clr_occupier,
                )

                lignes_description = []
                for isle in page_items:
                    x, y = isle.get("position_x"), isle.get("position_y")
                    res_nom = get_isle_name(isle.get("isle_id"), langue)
                    lignes_description.append(f"{{e_compass}} `({x}:{y})` | {res_nom}")

                # On ajoute toutes les îles à la description, puis la ligne de mise à jour à la fin
                embed.description += "\n".join(lignes_description) + "\n" + last_update
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

            async def refresh_callback(btn_inter: discord.Interaction):
                await btn_inter.response.defer()
                await fetch_and_build_view(btn_inter, is_refresh=True)

            if len(embeds) > 1:
                view = LocalPaginationView(embeds)
                refresh_btn = discord.ui.Button(
                    style=discord.ButtonStyle.primary,
                    emoji=DICT_EMOJIS.get("e_refresh", "🔄"),
                    label=t(langue, "btn_refresh", defaut="Refresh"),
                )
                refresh_btn.callback = refresh_callback
                view.add_item(refresh_btn)
            else:
                view = RefreshOnlyView(refresh_callback, langue)

            if is_refresh:
                await current_inter.edit_original_response(embed=embeds[0], view=view)
            else:
                view.message = await current_inter.followup.send(embed=embeds[0], view=view, wait=True)
                await prompt_vote_if_lucky(interaction, probability_percent=5, langue=langue)

        await fetch_and_build_view(interaction, is_refresh=False)

    # ========================================================
    # 📡 COMMANDE : /storm status
    # ========================================================
    @storm_group.command(name="status", description="Displays the freshness state of the Storm Islands map scan")
    async def storm_status(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

        url = f"{self.api_base}/storms/meta"
        async with self.bot.session.get(url, headers=headers, timeout=10) as r:
            if r.status != 200:
                return await interaction.followup.send(
                    t(
                        langue,
                        "cmd_storm_api_err",
                        defaut="{e_error} Error connecting to the Storms API.",
                    )
                )
            data = await r.json()

        # 1. Parsing de la date du dernier scan
        last_scan = data.get("last_scan_at", "")
        try:
            ts_scan = int(datetime.fromisoformat(last_scan.replace("Z", "+00:00")).timestamp())
            scan_str = f"<t:{ts_scan}:R>"
        except:
            scan_str = t(langue, "storm_status_unknown", defaut="Unknown")

        # 2. Parsing de la date de début de saison
        season_start = data.get("season_started_at", "")
        try:
            ts_season = int(datetime.fromisoformat(season_start.replace("Z", "+00:00")).timestamp())
            season_str = f"<t:{ts_season}:D>"  # Format date courte Discord (ex: 1 August 2026)
        except:
            season_str = t(langue, "storm_status_unknown", defaut="Unknown")

        embed = discord.Embed(
            title=t(langue, "cmd_storm_status_title", defaut="{e_std_satellite} Storm Islands Status"),
            color=self.clr_status,
        )

        desc = t(
            langue,
            "storm_status_desc_v2",
            season=season_str,
            scan=scan_str,
            radius=data.get("scan_radius", 0),
            forts=format_num(data.get("forts_count", 0)),
            isles=format_num(data.get("isles_count", 0)),
            defaut=(
                f"**Season Started:** {season_str}\n"
                f"**Last Scan:** {scan_str}\n"
                f"**Covered Radius:** {data.get('scan_radius', 0)} tiles\n\n"
                f"{{e_aquamarineforts}} **Tracked Forts:** {format_num(data.get('forts_count', 0))}\n"
                f"{{e_aquamarineiles}} **Tracked Isles:** {format_num(data.get('isles_count', 0))}"
            ),
        )

        embed.description = desc
        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed)

    # ========================================================
    # ⚙️ COMMANDE : /storm setup
    # ========================================================
    @storm_group.command(name="setup", description="Configure automatic alerts for respawning islands")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        channel="The channel where notifications will be sent",
        ping_small="Ping for small islands (Default: False)",
        ping_big="Ping for big islands (Default: False)",
        role="Role to ping (Leave empty to use @here if a ping is active)",
    )
    async def storm_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        ping_small: bool = False,
        ping_big: bool = False,
        role: discord.Role = None,
    ):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)

        bot_permissions = channel.permissions_for(interaction.guild.me)
        if not bot_permissions.send_messages or not bot_permissions.embed_links:
            msg = t(
                langue,
                "cmd_storm_setup_perms",
                defaut=f"{{e_error}} I need permissions to send messages and embed links in {channel.mention}.",
            )
            return await interaction.followup.send(msg)

        ping_format = role.mention if role else "@here"

        data = await load_storm_config()
        if "guilds" not in data:
            data["guilds"] = {}

        data["guilds"][str(interaction.guild.id)] = {
            "channel_id": channel.id,
            "ping_small": ping_small,
            "ping_big": ping_big,
            "ping_role": ping_format,
            "gge_server": serveur,
            "langue": langue,
        }
        await save_storm_config(data)

        desc = t(
            langue,
            "storm_setup_desc",
            channel=channel.mention,
            defaut=f"Notifications will be sent in {channel.mention}.",
        )
        embed = discord.Embed(
            title=t(
                langue,
                "cmd_storm_setup_title",
                defaut="{e_greencirclebullet} Storm Islands Alerts Configured!",
            ),
            color=self.clr_setup,
            description=desc,
        )

        val_yes = t(langue, "storm_setup_yes", defaut="Yes")
        val_no = t(langue, "storm_setup_no", defaut="No")
        val_no_mention = t(langue, "storm_setup_no_mention", defaut="No mention")

        etat_big = f"{{e_greencirclebullet}} {val_yes}" if ping_big else f"{{e_tomatobulletpoint}} {val_no}"
        etat_small = f"{{e_greencirclebullet}} {val_yes}" if ping_small else f"{{e_tomatobulletpoint}} {val_no}"
        mention_txt = ping_format if (ping_big or ping_small) else val_no_mention

        embed.add_field(name=t(langue, "storm_setup_field_big", defaut="Ping Big Isles"), value=etat_big, inline=True)
        embed.add_field(
            name=t(langue, "storm_setup_field_small", defaut="Ping Small Isles"), value=etat_small, inline=True
        )
        embed.add_field(name=t(langue, "storm_setup_field_mention", defaut="Mention"), value=mention_txt, inline=True)

        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed)

    def cog_load(self):
        self.storm_alert_loop.start()

    def cog_unload(self):
        self.storm_alert_loop.cancel()

    @tasks.loop(minutes=1.0)
    async def storm_alert_loop(self):
        now = datetime.now().timestamp()

        data = await load_storm_config()
        guilds_config = data.get("guilds", {})
        notified = data.get("notified", [])

        if not guilds_config:
            return

        servers_to_check = {}
        for guild_id_str, config in guilds_config.items():
            gge_server = config.get("gge_server", "E4K_FR1")
            if gge_server not in servers_to_check:
                servers_to_check[gge_server] = []
            servers_to_check[gge_server].append((guild_id_str, config))

        # 1️⃣ Mémoire : Identifier les serveurs qui ont des îles "Apparues" en attente de capture
        servers_with_spawned = {
            alert["gge_server"]
            for alert in self.active_alerts
            if now >= alert["ts"] and alert.get("status", "pending") in ["pending", "spawned"]
        }

        modifie = False
        total_annoncailles = 0
        occupied_isles_by_server = {}

        # 2️⃣ Fetch API : On récupère les respawns et les occupations intelligemment
        for gge_server, guilds_list in servers_to_check.items():
            headers = await get_api_headers(custom_server=gge_server)

            # A) Îles en phase de Respawn (filterByState=3)
            params_respawning = {"size": 4000, "filterByState": 3}
            isles_respawning = []
            try:
                async with self.bot.session.get(
                    f"{self.api_base}storms/isles", headers=headers, params=params_respawning, timeout=15
                ) as r:
                    if r.status == 200:
                        api_data = await r.json()
                        isles_respawning = api_data.get("isles", [])
            except Exception as e:
                logger.error(f"❌ [Storm Alerts] Erreur API Respawning pour {gge_server} : {e}")

            # B) Îles Occupées (filterByState=2) - UNIQUEMENT si on en a besoin (économie de requêtes)
            if gge_server in servers_with_spawned:
                params_occupied = {"size": 4000, "filterByState": 2}
                try:
                    async with self.bot.session.get(
                        f"{self.api_base}storms/isles", headers=headers, params=params_occupied, timeout=15
                    ) as r:
                        if r.status == 200:
                            occ_data = await r.json()
                            occupied_isles_by_server[gge_server] = {
                                (isle.get("position_x"), isle.get("position_y")): isle
                                for isle in occ_data.get("isles", [])
                            }
                except Exception as e:
                    logger.error(f"❌ [Storm Alerts] Erreur API Occupied pour {gge_server} : {e}")

            # C) Création des nouvelles alertes pour les îles qui vont spawn
            isles_to_announce = []
            for isle in isles_respawning:
                isle_id = isle.get("isle_id")
                if isle_id not in [3, 6]:
                    continue

                raw_time = isle.get("available_at", "")
                try:
                    dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                    ts = dt.timestamp()
                    time_left = ts - now

                    x, y = isle.get("position_x"), isle.get("position_y")
                    uid = f"{gge_server}_{x}_{y}_{int(ts) // 3600}"

                    if 0 < time_left <= 300 and uid not in notified:
                        isles_to_announce.append((isle, ts))
                        notified.append(uid)
                        modifie = True
                except:
                    continue

            if not isles_to_announce:
                continue

            total_annoncailles += len(isles_to_announce)

            for guild_id_str, config in guilds_list:
                channel_id = config.get("channel_id")
                ping_small = config.get("ping_small", False)
                ping_big = config.get("ping_big", False)
                ping_role = config.get("ping_role", "")
                langue = config.get("langue", "fr")

                channel = self.bot.get_channel(channel_id)
                if not channel:
                    try:
                        channel = await self.bot.fetch_channel(channel_id)
                    except:
                        continue

                for isle, ts in isles_to_announce:
                    isle_id = isle.get("isle_id")
                    is_big = isle_id == 3
                    is_small = isle_id == 6

                    should_ping = (is_big and ping_big) or (is_small and ping_small)
                    res_nom = get_isle_name(isle_id, langue)
                    x, y = isle.get("position_x"), isle.get("position_y")

                    msg_content = f"{ping_role}" if should_ping else None
                    titre = t(
                        langue,
                        "alert_storm_title",
                        defaut="{e_aquamarineiles} Island Respawning Soon!",
                    )

                    desc = t(
                        langue,
                        "alert_storm_desc",
                        name=res_nom,
                        ts=int(ts),
                        x=x,
                        y=y,
                        defaut=f"**{res_nom}** will spawn at **<t:{int(ts)}:T>** (<t:{int(ts)}:R>)\n{{e_compass}} Coords: `{x}:{y}`",
                    )

                    embed = discord.Embed(title=titre, description=desc, color=self.clr_isles)

                    try:
                        sent_msg = await channel.send(content=msg_content, embed=embed)
                        # Ajout des informations vitales pour retrouver l'île plus tard
                        self.active_alerts.append(
                            {
                                "message": sent_msg,
                                "embed": embed,
                                "ts": ts,
                                "langue": langue,
                                "x": x,
                                "y": y,
                                "gge_server": gge_server,
                                "res_nom": res_nom,
                                "status": "pending",
                            }
                        )
                    except discord.Forbidden:
                        pass
                    except discord.HTTPException as e:
                        logger.error(f"❌ [Storm Alerts] Erreur réseau Discord : {e}")

        # 3️⃣ Cycle de vie des alertes (Pending ➔ Spawned ➔ Captured)
        alerts_to_keep = []
        for alert in self.active_alerts:
            status = alert.get("status", "pending")
            ts = alert["ts"]
            langue_alert = alert["langue"]
            emb = alert["embed"]
            msg = alert["message"]

            # Phase 1 : Pas encore apparue, on la garde en attente
            if now < ts:
                alerts_to_keep.append(alert)
                continue

            # Phase 2 : Vient d'apparaître ! (Vert)
            if status == "pending":
                alert["status"] = "spawned"
                emb.color = discord.Color.green()
                emb.title = t(
                    langue_alert,
                    "alert_storm_spawned_title",
                    defaut="{e_aquamarineiles} Island Spawned!",
                )
                emb.description = t(
                    langue_alert,
                    "alert_storm_spawned_desc",
                    name=alert["res_nom"],
                    x=alert["x"],
                    y=alert["y"],
                    defaut=f"**{alert['res_nom']}** is now available!\n{{e_compass}} Coords: `{alert['x']}:{alert['y']}`",
                )
                try:
                    await msg.edit(embed=emb)
                except:
                    pass
                alerts_to_keep.append(alert)
                continue

            # Phase 3 : Était déjà apparue, on vérifie si un joueur l'a capturée (Rouge)
            if status == "spawned":
                gge_server = alert["gge_server"]
                x, y = alert["x"], alert["y"]

                occ_dict = occupied_isles_by_server.get(gge_server, {})
                isle_data = occ_dict.get((x, y))

                if isle_data:
                    # ✅ Un joueur l'a prise !
                    occupier = isle_data.get("occupier_name") or "Unknown"
                    alliance = isle_data.get("occupier_alliance_name") or "None"

                    emb.color = discord.Color.red()
                    emb.title = t(
                        langue_alert,
                        "alert_storm_captured_title",
                        defaut="{e_aquamarineiles} Island Captured!",
                    )
                    emb.description = t(
                        langue_alert,
                        "alert_storm_captured_desc",
                        name=alert["res_nom"],
                        x=x,
                        y=y,
                        occupier=occupier,
                        alliance=alliance,
                        defaut=f"**{alert['res_nom']}** was captured by **{occupier}** (*{alliance}*)!\n{{e_compass}} Coords: `{x}:{y}`",
                    )

                    try:
                        await msg.edit(embed=emb)
                    except:
                        pass
                    # On ne l'ajoute plus à alerts_to_keep : le cycle de cette alerte est terminé.
                else:
                    # Toujours libre sur la carte. On la surveille pendant 2 heures max (7200 secondes).
                    if now <= ts + 7200:
                        alerts_to_keep.append(alert)

        self.active_alerts = alerts_to_keep

        # Sauvegarde finale
        if modifie:
            if len(notified) > 200:
                notified = notified[-200:]
            data["notified"] = notified
            await save_storm_config(data)

        if total_annoncailles > 0:
            logger.info(f"📝 [Storm Alerts] {total_annoncailles} îles annoncées au total ce cycle !")

    @storm_alert_loop.before_loop
    async def before_storm_alert_loop(self):
        await self.bot.wait_until_ready()

    # ========================================================
    # 🛑 COMMANDE : /storm stop
    # ========================================================
    @storm_group.command(name="stop", description="Stop automatic alerts for respawning islands")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def storm_stop(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)

        data = await load_storm_config()
        guild_id_str = str(interaction.guild.id)

        if "guilds" in data and guild_id_str in data["guilds"]:
            del data["guilds"][guild_id_str]
            await save_storm_config(data)

            embed = discord.Embed(
                title=t(langue, "cmd_storm_stop_title", defaut="🛑 Alerts Stopped"),
                description=t(
                    langue,
                    "cmd_storm_stop_desc",
                    defaut="Automatic Storm Islands alerts have been successfully disabled for this server.",
                ),
                color=discord.Color.red(),
            )
            await setup_embed_footer(embed, interaction, langue)
            await interaction.followup.send(embed=embed)
        else:
            msg = t(
                langue,
                "cmd_storm_stop_none",
                defaut="{e_information} No active alerts configuration found for this server.",
            )
            await interaction.followup.send(msg)

    # ==========================================
    # 🛡️ LEURRES POUR LE SCRIPT DE TRADUCTION
    # ==========================================
    def _dummy_i18n():
        langue = "fr"
        t(langue, "storm_res_aqua")
        t(langue, "storm_res_stone")
        t(langue, "storm_res_wood")


async def setup(bot: commands.Bot):
    await bot.add_cog(StormsCog(bot))
