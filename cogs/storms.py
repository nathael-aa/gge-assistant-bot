import asyncio
import json
import logging
import os
import traceback
import urllib.parse
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

import observability as obs
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


# ===========================================
# 💾 GESTION DE LA SAUVEGARDE
# ===========================================
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
# ⚙️ MAPPINGS (FORTS ET ÎLES)
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
    1: {"key": "storm_res_wood", "def": "Wood", "qty": f"40,000 {DICT_EMOJIS.get('e_wood', '🪵')}"},
    4: {"key": "storm_res_wood", "def": "Wood", "qty": f"20,000 {DICT_EMOJIS.get('e_wood', '🪵')}"},
    2: {"key": "storm_res_stone", "def": "Stone", "qty": f"40,000 {DICT_EMOJIS.get('e_stone', '🪨')}"},
    5: {"key": "storm_res_stone", "def": "Stone", "qty": f"20,000 {DICT_EMOJIS.get('e_stone', '🪨')}"},
    3: {"key": "storm_res_aqua", "def": "Aquamarine", "qty": f"52,000 {DICT_EMOJIS.get('e_aquamarine_brut', '💎')}"},
    6: {"key": "storm_res_aqua", "def": "Aquamarine", "qty": f"11,500 {DICT_EMOJIS.get('e_aquamarine_brut', '💎')}"},
}


def get_isle_name(isle_id, langue):
    res_data = ISLE_RESOURCE_MAPPING.get(isle_id)
    if not res_data:
        return t(langue, "storm_res_unknown", defaut="Unknown Island")
    name = t(langue, res_data["key"], defaut=res_data["def"])
    return f"{name} ({res_data['qty']})"


# ===========================================
# 🖼️ VUES (UI INTERACTIVE)
# ===========================================
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


# ===========================================
# 🚀 MODULE PRINCIPAL
# ===========================================
class StormsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.clr_forts = discord.Color.from_rgb(230, 126, 34)  # Orange
        self.clr_isles = discord.Color.from_rgb(64, 224, 208)  # Cyan
        self.clr_occupier = discord.Color.from_rgb(137, 196, 199)
        self.clr_status = discord.Color.from_rgb(132, 206, 209)
        self.clr_setup = discord.Color.from_rgb(144, 232, 219)
        self.api_base = "https://api.gge-tracker.com/api/v1/"
        self.active_alerts = []

    storm_group = app_commands.Group(name="storm", description="Commands for the Storm Islands event")

    # -------------------------------------------
    # 🔍 COMMANDES DE RECHERCHE
    # -------------------------------------------
    @storm_group.command(name="forts", description="Search for storm forts based on your criteria")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
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
                    msg = t(langue, "cmd_storm_api_err", defaut="{e_error} Error connecting to the Storms API.")
                    return await current_inter.followup.send(msg, ephemeral=is_refresh)
                data = await r.json()

            forts = data.get("forts", [])
            if not forts:
                msg = t(langue, "cmd_storm_no_forts", defaut="{e_information} No forts match your criteria.")
                return await current_inter.followup.send(msg, ephemeral=is_refresh)

            forts_filtres = []
            for fort in forts:
                fort_info = FORT_LEVELS_MAPPING.get(fort.get("isle_id"))
                if fort_info:
                    forts_filtres.append((fort_info["desc"], fort))

            if availability in [2, 3]:

                def sort_by_spawn(item):
                    desc, fort = item
                    try:
                        dt = datetime.fromisoformat(fort.get("available_at", "").replace("Z", "+00:00"))
                        return (int(dt.timestamp()), -fort.get("isle_id", 0))
                    except:
                        return (9999999999, -fort.get("isle_id", 0))

                forts_filtres.sort(key=sort_by_spawn)
            else:
                forts_filtres.sort(key=lambda x: (x[0], x[1].get("attacks_left", 0)), reverse=True)

            embeds = []
            items_par_page = 15
            total_pages = (len(forts_filtres) - 1) // items_par_page + 1
            titre_base = t(langue, "cmd_storm_forts_title", defaut="{e_aquamarineforts} Available Storm Forts")
            last_update = f"\n*{{e_time}} {t(langue, 'storm_last_refreshed', defaut='Last refreshed:')} <t:{int(datetime.now().timestamp())}:T>*"
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
                        try:
                            dt = datetime.fromisoformat(fort.get("available_at", "").replace("Z", "+00:00"))
                            time_str = f" | {{e_time}} <t:{int(dt.timestamp())}:R>"
                        except:
                            pass
                    lignes_description.append(
                        f"**Lvl. {desc}** | {{e_compass}} `({x}:{y})` | {{e_attaque}} {attaques} {lbl_att_left}{time_str}"
                    )

                embed.description = ("\n".join(lignes_description) + "\n" + last_update).format(**DICT_EMOJIS)
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

            async def refresh_callback(btn_inter: discord.Interaction):
                await btn_inter.response.defer()
                await fetch_and_build_view(btn_inter, is_refresh=True)

            view = LocalPaginationView(embeds) if len(embeds) > 1 else RefreshOnlyView(refresh_callback, langue)
            if isinstance(view, LocalPaginationView):
                refresh_btn = discord.ui.Button(
                    style=discord.ButtonStyle.primary,
                    emoji=DICT_EMOJIS.get("e_refresh", "🔄"),
                    label=t(langue, "btn_refresh", defaut="Refresh"),
                )
                refresh_btn.callback = refresh_callback
                view.add_item(refresh_btn)

            if is_refresh:
                await current_inter.edit_original_response(embed=embeds[0], view=view)
            else:
                view.message = await current_inter.followup.send(embed=embeds[0], view=view, wait=True)
                await prompt_vote_if_lucky(interaction, probability_percent=15, langue=langue)

        await fetch_and_build_view(interaction, is_refresh=False)

    @storm_group.command(name="isles", description="Search for resource islands in the Storm Islands")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
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
            params = {"page": 1, "size": 500}
            if status.value != 0:
                params["filterByState"] = status.value

            url = f"{self.api_base}/storms/isles"
            async with self.bot.session.get(url, headers=headers, params=params, timeout=15) as r:
                if r.status != 200:
                    msg = t(langue, "cmd_storm_api_err", defaut="{e_error} Error connecting to the Storms API.")
                    return await current_inter.followup.send(msg, ephemeral=is_refresh)
                data = await r.json()

            isles = data.get("isles", [])
            allowed_isles = []
            if resource.value == 1:
                allowed_isles = [3, 6]
            elif resource.value == 2:
                allowed_isles = [1, 4]
            elif resource.value == 3:
                allowed_isles = [2, 5]

            if allowed_isles:
                isles = [i for i in isles if i.get("isle_id") in allowed_isles]

            if not isles:
                msg = t(langue, "cmd_storm_no_isles", defaut="{e_information} No islands match your criteria.")
                return await current_inter.followup.send(msg, ephemeral=is_refresh)

            if status.value == 3:

                def sort_by_spawn(isle):
                    try:
                        dt = datetime.fromisoformat(isle.get("available_at", "").replace("Z", "+00:00"))
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
            last_update = f"\n*{{e_refresh}} {t(langue, 'storm_last_refreshed', defaut='Last refreshed:')} <t:{int(datetime.now().timestamp())}:T>*"
            lbl_free, lbl_respawns, lbl_unknown_state = (
                t(langue, "storm_isle_free", defaut="Free!"),
                t(langue, "storm_isle_respawns", defaut="Respawns:"),
                t(langue, "storm_isle_unknown", defaut="State unknown"),
            )
            lbl_unknown_player, lbl_none_alliance = (
                t(langue, "storm_unknown_player", defaut="Unknown"),
                t(langue, "storm_none_alliance", defaut="None"),
            )

            for i in range(0, len(isles), items_par_page):
                page_items = isles[i : i + items_par_page]
                embed = discord.Embed(
                    title=f"{titre_base} (Page {(i // items_par_page) + 1}/{total_pages})", color=self.clr_isles
                )

                lignes_description = []
                for isle in page_items:
                    x, y = isle.get("position_x"), isle.get("position_y")
                    res_nom = get_isle_name(isle.get("isle_id"), langue)
                    etat = isle.get("state")

                    if etat == 0:
                        ligne = f"{{e_greencirclebullet}} **{res_nom}** | {{e_compass}} `({x}:{y})` | *{lbl_free}*"
                    elif etat == 1:
                        ligne = f"{{e_std_red_circle}} **{res_nom}** | {{e_compass}} `({x}:{y})` | {{e_std_shield}} {isle.get('occupier_name') or lbl_unknown_player} (*{isle.get('occupier_alliance_name') or lbl_none_alliance}*)"
                    elif etat == 2:
                        try:
                            ts = int(
                                datetime.fromisoformat(isle.get("available_at", "").replace("Z", "+00:00")).timestamp()
                            )
                            time_str = f"<t:{ts}:R>"
                        except:
                            time_str = t(langue, "storm_isle_soon", defaut="Soon")
                        ligne = f"{{e_time}} **{res_nom}** | {{e_compass}} `({x}:{y})` | {lbl_respawns} {time_str}"
                    else:
                        ligne = f"{{e_information}} **{res_nom}** | {{e_compass}} `({x}:{y})` | {lbl_unknown_state}"
                    lignes_description.append(ligne)

                embed.description = ("\n".join(lignes_description) + "\n" + last_update).format(**DICT_EMOJIS)
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

            async def refresh_callback(btn_inter: discord.Interaction):
                await btn_inter.response.defer()
                await fetch_and_build_view(btn_inter, is_refresh=True)

            view = LocalPaginationView(embeds) if len(embeds) > 1 else RefreshOnlyView(refresh_callback, langue)
            if isinstance(view, LocalPaginationView):
                refresh_btn = discord.ui.Button(
                    style=discord.ButtonStyle.primary,
                    emoji=DICT_EMOJIS.get("e_refresh", "🔄"),
                    label=t(langue, "btn_refresh", defaut="Refresh"),
                )
                refresh_btn.callback = refresh_callback
                view.add_item(refresh_btn)

            if is_refresh:
                await current_inter.edit_original_response(embed=embeds[0], view=view)
            else:
                view.message = await current_inter.followup.send(embed=embeds[0], view=view, wait=True)
                await prompt_vote_if_lucky(interaction, probability_percent=15, langue=langue)

        await fetch_and_build_view(interaction, is_refresh=False)

    @storm_group.command(name="occupier", description="List all islands currently held by a specific player")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
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
                    return await current_inter.followup.send(msg, ephemeral=is_refresh)
                data = await r.json()

            isles = data.get("isles", [])
            if not isles:
                msg = t(
                    langue,
                    "cmd_storm_no_occupier",
                    player=player,
                    defaut=f"{{e_information}} The player **{player}** does not currently hold any resource islands.",
                )
                return await current_inter.followup.send(msg, ephemeral=is_refresh)

            embeds = []
            items_par_page = 20
            total_pages = (len(isles) - 1) // items_par_page + 1
            titre_base = t(
                langue, "cmd_storm_occupier_title", player=player, defaut=f"{{e_players}} Islands held by {player}"
            )
            desc_total = t(
                langue, "storm_occ_total", count=len(isles), defaut=f"Total islands under control: **{len(isles)}**"
            )
            last_update = f"\n*{{e_refresh}} {t(langue, 'storm_last_refreshed', defaut='Last refreshed:')} <t:{int(datetime.now().timestamp())}:T>*"

            for i in range(0, len(isles), items_par_page):
                page_items = isles[i : i + items_par_page]
                embed = discord.Embed(
                    title=f"{titre_base} (Page {(i // items_par_page) + 1}/{total_pages})",
                    description=f"{desc_total}\n\n",
                    color=self.clr_occupier,
                )
                lignes_description = [
                    f"{{e_compass}} `({isle.get('position_x')}:{isle.get('position_y')})` | {get_isle_name(isle.get('isle_id'), langue)}"
                    for isle in page_items
                ]
                embed.description += "\n".join(lignes_description) + "\n" + last_update
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

            async def refresh_callback(btn_inter: discord.Interaction):
                await btn_inter.response.defer()
                await fetch_and_build_view(btn_inter, is_refresh=True)

            view = LocalPaginationView(embeds) if len(embeds) > 1 else RefreshOnlyView(refresh_callback, langue)
            if isinstance(view, LocalPaginationView):
                refresh_btn = discord.ui.Button(
                    style=discord.ButtonStyle.primary,
                    emoji=DICT_EMOJIS.get("e_refresh", "🔄"),
                    label=t(langue, "btn_refresh", defaut="Refresh"),
                )
                refresh_btn.callback = refresh_callback
                view.add_item(refresh_btn)

            if is_refresh:
                await current_inter.edit_original_response(embed=embeds[0], view=view)
            else:
                view.message = await current_inter.followup.send(embed=embeds[0], view=view, wait=True)
                await prompt_vote_if_lucky(interaction, probability_percent=5, langue=langue)

        await fetch_and_build_view(interaction, is_refresh=False)

    @storm_group.command(name="status", description="Displays the freshness state of the Storm Islands map scan")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    async def storm_status(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

        url = f"{self.api_base}/storms/meta"
        async with self.bot.session.get(url, headers=headers, timeout=10) as r:
            if r.status != 200:
                return await interaction.followup.send(
                    t(langue, "cmd_storm_api_err", defaut="{e_error} Error connecting to the Storms API.")
                )
            data = await r.json()

        try:
            scan_str = (
                f"<t:{int(datetime.fromisoformat(data.get('last_scan_at', '').replace('Z', '+00:00')).timestamp())}:R>"
            )
        except:
            scan_str = t(langue, "storm_status_unknown", defaut="Unknown")

        try:
            season_str = f"<t:{int(datetime.fromisoformat(data.get('season_started_at', '').replace('Z', '+00:00')).timestamp())}:D>"
        except:
            season_str = t(langue, "storm_status_unknown", defaut="Unknown")

        embed = discord.Embed(
            title=t(langue, "cmd_storm_status_title", defaut="{e_std_satellite} Storm Islands Status"),
            color=self.clr_status,
        )
        embed.description = t(
            langue,
            "storm_status_desc_v2",
            season=season_str,
            scan=scan_str,
            radius=data.get("scan_radius", 0),
            forts=format_num(data.get("forts_count", 0)),
            isles=format_num(data.get("isles_count", 0)),
            defaut=(
                f"**Season Started:** {season_str}\n**Last Scan:** {scan_str}\n**Covered Radius:** {data.get('scan_radius', 0)} tiles\n\n"
                f"{{e_aquamarineforts}} **Tracked Forts:** {format_num(data.get('forts_count', 0))}\n"
                f"{{e_aquamarineiles}} **Tracked Isles:** {format_num(data.get('isles_count', 0))}"
            ),
        )
        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed)

    # -------------------------------------------
    # 🔔 CONFIGURATION ALERTES ÎLES
    # -------------------------------------------
    @storm_group.command(name="setup_isles", description="Configure automatic alerts for respawning islands")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.describe(
        channel="The channel where notifications will be sent",
        ping_small="Ping for small islands (Default: False)",
        ping_big="Ping for big islands (Default: False)",
        role="Role to ping (Leave empty for no mention)",
    )
    async def storm_setup_isles(
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

        ping_format = role.mention if role else ""
        data = await load_storm_config()
        if "guilds" not in data:
            data["guilds"] = {}
        guild_id_str = str(interaction.guild.id)

        if guild_id_str not in data["guilds"]:
            data["guilds"][guild_id_str] = {"gge_server": serveur, "langue": langue}

        data["guilds"][guild_id_str].update(
            {
                "isle_channel_id": channel.id,
                "isle_ping_small": ping_small,
                "isle_ping_big": ping_big,
                "isle_ping_role": ping_format,
                "gge_server": serveur,
                "langue": langue,
            }
        )

        await save_storm_config(data)

        obs.record_guild_event(
            "storm_setup_isles",
            guild=interaction.guild,
            user_id=interaction.user.id,
            gge_server=serveur,
            new_value=f"channel:{channel.id}|big:{ping_big}|small:{ping_small}",
        )

        desc = t(
            langue,
            "storm_setup_desc",
            channel=channel.mention,
            defaut=f"Notifications will be sent in {channel.mention}.",
        )
        titre = t(
            langue, "cmd_storm_setup_isles_title", defaut="{e_greencirclebullet} Storm Islands Alerts Configured!"
        ).format(**DICT_EMOJIS)
        embed = discord.Embed(title=titre, color=self.clr_setup, description=desc)

        val_yes = t(langue, "storm_setup_yes", defaut="Yes")
        val_no = t(langue, "storm_setup_no", defaut="No")
        val_no_mention = t(langue, "storm_setup_no_mention", defaut="No mention")

        emo_yes = DICT_EMOJIS.get("e_greencirclebullet", "🟢")
        emo_no = DICT_EMOJIS.get("e_tomatobulletpoint", "🔴")

        etat_big = f"{emo_yes} {val_yes}" if ping_big else f"{emo_no} {val_no}"
        etat_small = f"{emo_yes} {val_yes}" if ping_small else f"{emo_no} {val_no}"
        mention_txt = ping_format if ping_format else val_no_mention

        embed.add_field(name=t(langue, "storm_setup_field_big", defaut="Ping Big Isles"), value=etat_big, inline=True)
        embed.add_field(
            name=t(langue, "storm_setup_field_small", defaut="Ping Small Isles"), value=etat_small, inline=True
        )
        embed.add_field(name=t(langue, "storm_setup_field_mention", defaut="Mention"), value=mention_txt, inline=True)

        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed)

    @storm_group.command(name="stop_isles", description="Stop automatic alerts for respawning islands")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    async def storm_stop_isles(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)

        data = await load_storm_config()
        guild_id_str = str(interaction.guild.id)

        if "guilds" in data and guild_id_str in data["guilds"] and "isle_channel_id" in data["guilds"][guild_id_str]:
            keys_to_remove = ["isle_channel_id", "isle_ping_small", "isle_ping_big", "isle_ping_role"]
            for k in keys_to_remove:
                data["guilds"][guild_id_str].pop(k, None)

            if "fort_channel_id" not in data["guilds"][guild_id_str]:
                del data["guilds"][guild_id_str]

            await save_storm_config(data)
            obs.record_guild_event(
                "storm_stop_isles", guild=interaction.guild, user_id=interaction.user.id, gge_server=serveur
            )

            embed = discord.Embed(
                title=t(langue, "cmd_storm_stop_isles_title", defaut="🛑 Alerts Stopped"),
                description=t(
                    langue,
                    "cmd_storm_stop_isles_desc",
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

    # -------------------------------------------
    # 🔔 CONFIGURATION ALERTES FORTS
    # -------------------------------------------
    @storm_group.command(name="setup_forts", description="Configure automatic alerts for returning storm forts")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.describe(
        channel="The channel where notifications will be sent",
        min_level="Minimum fort level to receive alerts for",
        role="Role to ping (Leave empty for no mention)",
    )
    @app_commands.choices(
        min_level=[
            app_commands.Choice(name="Level 80 only", value=80),
            app_commands.Choice(name="Level 70 and 80", value=70),
            app_commands.Choice(name="Level 60+", value=60),
            app_commands.Choice(name="All forts (40+)", value=40),
        ]
    )
    async def storm_setup_forts(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        min_level: app_commands.Choice[int],
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

        ping_format = role.mention if role else ""
        data = await load_storm_config()
        if "guilds" not in data:
            data["guilds"] = {}
        guild_id_str = str(interaction.guild.id)

        if guild_id_str not in data["guilds"]:
            data["guilds"][guild_id_str] = {"gge_server": serveur, "langue": langue}

        data["guilds"][guild_id_str].update(
            {
                "fort_channel_id": channel.id,
                "fort_min_level": min_level.value,
                "fort_ping_role": ping_format,
                "gge_server": serveur,
                "langue": langue,
            }
        )
        await save_storm_config(data)

        obs.record_guild_event(
            "storm_setup_forts",
            guild=interaction.guild,
            user_id=interaction.user.id,
            gge_server=serveur,
            new_value=f"channel:{channel.id}|min_lvl:{min_level.value}",
        )

        desc = t(
            langue,
            "storm_setup_forts_desc",
            channel=channel.mention,
            lvl=min_level.value,
            defaut=f"Fort notifications (Level {min_level.value}+) will be sent in {channel.mention}.",
        )
        titre = t(langue, "cmd_storm_setup_forts_title", defaut="{e_greencirclebullet} Fort Alerts Configured!").format(
            **DICT_EMOJIS
        )
        embed = discord.Embed(title=titre, color=self.clr_setup, description=desc)

        val_no_mention = t(langue, "storm_setup_no_mention", defaut="No mention")
        mention_txt = ping_format if ping_format else val_no_mention

        embed.add_field(
            name=t(langue, "storm_setup_field_min_lvl", defaut="Min. Level"),
            value=f"**{min_level.value}+**",
            inline=True,
        )
        embed.add_field(name=t(langue, "storm_setup_field_mention", defaut="Mention"), value=mention_txt, inline=True)

        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed)

    @storm_group.command(name="stop_forts", description="Stop automatic alerts for storm forts")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    async def storm_stop_forts(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)

        data = await load_storm_config()
        guild_id_str = str(interaction.guild.id)

        if "guilds" in data and guild_id_str in data["guilds"] and "fort_channel_id" in data["guilds"][guild_id_str]:
            keys_to_remove = ["fort_channel_id", "fort_min_level", "fort_ping_role"]
            for k in keys_to_remove:
                data["guilds"][guild_id_str].pop(k, None)

            if "isle_channel_id" not in data["guilds"][guild_id_str]:
                del data["guilds"][guild_id_str]

            await save_storm_config(data)
            obs.record_guild_event(
                "storm_stop_forts", guild=interaction.guild, user_id=interaction.user.id, gge_server=serveur
            )

            embed = discord.Embed(
                title=t(langue, "cmd_storm_stop_forts_title", defaut="🛑 Fort Alerts Stopped"),
                description=t(
                    langue,
                    "cmd_storm_stop_forts_desc",
                    defaut="Automatic Storm Forts alerts have been successfully disabled for this server.",
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

    # -------------------------------------------
    # 🔄 TÂCHE DE FOND (BOUCLE D'ALERTES)
    # -------------------------------------------
    def cog_load(self):
        self.storm_alert_loop.start()

    def cog_unload(self):
        self.storm_alert_loop.cancel()

    @tasks.loop(minutes=1.0)
    async def storm_alert_loop(self):
        obs.set_task_name("storm_alert_loop")
        try:
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

            servers_with_spawned = {
                alert["gge_server"]
                for alert in self.active_alerts
                if now >= alert["ts"] and alert.get("status", "pending") in ["pending", "spawned"]
            }

            modifie = False
            total_annoncailles = 0
            occupied_isles_by_server = {}

            for gge_server, guilds_list in servers_to_check.items():
                headers = await get_api_headers(custom_server=gge_server)

                # ==========================================
                # 🏝️ SCAN DES ÎLES
                # ==========================================
                params_respawning = {"size": 4000, "filterByState": 3}
                isles_respawning = []
                try:
                    url_respawning = f"{self.api_base}storms/isles"
                    async with self.bot.session.get(
                        url_respawning, headers=headers, params=params_respawning, timeout=15
                    ) as r:
                        if r.status == 200:
                            api_data = await r.json()
                            isles_respawning = api_data.get("isles", [])
                        else:
                            logger.warning(
                                f"⚠️ [Storm Alerts] Code {r.status} reçu de l'API. URL: {r.url} - Serveur configuré: {gge_server}"
                            )
                except Exception as e:
                    logger.error(f"❌ [Storm Alerts] Erreur API Respawning pour {gge_server} : {e}")

                if gge_server in servers_with_spawned:
                    params_occupied = {"size": 4000, "filterByState": 2}
                    try:
                        url_occupied = f"{self.api_base}storms/isles"
                        async with self.bot.session.get(
                            url_occupied, headers=headers, params=params_occupied, timeout=15
                        ) as r:
                            if r.status == 200:
                                occ_data = await r.json()
                                occupied_isles_by_server[gge_server] = {
                                    (isle.get("position_x"), isle.get("position_y")): isle
                                    for isle in occ_data.get("isles", [])
                                }
                            else:
                                logger.warning(
                                    f"⚠️ [Storm Alerts] Code {r.status} API (Occupied). Serveur: {gge_server}"
                                )
                    except Exception as e:
                        logger.error(f"❌ [Storm Alerts] Erreur API Occupied pour {gge_server} : {e}")

                # ==========================================
                # 🏰 SCAN DES FORTS
                # ==========================================
                params_forts = {"size": 4000, "filterByAvailability": 2}
                forts_respawning = []
                try:
                    url_forts = f"{self.api_base}storms/forts"
                    async with self.bot.session.get(url_forts, headers=headers, params=params_forts, timeout=15) as r:
                        if r.status == 200:
                            api_data = await r.json()
                            forts_respawning = api_data.get("forts", [])
                        else:
                            logger.warning(f"⚠️ [Storm Alerts] Code {r.status} API (Forts). Serveur: {gge_server}")
                except Exception as e:
                    logger.error(f"❌ [Storm Alerts] Erreur API Forts pour {gge_server} : {e}")

                # -- Préparation des annonces Îles --
                isles_to_announce = []
                for isle in isles_respawning:
                    isle_id = isle.get("isle_id")
                    if isle_id not in [3, 6]:
                        continue
                    try:
                        dt = datetime.fromisoformat(isle.get("available_at", "").replace("Z", "+00:00"))
                        ts = dt.timestamp()
                        time_left = ts - now
                        x, y = isle.get("position_x"), isle.get("position_y")
                        uid = f"isle_{gge_server}_{x}_{y}_{int(ts) // 3600}"

                        if 0 < time_left <= 300 and uid not in notified:
                            isles_to_announce.append((isle, ts))
                            notified.append(uid)
                            modifie = True
                    except:
                        continue

                # -- Préparation des annonces Forts --
                forts_to_announce = []
                for fort in forts_respawning:
                    isle_id = fort.get("isle_id")
                    fort_info = FORT_LEVELS_MAPPING.get(isle_id)
                    if not fort_info:
                        continue
                    try:
                        dt = datetime.fromisoformat(fort.get("available_at", "").replace("Z", "+00:00"))
                        ts = dt.timestamp()
                        time_left = ts - now
                        x, y = fort.get("position_x"), fort.get("position_y")
                        uid = f"fort_{gge_server}_{x}_{y}_{int(ts) // 3600}"

                        if 0 < time_left <= 300 and uid not in notified:
                            forts_to_announce.append((fort, ts, fort_info["lvl"], fort_info["desc"]))
                            notified.append(uid)
                            modifie = True
                    except:
                        continue

                if not isles_to_announce and not forts_to_announce:
                    continue

                total_annoncailles += len(isles_to_announce) + len(forts_to_announce)

                for guild_id_str, config in guilds_list:
                    langue = config.get("langue", "fr")

                    # ==========================================
                    # 📣 ENVOI DES ALERTES ÎLES
                    # ==========================================
                    channel_id = config.get("isle_channel_id")
                    if channel_id and isles_to_announce:
                        channel = self.bot.get_channel(channel_id)
                        if not channel:
                            try:
                                channel = await self.bot.fetch_channel(channel_id)
                            except:
                                pass

                        if channel:
                            ping_small = config.get("isle_ping_small", False)
                            ping_big = config.get("isle_ping_big", False)
                            ping_role = config.get("isle_ping_role", "")

                            for isle, ts in isles_to_announce:
                                isle_id = isle.get("isle_id")
                                is_big = isle_id == 3
                                is_small = isle_id == 6
                                should_ping = (is_big and ping_big) or (is_small and ping_small)
                                res_nom = get_isle_name(isle_id, langue)
                                x, y = isle.get("position_x"), isle.get("position_y")

                                msg_content = ping_role if (should_ping and ping_role) else None
                                titre = t(
                                    langue, "alert_storm_title", defaut="{e_aquamarineiles} Island Respawning Soon!"
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
                                embed = discord.Embed(
                                    title=titre, description=desc.format(**DICT_EMOJIS), color=self.clr_isles
                                )

                                try:
                                    sent_msg = await channel.send(content=msg_content, embed=embed)
                                    obs.record_alert(
                                        source="storm_islands",
                                        alert_type="respawn",
                                        gge_server=gge_server,
                                        channel="guild",
                                        recipients=1,
                                        delivered=1,
                                        failed=0,
                                        dm_blocked=0,
                                    )
                                    self.active_alerts.append(
                                        {
                                            "type": "isle",
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
                                    obs.record_alert(
                                        source="storm_islands",
                                        alert_type="respawn",
                                        gge_server=gge_server,
                                        channel="guild",
                                        recipients=1,
                                        delivered=0,
                                        failed=1,
                                        dm_blocked=1,
                                    )
                                except discord.HTTPException as e:
                                    logger.error(f"❌ [Storm Alerts] Erreur réseau Discord : {e}")

                    # ==========================================
                    # 📣 ENVOI DES ALERTES FORTS
                    # ==========================================
                    fort_channel_id = config.get("fort_channel_id")
                    if fort_channel_id and forts_to_announce:
                        fort_channel = self.bot.get_channel(fort_channel_id)
                        if not fort_channel:
                            try:
                                fort_channel = await self.bot.fetch_channel(fort_channel_id)
                            except:
                                pass

                        if fort_channel:
                            fort_min_level = config.get("fort_min_level", 80)
                            fort_ping_role = config.get("fort_ping_role", "")

                            for fort, ts, lvl, desc_str in forts_to_announce:
                                if lvl >= fort_min_level:
                                    x, y = fort.get("position_x"), fort.get("position_y")

                                    titre = t(
                                        langue, "alert_fort_title", defaut="{e_aquamarineforts} Fort Available Soon!"
                                    )
                                    desc = t(
                                        langue,
                                        "alert_fort_desc",
                                        lvl=desc_str,
                                        ts=int(ts),
                                        x=x,
                                        y=y,
                                        defaut=f"A **Level {desc_str} Fort** will be attackable at **<t:{int(ts)}:T>** (<t:{int(ts)}:R>)\n{{e_compass}} Coords: `{x}:{y}`",
                                    )
                                    embed = discord.Embed(
                                        title=titre, description=desc.format(**DICT_EMOJIS), color=self.clr_forts
                                    )

                                    msg_content = fort_ping_role if fort_ping_role else None
                                    try:
                                        sent_msg = await fort_channel.send(content=msg_content, embed=embed)
                                        self.active_alerts.append(
                                            {
                                                "type": "fort",
                                                "message": sent_msg,
                                                "embed": embed,
                                                "ts": ts,
                                                "langue": langue,
                                                "x": x,
                                                "y": y,
                                                "gge_server": gge_server,
                                                "res_nom": f"Fort {desc_str}",
                                                "status": "pending",
                                            }
                                        )
                                    except Exception as e:
                                        logger.error(f"❌ [Storm Alerts] Erreur envoi Fort sur {fort_channel_id} : {e}")

            # ==========================================
            # 🔄 MISE À JOUR DES ANCIENS MESSAGES
            # ==========================================
            alerts_to_keep = []
            for alert in self.active_alerts:
                status = alert.get("status", "pending")
                ts = alert["ts"]
                langue_alert = alert["langue"]
                emb = alert["embed"]
                msg = alert["message"]
                is_fort = alert.get("type") == "fort"

                if now < ts:
                    alerts_to_keep.append(alert)
                    continue

                if status == "pending":
                    alert["status"] = "spawned"
                    emb.color = discord.Color.dark_green() if is_fort else discord.Color.green()

                    if is_fort:
                        emb.title = t(
                            langue_alert, "alert_fort_spawned_title", defaut="{e_aquamarineforts} Fort Available!"
                        ).format(**DICT_EMOJIS)
                    else:
                        emb.title = t(
                            langue_alert, "alert_storm_spawned_title", defaut="{e_aquamarineiles} Island Spawned!"
                        ).format(**DICT_EMOJIS)

                    emb.description = t(
                        langue_alert,
                        "alert_storm_spawned_desc",
                        name=alert["res_nom"],
                        x=alert["x"],
                        y=alert["y"],
                        defaut=f"**{alert['res_nom']}** is now available!\n{{e_compass}} Coords: `{alert['x']}:{alert['y']}`",
                    ).format(**DICT_EMOJIS)

                    try:
                        await msg.edit(embed=emb)
                    except:
                        pass
                    alerts_to_keep.append(alert)
                    continue

                if status == "spawned":
                    gge_server = alert["gge_server"]
                    x, y = alert["x"], alert["y"]
                    occ_dict = occupied_isles_by_server.get(gge_server, {})
                    isle_data = occ_dict.get((x, y))

                    if isle_data:
                        occupier = isle_data.get("occupier_name") or "Unknown"
                        alliance = isle_data.get("occupier_alliance_name") or "None"

                        emb.color = discord.Color.dark_red() if is_fort else discord.Color.red()

                        if is_fort:
                            emb.title = t(
                                langue_alert, "alert_fort_captured_title", defaut="{e_aquamarineforts} Fort Captured!"
                            ).format(**DICT_EMOJIS)
                        else:
                            emb.title = t(
                                langue_alert, "alert_storm_captured_title", defaut="{e_aquamarineiles} Island Captured!"
                            ).format(**DICT_EMOJIS)

                        emb.description = t(
                            langue_alert,
                            "alert_storm_captured_desc",
                            name=alert["res_nom"],
                            x=x,
                            y=y,
                            occupier=occupier,
                            alliance=alliance,
                            defaut=f"**{alert['res_nom']}** was captured by **{occupier}** (*{alliance}*)!\n{{e_compass}} Coords: `{x}:{y}`",
                        ).format(**DICT_EMOJIS)

                        try:
                            await msg.edit(embed=emb)
                        except:
                            pass
                    else:
                        if now <= ts + 7200:
                            alerts_to_keep.append(alert)

            self.active_alerts = alerts_to_keep

            if modifie:
                if len(notified) > 200:
                    notified = notified[-200:]
                data["notified"] = notified
                await save_storm_config(data)

            if total_annoncailles > 0:
                logger.info(
                    f"📝 [Storm Alerts] {total_annoncailles} alertes envoyées au total ce cycle (Îles + Forts) !"
                )

        except Exception as e:
            logger.error(f"❌ [Storm Alerts CRASH FATAL] : {traceback.format_exc()}")
            obs.record_error(source="task", scope="storm_alert_loop", exception=e, cog="storms", severity="critical")

    @storm_alert_loop.before_loop
    async def before_storm_alert_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(15)

    def _dummy_i18n():
        langue = "fr"
        t(langue, "storm_res_aqua")
        t(langue, "storm_res_stone")
        t(langue, "storm_res_wood")


async def setup(bot: commands.Bot):
    await bot.add_cog(StormsCog(bot))
