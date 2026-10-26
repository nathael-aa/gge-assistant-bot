import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

import observability as obs
from utils import (
    BASE_DATA_PATH,
    BASE_DIR,
    CONFIG_DIR,
    async_json_dump,
    async_json_load,
    clear_config_cache,
    get_file_lock,
    get_server_config,
    load_configuration_async,
    t,
)

logger = logging.getLogger("GGE_Bot")

# ===========================================
# 🛒 OFFERS CATEGORIES (Pour le Wizard)
# ===========================================
CAT_MAPPING = {
    "promos": ("🎁", "Gifts & Promos (Free)"),
    "supersale": ("🛍️", "Super Sales"),
    "eventoffers": ("⚔️", "Event Offers"),
    "dailybundles": ("📅", "Daily Bundles"),
    "hcbundles": ("💰", "Ruby Bundles"),
    "hardCurrency": ("💎", "Direct Ruby Purchases"),
    "cashoffers": ("🛒", "Classic Offers"),
    "subscriptions": ("📜", "Subscriptions"),
}


# ===========================================
# 💾 USER CONFIGURATION (DMs)
# ===========================================
async def load_users_config():
    path = CONFIG_DIR / "users.json"
    async with get_file_lock(path):
        if path.exists():
            try:
                return await async_json_load(path)
            except Exception:
                pass
    return {}


async def save_users_config(data):
    path = CONFIG_DIR / "users.json"
    async with get_file_lock(path):
        await async_json_dump(path, data, indent=2, ensure_ascii=False)


# ===========================================
# 🛠️ SERVER SETUP WIZARD (UI)
# ===========================================
class ServerSetupWizard(discord.ui.View):
    def __init__(self, cog, interaction: discord.Interaction, lang: str, gge_server: str):
        super().__init__(timeout=900)
        self.cog = cog
        self.guild = interaction.guild
        self.lang = lang
        self.gge_server = gge_server
        self.step_idx = 0
        self.servers_dir = CONFIG_DIR.parent / "serveurs"

        self.data = {
            "news": {"channel_id": None, "ping_role": None, "disabled": False},
            "patchnotes": {"channel_id": None, "ping_role": None, "disabled": False},
            "hub_alerts": {"channel_id": None, "ping_role": None, "disabled": False},
            "calendar": {"channel_id": None, "disabled": False},
            "isle": {"channel_id": None, "ping_role": None, "ping_small": False, "ping_big": False, "disabled": False},
            "forts": {"channel_id": None, "min_level": 70, "ping_role": None, "disabled": False},
            "radar": {"channel_id": None, "ping_role": None, "disabled": False},
            "shop": {
                "channel_id": None,
                "profile": 950,
                "ping_role": None,
                "categories": ["promos"],
                "disabled": False,
            },
        }

        self.step_keys = ["intro", "news", "patchnotes", "hub_alerts", "calendar", "isle", "forts", "radar", "shop"]
        self.steps = [
            self.step_intro,
            self.step_news,
            self.step_patchnotes,
            self.step_hub_alerts,
            self.step_calendar,
            self.step_isle,
            self.step_forts,
            self.step_radar,
            self.step_shop,
        ]

        self.setup_current_step()

    def format_ping(self, r_id):
        """Transforme l'ID brut du rôle en format mention Discord compréhensible par l'API."""
        if not r_id:
            return ""
        role = self.guild.get_role(r_id)
        if role:
            return "@everyone" if role.is_default() else role.mention
        return f"<@&{r_id}>"

    def setup_current_step(self):
        self.clear_items()
        self.steps[self.step_idx]()

    def add_controls(self, is_last: bool = False, extra_buttons: list = None, ctrl_row: int = 3):
        """Standardise l'ordre des boutons: Cancel -> Extras -> Disable -> Skip -> Next"""
        btn_cancel = discord.ui.Button(
            label=t(self.lang, "wizard_btn_cancel", defaut="Cancel ✖️"), style=discord.ButtonStyle.danger, row=ctrl_row
        )
        btn_cancel.callback = self.cancel_wizard
        self.add_item(btn_cancel)

        if extra_buttons:
            for btn in extra_buttons:
                btn.row = 2
                self.add_item(btn)

        btn_disable = discord.ui.Button(
            label=t(self.lang, "wizard_btn_disable", defaut="Disable 🚫"),
            style=discord.ButtonStyle.secondary,
            row=ctrl_row,
        )
        btn_disable.callback = self.disable_step
        self.add_item(btn_disable)

        if not is_last:
            btn_skip = discord.ui.Button(
                label=t(self.lang, "wizard_btn_skip", defaut="Skip ⏭️"),
                style=discord.ButtonStyle.secondary,
                row=ctrl_row,
            )
            btn_skip.callback = self.skip_step
            self.add_item(btn_skip)

        if is_last:
            btn_next = discord.ui.Button(
                label=t(self.lang, "wizard_btn_finish", defaut="Finish ✅"),
                style=discord.ButtonStyle.success,
                row=ctrl_row,
            )
        else:
            btn_next = discord.ui.Button(
                label=t(self.lang, "wizard_btn_next", defaut="Next ➡️"), style=discord.ButtonStyle.primary, row=ctrl_row
            )
        btn_next.callback = self.advance
        self.add_item(btn_next)

    async def advance(self, interaction: discord.Interaction):
        self.step_idx += 1
        if self.step_idx >= len(self.steps):
            await self.finish_setup(interaction)
        else:
            self.setup_current_step()
            embed = self.get_current_embed()
            await interaction.response.edit_message(embed=embed, view=self)

    async def skip_step(self, interaction: discord.Interaction):
        await self.advance(interaction)

    async def disable_step(self, interaction: discord.Interaction):
        current_key = self.step_keys[self.step_idx]
        if current_key != "intro":
            self.data[current_key]["disabled"] = True
        await self.advance(interaction)

    async def cancel_wizard(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(
            content=t(self.lang, "wizard_cancelled", defaut="❌ Configuration cancelled. No changes were saved."),
            embed=None,
            view=None,
        )

    # -------------------------------------------------------------
    # 0. INTRO
    # -------------------------------------------------------------
    def step_intro(self):
        btn_cancel = discord.ui.Button(
            label=t(self.lang, "wizard_btn_cancel", defaut="Cancel ✖️"), style=discord.ButtonStyle.danger, row=3
        )
        btn_cancel.callback = self.cancel_wizard
        self.add_item(btn_cancel)

        btn_start = discord.ui.Button(
            label=t(self.lang, "wizard_btn_start", defaut="Start 🚀"), style=discord.ButtonStyle.success, row=3
        )
        btn_start.callback = self.advance
        self.add_item(btn_start)

    # -------------------------------------------------------------
    # 1. NEWS
    # -------------------------------------------------------------
    def step_news(self):
        c_sel = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder=t(self.lang, "wizard_sel_news_chan", defaut="Select the official news channel..."),
            row=0,
        )

        async def c_callback(interaction: discord.Interaction):
            self.data["news"]["channel_id"] = int(c_sel.values[0].id)
            await interaction.response.defer()

        c_sel.callback = c_callback
        self.add_item(c_sel)

        r_sel = discord.ui.RoleSelect(
            placeholder=t(self.lang, "wizard_sel_ping_role", defaut="Select a role to ping (optional)..."), row=1
        )

        async def r_callback(interaction: discord.Interaction):
            self.data["news"]["ping_role"] = int(r_sel.values[0].id)
            await interaction.response.defer()

        r_sel.callback = r_callback
        self.add_item(r_sel)

        self.add_controls()

    # -------------------------------------------------------------
    # 2. PATCHNOTES
    # -------------------------------------------------------------
    def step_patchnotes(self):
        c_sel = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder=t(self.lang, "wizard_sel_patch_chan", defaut="Select the patchnotes channel..."),
            row=0,
        )

        async def c_callback(interaction: discord.Interaction):
            self.data["patchnotes"]["channel_id"] = int(c_sel.values[0].id)
            await interaction.response.defer()

        c_sel.callback = c_callback
        self.add_item(c_sel)

        r_sel = discord.ui.RoleSelect(
            placeholder=t(self.lang, "wizard_sel_ping_role", defaut="Select a role to ping (optional)..."), row=1
        )

        async def r_callback(interaction: discord.Interaction):
            self.data["patchnotes"]["ping_role"] = int(r_sel.values[0].id)
            await interaction.response.defer()

        r_sel.callback = r_callback
        self.add_item(r_sel)

        self.add_controls()

    # -------------------------------------------------------------
    # 3. HUB ALERTS
    # -------------------------------------------------------------
    def step_hub_alerts(self):
        c_sel = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder=t(self.lang, "wizard_sel_hub_alerts_chan", defaut="Select the Hub Alerts channel..."),
            row=0,
        )

        async def c_callback(interaction: discord.Interaction):
            self.data["hub_alerts"]["channel_id"] = int(c_sel.values[0].id)
            await interaction.response.defer()

        c_sel.callback = c_callback
        self.add_item(c_sel)

        r_sel = discord.ui.RoleSelect(
            placeholder=t(self.lang, "wizard_sel_ping_role", defaut="Select a role to ping (optional)..."), row=1
        )

        async def r_callback(interaction: discord.Interaction):
            self.data["hub_alerts"]["ping_role"] = int(r_sel.values[0].id)
            await interaction.response.defer()

        r_sel.callback = r_callback
        self.add_item(r_sel)

        self.add_controls()

    # -------------------------------------------------------------
    # 4. CALENDAR
    # -------------------------------------------------------------
    def step_calendar(self):
        c_sel = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder=t(self.lang, "wizard_sel_cal_chan", defaut="Select the event calendar channel..."),
            row=0,
        )

        async def c_callback(interaction: discord.Interaction):
            self.data["calendar"]["channel_id"] = int(c_sel.values[0].id)
            await interaction.response.defer()

        c_sel.callback = c_callback
        self.add_item(c_sel)

        self.add_controls()

    # -------------------------------------------------------------
    # 5. ISLE (Storm Islands)
    # -------------------------------------------------------------
    def step_isle(self):
        c_sel = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder=t(self.lang, "wizard_sel_isle_chan", defaut="Select the Storm Islands channel..."),
            row=0,
        )

        async def c_callback(interaction: discord.Interaction):
            self.data["isle"]["channel_id"] = int(c_sel.values[0].id)
            await interaction.response.defer()

        c_sel.callback = c_callback
        self.add_item(c_sel)

        r_sel = discord.ui.RoleSelect(
            placeholder=t(self.lang, "wizard_sel_ping_role", defaut="Select a role to ping (optional)..."), row=1
        )

        async def r_callback(interaction: discord.Interaction):
            self.data["isle"]["ping_role"] = int(r_sel.values[0].id)
            await interaction.response.defer()

        r_sel.callback = r_callback
        self.add_item(r_sel)

        status_small = "ON" if self.data["isle"]["ping_small"] else "OFF"
        btn_small = discord.ui.Button(
            label=t(
                self.lang, "wizard_btn_small_ping", status=status_small, defaut=f"Ping Small Isles: {status_small}"
            ),
            style=discord.ButtonStyle.success if self.data["isle"]["ping_small"] else discord.ButtonStyle.secondary,
        )

        async def small_cb(interaction: discord.Interaction):
            self.data["isle"]["ping_small"] = not self.data["isle"]["ping_small"]
            new_status = "ON" if self.data["isle"]["ping_small"] else "OFF"
            btn_small.label = t(
                self.lang, "wizard_btn_small_ping", status=new_status, defaut=f"Ping Small Isles: {new_status}"
            )
            btn_small.style = (
                discord.ButtonStyle.success if self.data["isle"]["ping_small"] else discord.ButtonStyle.secondary
            )
            await interaction.response.edit_message(view=self)

        btn_small.callback = small_cb

        status_big = "ON" if self.data["isle"]["ping_big"] else "OFF"
        btn_big = discord.ui.Button(
            label=t(self.lang, "wizard_btn_big_ping", status=status_big, defaut=f"Ping Big Isles: {status_big}"),
            style=discord.ButtonStyle.success if self.data["isle"]["ping_big"] else discord.ButtonStyle.secondary,
        )

        async def big_cb(interaction: discord.Interaction):
            self.data["isle"]["ping_big"] = not self.data["isle"]["ping_big"]
            new_status = "ON" if self.data["isle"]["ping_big"] else "OFF"
            btn_big.label = t(
                self.lang, "wizard_btn_big_ping", status=new_status, defaut=f"Ping Big Isles: {new_status}"
            )
            btn_big.style = (
                discord.ButtonStyle.success if self.data["isle"]["ping_big"] else discord.ButtonStyle.secondary
            )
            await interaction.response.edit_message(view=self)

        btn_big.callback = big_cb

        self.add_controls(extra_buttons=[btn_small, btn_big])

    # -------------------------------------------------------------
    # 6. FORTS (Storm Fortresses)
    # -------------------------------------------------------------
    def step_forts(self):
        c_sel = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder=t(self.lang, "wizard_sel_forts_chan", defaut="Select the Storm Fortresses channel..."),
            row=0,
        )

        async def c_callback(interaction: discord.Interaction):
            self.data["forts"]["channel_id"] = int(c_sel.values[0].id)
            await interaction.response.defer()

        c_sel.callback = c_callback
        self.add_item(c_sel)

        r_sel = discord.ui.RoleSelect(
            placeholder=t(self.lang, "wizard_sel_ping_role", defaut="Select a role to ping (optional)..."), row=1
        )

        async def r_callback(interaction: discord.Interaction):
            self.data["forts"]["ping_role"] = int(r_sel.values[0].id)
            await interaction.response.defer()

        r_sel.callback = r_callback
        self.add_item(r_sel)

        lvl_select = discord.ui.Select(
            placeholder=t(self.lang, "wizard_sel_forts_lvl", defaut="Minimum fortress level..."),
            options=[
                discord.SelectOption(label=t(self.lang, "wizard_lvl_40", defaut="Level 40+"), value="40"),
                discord.SelectOption(label=t(self.lang, "wizard_lvl_60", defaut="Level 60+"), value="60"),
                discord.SelectOption(label=t(self.lang, "wizard_lvl_70", defaut="Level 70+"), value="70", default=True),
                discord.SelectOption(label=t(self.lang, "wizard_lvl_80", defaut="Level 80 only"), value="80"),
            ],
            row=2,
        )

        async def lvl_cb(interaction: discord.Interaction):
            self.data["forts"]["min_level"] = int(lvl_select.values[0])
            await interaction.response.defer()

        lvl_select.callback = lvl_cb
        self.add_item(lvl_select)

        self.add_controls()

    # -------------------------------------------------------------
    # 7. RADAR
    # -------------------------------------------------------------
    def step_radar(self):
        c_sel = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder=t(self.lang, "wizard_sel_radar_chan", defaut="Select the Radar alerts channel..."),
            row=0,
        )

        async def c_callback(interaction: discord.Interaction):
            self.data["radar"]["channel_id"] = int(c_sel.values[0].id)
            await interaction.response.defer()

        c_sel.callback = c_callback
        self.add_item(c_sel)

        r_sel = discord.ui.RoleSelect(
            placeholder=t(self.lang, "wizard_sel_ping_role", defaut="Select a role to ping (optional)..."), row=1
        )

        async def r_callback(interaction: discord.Interaction):
            self.data["radar"]["ping_role"] = int(r_sel.values[0].id)
            await interaction.response.defer()

        r_sel.callback = r_callback
        self.add_item(r_sel)

        self.add_controls()

    # -------------------------------------------------------------
    # 8. SHOP / OFFERS
    # -------------------------------------------------------------
    def step_shop(self):
        c_sel = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder=t(self.lang, "wizard_sel_shop_chan", defaut="Select the Shop Offers & Gifts channel..."),
            row=0,
        )

        async def c_callback(interaction: discord.Interaction):
            self.data["shop"]["channel_id"] = int(c_sel.values[0].id)
            await interaction.response.defer()

        c_sel.callback = c_callback
        self.add_item(c_sel)

        prof_sel = discord.ui.Select(
            placeholder=t(self.lang, "wizard_sel_shop_prof", defaut="Player profile for offers..."),
            options=[
                discord.SelectOption(
                    label=t(self.lang, "wizard_prof_leg", defaut="Legendary (Level 70 / LL 950+)"),
                    value="950",
                    default=True,
                ),
                discord.SelectOption(
                    label=t(self.lang, "wizard_prof_norm", defaut="Normal (Level 70 / No LL)"), value="0"
                ),
            ],
            row=1,
        )

        async def prof_cb(interaction: discord.Interaction):
            self.data["shop"]["profile"] = int(prof_sel.values[0])
            await interaction.response.defer()

        prof_sel.callback = prof_cb
        self.add_item(prof_sel)

        r_sel = discord.ui.RoleSelect(
            placeholder=t(self.lang, "wizard_sel_ping_role", defaut="Select a role to ping (optional)..."), row=2
        )

        async def r_callback(interaction: discord.Interaction):
            self.data["shop"]["ping_role"] = int(r_sel.values[0].id)
            await interaction.response.defer()

        r_sel.callback = r_callback
        self.add_item(r_sel)

        # Ajout du sélecteur de catégories de la boutique
        options_cats = []
        for key, (emoji, name_en) in CAT_MAPPING.items():
            name_local = t(self.lang, f"offres_cat_{key}", defaut=name_en)
            options_cats.append(
                discord.SelectOption(
                    label=name_local, value=key, emoji=emoji, default=(key in self.data["shop"]["categories"])
                )
            )

        cat_sel = discord.ui.Select(
            placeholder=t(self.lang, "wizard_sel_shop_cat", defaut="Select categories to track..."),
            min_values=1,
            max_values=len(options_cats),
            options=options_cats,
            row=3,
        )

        async def cat_cb(interaction: discord.Interaction):
            self.data["shop"]["categories"] = cat_sel.values
            await interaction.response.defer()

        cat_sel.callback = cat_cb
        self.add_item(cat_sel)

        # On décale les boutons de contrôle à la ligne 4 car la ligne 3 est occupée par les catégories
        self.add_controls(is_last=True, ctrl_row=4)

    # -------------------------------------------------------------
    # EMBED GENERATION
    # -------------------------------------------------------------
    def get_progress_color(self) -> discord.Color:
        """Calcule un dégradé du bleu (52, 152, 219) au vert (46, 204, 113)"""
        progress = self.step_idx / max(1, len(self.steps) - 1)
        r = int(52 + (46 - 52) * progress)
        g = int(152 + (204 - 152) * progress)
        b = int(219 + (113 - 219) * progress)
        return discord.Color.from_rgb(r, g, b)

    def get_current_embed(self) -> discord.Embed:
        if self.step_idx == 0:
            embed = discord.Embed(
                title=t(self.lang, "wizard_intro_title", defaut="⚙️ Server Configuration Wizard"),
                description=t(
                    self.lang,
                    "wizard_intro_desc",
                    defaut="Welcome to the Server Setup Wizard!\nThis tool will guide you step-by-step to configure channels, pings, and alerts for your server.\n\nAt each step, you can select options, **Skip ⏭️** to keep your existing settings, or **Disable 🚫** the feature entirely.\n\nClick **Start 🚀** when you're ready!",
                ),
                color=self.get_progress_color(),
            )
            embed.set_footer(text=t(self.lang, "wizard_intro_footer", defaut="GGE Assistant • Configuration Mode"))
            return embed

        idx = self.step_idx
        total = len(self.steps) - 1

        steps_info = [
            (
                t(self.lang, "wizard_news_title", defaut="📢 Official News"),
                t(
                    self.lang,
                    "wizard_news_desc",
                    defaut="Select the channel and an optional role to ping for official game announcements.",
                ),
            ),
            (
                t(self.lang, "wizard_patchnotes_title", defaut="📜 Patchnotes"),
                t(
                    self.lang,
                    "wizard_patchnotes_desc",
                    defaut="Select the channel and an optional role to ping for game updates.",
                ),
            ),
            (
                t(self.lang, "wizard_hub_alerts_title", defaut="⚠️ Hub Alerts"),
                t(
                    self.lang,
                    "wizard_hub_alerts_desc",
                    defaut="Select the channel and an optional role to ping for quick Hub Alerts.",
                ),
            ),
            (
                t(self.lang, "wizard_calendar_title", defaut="📅 Event Calendar"),
                t(
                    self.lang,
                    "wizard_calendar_desc",
                    defaut="Select the channel. To track a specific alliance later, use `/calendar track`.",
                ),
            ),
            (
                t(self.lang, "wizard_isle_title", defaut="🏝️ Storm Islands"),
                t(
                    self.lang,
                    "wizard_isle_desc",
                    defaut="Select the channel, ping role, and toggle pings for small/big islands.",
                ),
            ),
            (
                t(self.lang, "wizard_forts_title", defaut="🏰 Storm Fortresses"),
                t(self.lang, "wizard_forts_desc", defaut="Select the channel, ping role, and minimum fortress level."),
            ),
            (
                t(self.lang, "wizard_radar_title", defaut="🛰️ Radar & Targets"),
                t(
                    self.lang,
                    "wizard_radar_desc",
                    defaut="Select the channel and ping role for radar alerts. To track alliances later, use `/radar server add_alliance`.",
                ),
            ),
            (
                t(self.lang, "wizard_shop_title", defaut="🛒 Shop Offers & Gifts"),
                t(
                    self.lang,
                    "wizard_shop_desc",
                    defaut="Select the channel, player profile, ping role, and categories for shop alerts.",
                ),
            ),
        ]

        title, desc = steps_info[self.step_idx - 1]
        embed = discord.Embed(
            title=f"Server Setup ({idx}/{total}) — {title}",
            description=desc,
            color=self.get_progress_color(),
        )
        embed.set_footer(
            text=t(
                self.lang,
                "wizard_footer",
                defaut="GGE Assistant • Select your options then click Next, Disable, or Skip.",
            )
        )
        return embed

    # -------------------------------------------------------------
    # FINAL SAVE
    # -------------------------------------------------------------
    async def finish_setup(self, interaction: discord.Interaction):
        self.stop()
        guild_id = str(self.guild.id)
        os.makedirs(self.servers_dir, exist_ok=True)
        recap = []

        # 1. Hub / News / Patchnotes / Alerts (hub_community.json)
        hub_file = self.servers_dir / "hub_community.json"
        async with get_file_lock(hub_file):
            hub_data = {"guilds": {}}
            if os.path.exists(hub_file):
                try:
                    hub_data = await async_json_load(hub_file) or {"guilds": {}}
                except Exception:
                    pass

            if "guilds" not in hub_data:
                hub_data["guilds"] = {}
            if guild_id not in hub_data["guilds"]:
                hub_data["guilds"][guild_id] = {}

            hub_data["guilds"][guild_id]["gge_server"] = self.gge_server
            hub_data["guilds"][guild_id]["langue"] = self.lang

            for old_key in ["news_channel_id", "news_ping_role", "patchnotes_channel_id", "patchnotes_ping_role"]:
                hub_data["guilds"][guild_id].pop(old_key, None)

            # News
            if self.data["news"]["disabled"]:
                hub_data["guilds"][guild_id].pop("news", None)
                recap.append("• **News:** *Désactivé*")
            elif self.data["news"]["channel_id"]:
                hub_data["guilds"][guild_id]["news"] = {
                    "channel_id": self.data["news"]["channel_id"],
                    "role": self.format_ping(self.data["news"]["ping_role"]),
                }
                c = self.guild.get_channel(self.data["news"]["channel_id"])
                recap.append(f"• **News:** {c.mention if c else 'Set'}")

            # Patchnotes
            if self.data["patchnotes"]["disabled"]:
                hub_data["guilds"][guild_id].pop("patchnotes", None)
                recap.append("• **Patchnotes:** *Désactivé*")
            elif self.data["patchnotes"]["channel_id"]:
                hub_data["guilds"][guild_id]["patchnotes"] = {
                    "channel_id": self.data["patchnotes"]["channel_id"],
                    "role": self.format_ping(self.data["patchnotes"]["ping_role"]),
                }
                c = self.guild.get_channel(self.data["patchnotes"]["channel_id"])
                recap.append(f"• **Patchnotes:** {c.mention if c else 'Set'}")

            # Hub Alerts
            if self.data["hub_alerts"]["disabled"]:
                hub_data["guilds"][guild_id].pop("alerts", None)
                recap.append("• **Hub Alerts:** *Désactivé*")
            elif self.data["hub_alerts"]["channel_id"]:
                hub_data["guilds"][guild_id]["alerts"] = {
                    "channel_id": self.data["hub_alerts"]["channel_id"],
                    "role": self.format_ping(self.data["hub_alerts"]["ping_role"]),
                }
                c = self.guild.get_channel(self.data["hub_alerts"]["channel_id"])
                recap.append(f"• **Hub Alerts:** {c.mention if c else 'Set'}")

            await async_json_dump(hub_file, hub_data, indent=4)

        # 2. Calendar (calendrier.json)
        cal_file = self.servers_dir / "calendrier.json"
        async with get_file_lock(cal_file):
            cal_data = {"guilds": {}, "notified": [], "cached_events": []}
            if os.path.exists(cal_file):
                try:
                    cal_data = await async_json_load(cal_file) or {"guilds": {}, "notified": [], "cached_events": []}
                except Exception:
                    pass

            if "guilds" not in cal_data:
                cal_data["guilds"] = {}
            if guild_id not in cal_data["guilds"]:
                cal_data["guilds"][guild_id] = {}

            cal_data["guilds"][guild_id]["gge_server"] = self.gge_server
            cal_data["guilds"][guild_id]["langue"] = self.lang
            cal_data["guilds"][guild_id].setdefault("tracked_alliances", [])

            if self.data["calendar"]["disabled"]:
                cal_data["guilds"][guild_id].pop("channel_id", None)
                cal_data["guilds"][guild_id].pop("ping_role", None)
                recap.append("• **Calendar:** *Désactivé*")
            elif self.data["calendar"]["channel_id"]:
                cal_data["guilds"][guild_id]["channel_id"] = self.data["calendar"]["channel_id"]
                cal_data["guilds"][guild_id].pop("ping_role", None)
                c = self.guild.get_channel(self.data["calendar"]["channel_id"])
                recap.append(f"• **Calendar:** {c.mention if c else 'Set'}")

            await async_json_dump(cal_file, cal_data, indent=4)

        # 3 & 4. Storm Isle & Forts (storm_alerts.json)
        storm_file = self.servers_dir / "storm_alerts.json"
        async with get_file_lock(storm_file):
            storm_data = {"guilds": {}}
            if os.path.exists(storm_file):
                try:
                    storm_data = await async_json_load(storm_file) or {"guilds": {}}
                except Exception:
                    pass

            if "guilds" not in storm_data:
                storm_data["guilds"] = {}
            if guild_id not in storm_data["guilds"]:
                storm_data["guilds"][guild_id] = {}

            storm_data["guilds"][guild_id]["gge_server"] = self.gge_server
            storm_data["guilds"][guild_id]["langue"] = self.lang

            # Isle
            if self.data["isle"]["disabled"]:
                storm_data["guilds"][guild_id].pop("isle_channel_id", None)
                recap.append("• **Storm Isles:** *Désactivé*")
            elif self.data["isle"]["channel_id"]:
                storm_data["guilds"][guild_id]["isle_channel_id"] = self.data["isle"]["channel_id"]
                storm_data["guilds"][guild_id]["isle_ping_role"] = self.format_ping(self.data["isle"]["ping_role"])
                storm_data["guilds"][guild_id]["isle_ping_small"] = self.data["isle"]["ping_small"]
                storm_data["guilds"][guild_id]["isle_ping_big"] = self.data["isle"]["ping_big"]

                c_isle = self.guild.get_channel(self.data["isle"]["channel_id"])
                recap.append(f"• **Storm Isles:** {c_isle.mention if c_isle else 'Set'}")

            # Forts
            if self.data["forts"]["disabled"]:
                storm_data["guilds"][guild_id].pop("fort_channel_id", None)
                recap.append("• **Storm Fortresses:** *Désactivé*")
            elif self.data["forts"]["channel_id"]:
                storm_data["guilds"][guild_id]["fort_channel_id"] = self.data["forts"]["channel_id"]
                storm_data["guilds"][guild_id]["fort_ping_role"] = self.format_ping(self.data["forts"]["ping_role"])
                storm_data["guilds"][guild_id]["fort_min_level"] = self.data["forts"]["min_level"]

                c_forts = self.guild.get_channel(self.data["forts"]["channel_id"])
                recap.append(
                    f"• **Storm Fortresses:** {c_forts.mention if c_forts else 'Set'} (Lvl: `{self.data['forts']['min_level']}+`)"
                )

            await async_json_dump(storm_file, storm_data, indent=4)

        # 5. Radar (/joueurs/surveillance.json)
        radar_dir = self.servers_dir.parent / "joueurs"
        os.makedirs(radar_dir, exist_ok=True)
        radar_file = radar_dir / "surveillance.json"

        async with get_file_lock(radar_file):
            radar_data = {"players": {}, "alliances": {}, "guild_configs": {}}
            if os.path.exists(radar_file):
                try:
                    loaded_data = await async_json_load(radar_file)
                    if loaded_data:
                        radar_data.update(loaded_data)
                except Exception:
                    pass

            radar_data.setdefault("players", {})
            radar_data.setdefault("alliances", {})
            radar_data.setdefault("guild_configs", {})

            if guild_id not in radar_data["guild_configs"]:
                radar_data["guild_configs"][guild_id] = {}

            radar_data["guild_configs"][guild_id]["serveur"] = self.gge_server
            radar_data["guild_configs"][guild_id]["langue"] = self.lang

            if self.data["radar"]["disabled"]:
                radar_data["guild_configs"][guild_id].pop("channel_id", None)
                radar_data["guild_configs"][guild_id].pop("ping_role", None)
                recap.append("• **Radar:** *Désactivé*")
            elif self.data["radar"]["channel_id"]:
                radar_data["guild_configs"][guild_id]["channel_id"] = self.data["radar"]["channel_id"]
                radar_data["guild_configs"][guild_id]["ping_role"] = self.format_ping(self.data["radar"]["ping_role"])

                c = self.guild.get_channel(self.data["radar"]["channel_id"])
                recap.append(f"• **Radar:** {c.mention if c else 'Set'}")

            await async_json_dump(radar_file, radar_data, indent=4)

        # 6. Offers / Shop (offers_alerts.json)
        alerts_file = self.servers_dir / "offers_alerts.json"
        async with get_file_lock(alerts_file):
            alerts_data = {"guilds": {}}
            if os.path.exists(alerts_file):
                try:
                    alerts_data = await async_json_load(alerts_file) or {"guilds": {}}
                except Exception:
                    pass

            if "guilds" not in alerts_data:
                alerts_data["guilds"] = {}
            if guild_id not in alerts_data["guilds"]:
                alerts_data["guilds"][guild_id] = {}

            alerts_data["guilds"][guild_id]["gge_server"] = self.gge_server
            alerts_data["guilds"][guild_id]["lang"] = "EN"

            if self.data["shop"]["disabled"]:
                alerts_data["guilds"][guild_id].pop("channel_id", None)
                recap.append("• **Shop Offers:** *Désactivé*")
            elif self.data["shop"]["channel_id"]:
                alerts_data["guilds"][guild_id]["channel_id"] = self.data["shop"]["channel_id"]
                alerts_data["guilds"][guild_id]["legendary"] = self.data["shop"]["profile"]
                alerts_data["guilds"][guild_id]["categories"] = self.data["shop"]["categories"]
                alerts_data["guilds"][guild_id]["ping_role"] = self.format_ping(self.data["shop"]["ping_role"])

                c = self.guild.get_channel(self.data["shop"]["channel_id"])
                prof_label = "LL 950+" if self.data["shop"]["profile"] == 950 else "Normal (LL 0)"
                recap.append(f"• **Shop Offers:** {c.mention if c else 'Set'} (`{prof_label}`)")

                # Lancement du scan de bienvenue
                offers_cog = self.cog.bot.get_cog("OffersCog")
                if offers_cog and hasattr(offers_cog, "trigger_immediate_scan") and c:
                    asyncio.create_task(offers_cog.trigger_immediate_scan(c, guild_id, alerts_data["guilds"][guild_id]))

            await async_json_dump(alerts_file, alerts_data, indent=4)

        if not recap:
            recap.append(t(self.lang, "wizard_recap_none", defaut="*No channels were modified.*"))

        embed = discord.Embed(
            title=t(self.lang, "wizard_done_title", defaut="✅ Server Configuration Saved"),
            description="\n".join(recap),
            color=discord.Color.green(),
        )
        embed.set_footer(
            text=t(self.lang, "wizard_done_footer", defaut="GGE Assistant • Settings Updated"),
            icon_url=self.cog.bot.user.display_avatar.url,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)


class ConfigurationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ==========================================
    # 🔍 SERVER AUTOCOMPLETE
    # ==========================================
    async def server_autocomplete(self, interaction: discord.Interaction, current: str):
        langue, _ = await get_server_config(interaction)

        lbl_mobile = t(langue, "platform_mobile", defaut="Mobile")
        lbl_pc = t(langue, "platform_pc", defaut="PC")
        lbl_ko = t(langue, "config_unsupported", defaut="Unsupported")
        lbl_unk = t(langue, "config_unknown", defaut="Unknown")

        choix_valides = []
        choix_invalides = []

        config_data = await load_configuration_async()
        servers_info = config_data.get("servers_info", {})

        for srv, data in servers_info.items():
            if current.lower() in srv.lower():
                is_mobile = srv.startswith("E4K_")
                emoji_platform = "📱" if is_mobile else "💻"
                platform_tag = lbl_mobile if is_mobile else lbl_pc

                is_enabled = data.get("enabled", False)

                if is_enabled:
                    full_name = f"{emoji_platform} {srv} ({platform_tag})"[:100]
                    choix_valides.append(app_commands.Choice(name=full_name, value=srv))
                else:
                    full_name = f"🔴 {srv} ({lbl_ko})"[:100]
                    choix_invalides.append(app_commands.Choice(name=full_name, value=srv))

        choix_valides.sort(key=lambda x: x.name)
        choix_invalides.sort(key=lambda x: x.name)

        choix = (choix_valides + choix_invalides)[:25]

        if not choix and current:
            safe_value = current.upper()[:100]
            full_name = f"❓ {safe_value} ({lbl_unk})"[:100]
            choix.append(app_commands.Choice(name=full_name, value=safe_value))

        return choix

    # ==========================================
    # ⚙️ EMERGENCY SCAN ENGINE
    # ==========================================
    def trigger_emergency_scan(self, serveur_upper: str) -> bool:
        dossier_serveur = BASE_DATA_PATH / "server_scans" / serveur_upper
        if dossier_serveur.exists():
            return False

        async def scan_urgence_background(srv):
            flag = BASE_DATA_PATH / "scan.flag"

            def _create_flag():
                flag.touch(exist_ok=True)

            def _remove_flag():
                if flag.exists():
                    flag.unlink()

            await asyncio.to_thread(_create_flag)
            try:
                proc1 = await asyncio.create_subprocess_exec(
                    "python3", "scanners/server_scanner.py", srv, cwd=str(BASE_DIR)
                )
                await proc1.wait()
                proc2 = await asyncio.create_subprocess_exec(
                    "python3", "scanners/murs_scanner.py", srv, cwd=str(BASE_DIR)
                )
                await proc2.wait()
            except Exception as e:
                logger.error(f"❌ Emergency scan error: {e}")
            finally:
                await asyncio.to_thread(_remove_flag)

        self.bot.loop.create_task(scan_urgence_background(serveur_upper))
        return True

    # ==========================================
    # 👤 USER SETUP
    # ==========================================
    @app_commands.command(name="setup", description="Configure your language and your primary GGE server.")
    @app_commands.describe(language="Your primary language", server="Your Goodgame Empire server")
    @app_commands.choices(
        language=[
            app_commands.Choice(name="🇫🇷 Français", value="fr"),
            app_commands.Choice(name="🇬🇧 English", value="en"),
            app_commands.Choice(name="🇩🇪 Deutsch", value="de"),
        ]
    )
    @app_commands.autocomplete(server=server_autocomplete)
    async def c_setup(self, interaction: discord.Interaction, language: app_commands.Choice[str], server: str):

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            logger.warning(f"❌ Defer failed on /{interaction.command.name}: {e}")
            return

        serveur_upper = server.upper()

        config_data = await load_configuration_async()
        servers_info = config_data.get("servers_info", {})
        srv_data = servers_info.get(serveur_upper, {})

        is_supported = srv_data.get("enabled", False)

        if not is_supported:
            msg_erreur = t(
                language.value,
                "error_unsupported_server",
                serveur=serveur_upper,
                defaut="{e_error} **Error**: The server `{serveur}` is not supported by the API at the moment.",
            )
            return await interaction.followup.send(msg_erreur)

        user_id = str(interaction.user.id)
        data = await load_users_config()
        old_profile = data.get(user_id) or {}
        data[user_id] = {"nom_discord": interaction.user.name, "langue": language.value, "gge_server": serveur_upper}
        await save_users_config(data)
        clear_config_cache()

        obs.record_guild_event(
            "setup",
            guild=interaction.guild,
            user_id=interaction.user.id,
            lang=language.value,
            gge_server=serveur_upper,
            old_value=f"{old_profile.get('langue', '')}/{old_profile.get('gge_server', '')}".strip("/"),
            new_value=f"{language.value}/{serveur_upper}",
        )
        obs.upsert_user(
            interaction.user.id,
            user_name=interaction.user.name,
            lang=language.value,
            gge_server=serveur_upper,
        )

        titre = t(language.value, "config_dm_setup_title", defaut="{e_check} Profile Configured")
        desc = t(
            language.value,
            "config_dm_setup_desc",
            defaut="Your personal profile has been successfully configured!\nThe bot will use these settings for you on any Discord server.",
        )

        if self.trigger_emergency_scan(serveur_upper):
            desc += t(
                language.value,
                "config_scan_init",
                srv=serveur_upper,
                defaut="\n\n{e_hourglass} *An initialization scan for {srv} has just been launched in the background. The radar will be available in 1-2 minutes.*",
            )

        embed = discord.Embed(title=titre, description=desc, color=discord.Color.blue())

        lbl_lang = t(language.value, "config_field_lang", defaut="Language")
        lbl_srv = t(language.value, "config_field_server", defaut="GGE Server")

        is_mobile = serveur_upper.startswith("E4K_")
        pastille = "📱" if is_mobile else "💻"

        embed.add_field(name=lbl_lang, value=language.name, inline=False)
        embed.add_field(name=lbl_srv, value=f"{pastille} `{serveur_upper}`", inline=False)

        await interaction.followup.send(embed=embed)

    # ==========================================
    # ⚙️ SERVER SETUP (STANDALONE COMMAND)
    # ==========================================
    @app_commands.command(
        name="server-setup",
        description="Launch the interactive wizard to configure alerts, channels and notifications.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def server_setup(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ This command must be executed within a Discord server.",
                ephemeral=True,
            )

        langue, gge_server = await get_server_config(interaction)
        wizard = ServerSetupWizard(self, interaction, langue, gge_server)

        await interaction.response.send_message(
            embed=wizard.get_current_embed(),
            view=wizard,
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(ConfigurationCog(bot))
