import asyncio
import json
import logging
import math
import random
import urllib.parse
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

import utils
from utils import (
    BASE_DATA_PATH,
    CONFIG_DIR,
    alliance_autocomplete,
    format_num,
    get_api_headers,
    get_server_config,
    joueur_autocomplete,
    prompt_vote_if_lucky,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")

# ========================================================
# 🎛️ UI COMPONENTS : SEARCH ENGINE WIZARD
# ========================================================
class WizardButton(discord.ui.Button):
    def __init__(self, label, step_target, wizard, action="ignore", style=discord.ButtonStyle.primary, row=0):
        super().__init__(label=label, style=style, row=row)
        self.step_target = step_target
        self.wizard = wizard
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        if self.action == "ignore_lvl":
            self.wizard.config["lvl_min"], self.wizard.config["lvl_max"] = -1, -1
        elif self.action == "ignore_pp":
            self.wizard.config["pp_min"], self.wizard.config["pp_max"] = -1, -1
        elif self.action == "ignore_dist":
            self.wizard.config["dist_min"], self.wizard.config["dist_max"] = -1, -1
        elif self.action == "ignore_honor":
            self.wizard.config["honor_min"], self.wizard.config["honor_max"] = -1, -1
        elif self.action == "ignore_glory":
            self.wizard.config["glory_min"], self.wizard.config["glory_max"] = -1, -1
        elif self.action == "ignore_loot":
            self.wizard.config["loot_min"], self.wizard.config["loot_max"] = -1, -1

        self.wizard.step = self.step_target
        await self.wizard.update_view(interaction)

class CustomMinMaxModal(discord.ui.Modal):
    def __init__(self, title, ph_min, ph_max, wizard, key_min, key_max, next_step, langue="en"):
        super().__init__(title=title)
        self.wizard = wizard
        self.key_min = key_min
        self.key_max = key_max
        self.next_step = next_step
        self.langue = langue
        
        lbl_min = t(langue, "wizard_modal_min", defaut="Minimum (-1 = No min)")
        lbl_max = t(langue, "wizard_modal_max", defaut="Maximum (-1 = No max)")

        self.val_min = discord.ui.TextInput(
            label=lbl_min,
            placeholder=ph_min,
            required=True,
            default=str(wizard.config.get(key_min, -1))
        )
        self.val_max = discord.ui.TextInput(
            label=lbl_max,
            placeholder=ph_max,
            required=True,
            default=str(wizard.config.get(key_max, -1))
        )
        self.add_item(self.val_min)
        self.add_item(self.val_max)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            self.wizard.config[self.key_min] = int(self.val_min.value.replace(" ", ""))
            self.wizard.config[self.key_max] = int(self.val_max.value.replace(" ", ""))
            self.wizard.step = self.next_step
            await self.wizard.update_view(interaction)
        except ValueError:
            err = t(self.langue, "wizard_err_nan", defaut="❌ Invalid values. Please enter only numbers (-1 to ignore).")
            await interaction.response.send_message(err, ephemeral=True)

class CustomTextModal(discord.ui.Modal):
    def __init__(self, title, label, placeholder, wizard, key_to_update, next_step):
        super().__init__(title=title)
        self.wizard = wizard
        self.key_to_update = key_to_update
        self.next_step = next_step
        
        self.val_input = discord.ui.TextInput(
            label=label,
            placeholder=placeholder,
            required=False,
            max_length=300,
            default=wizard.config.get(key_to_update, "")
        )
        self.add_item(self.val_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.wizard.config[self.key_to_update] = self.val_input.value
        self.wizard.step = self.next_step
        await self.wizard.update_view(interaction)

class ToggleButton(discord.ui.Button):
    def __init__(self, key, wizard, label_on, label_off, style_on=discord.ButtonStyle.success, style_off=discord.ButtonStyle.secondary, row=0):
        self.wizard = wizard
        self.key = key
        self.label_on = label_on
        self.label_off = label_off
        is_on = self.wizard.config.get(key, False)
        super().__init__(
            label=label_on if is_on else label_off,
            style=style_on if is_on else style_off,
            row=row
        )

    async def callback(self, interaction: discord.Interaction):
        self.wizard.config[self.key] = not self.wizard.config[self.key]
        is_on = self.wizard.config[self.key]
        self.label = self.label_on if is_on else self.label_off
        self.style = discord.ButtonStyle.success if is_on else discord.ButtonStyle.secondary
        await self.wizard.update_view(interaction)

class CycleButton(discord.ui.Button):
    def __init__(self, key, wizard, states, row=0):
        self.wizard = wizard
        self.key = key
        self.states = states 
        
        current_val = self.wizard.config.get(key, states[0]["value"])
        current_state = next((s for s in states if s["value"] == current_val), states[0])
        
        super().__init__(label=current_state["label"], style=current_state["style"], row=row)

    async def callback(self, interaction: discord.Interaction):
        current_val = self.wizard.config.get(self.key, self.states[0]["value"])
        current_idx = next((i for i, s in enumerate(self.states) if s["value"] == current_val), 0)
        next_idx = (current_idx + 1) % len(self.states)
        next_state = self.states[next_idx]
        
        self.wizard.config[self.key] = next_state["value"]
        self.label = next_state["label"]
        self.style = next_state["style"]
        await self.wizard.update_view(interaction)

class TargetWizard(discord.ui.View):
    def __init__(self, user_id, langue="en", current_config=None):
        super().__init__(timeout=600)
        self.user_id = user_id
        self.langue = langue
        self.config = current_config or {
            "lvl_min": -1, "lvl_max": -1,
            "pp_min": -1, "pp_max": -1,
            "dist_min": -1, "dist_max": -1,
            "honor_min": -1, "honor_max": -1,
            "glory_min": -1, "glory_max": -1,
            "loot_min": -1, "loot_max": -1,
            "show_doves": False, 
            "only_with_alliance": False,
            "excluded_alliances": "",
            "activity_filter": "all"
        }
        self.step = 1

    async def start(self, interaction: discord.Interaction, content=None):
        await self.update_view(interaction, initial=True, content=content)

    async def update_view(self, interaction: discord.Interaction, initial=False, content=None):
        self.clear_items()
        embed = discord.Embed(color=discord.Color.blue())

        lbl_ignore = t(self.langue, "wizard_btn_ignore", defaut="♾️ Doesn't matter")
        lbl_custom = t(self.langue, "wizard_btn_custom", defaut="⌨️ Enter Min / Max")
        lbl_back = t(self.langue, "wizard_btn_back", defaut="◀ Back")

        if self.step == 1:
            embed.title = t(self.langue, "wizard_s1_title", langue=self.langue, defaut="Step 1/7 : Absolute Level")
            embed.description = t(self.langue, "wizard_s1_desc", langue=self.langue, defaut=(
                "What level range are you looking for?\n"
                "*(Always add **70** for Legendary levels!)*\n"
                "• Classic Lvl 20 ➔ **20**\n"
                "• Legendary Lvl 100 ➔ **170** (70+100)"
            ))
            
            self.add_item(WizardButton(lbl_ignore, 2, self, action="ignore_lvl"))
            btn_custom = discord.ui.Button(label=lbl_custom, style=discord.ButtonStyle.secondary)
            async def cb_custom(i):
                titre = t(self.langue, "wizard_m1_title", langue=self.langue, defaut="Level Filter")
                ph1 = t(self.langue, "wizard_m1_ph1", langue=self.langue, defaut="Ex: 20 (Classic)")
                ph2 = t(self.langue, "wizard_m1_ph2", langue=self.langue, defaut="Ex: 570 (Legendary)")
                await i.response.send_modal(CustomMinMaxModal(titre, ph1, ph2, self, "lvl_min", "lvl_max", 2, self.langue))
            btn_custom.callback = cb_custom
            self.add_item(btn_custom)

        elif self.step == 2:
            embed.title = t(self.langue, "wizard_s2_title", langue=self.langue, defaut="Step 2/7 : Might")
            embed.description = t(self.langue, "wizard_s2_desc", langue=self.langue, defaut="What might range are you looking for?")
            
            self.add_item(WizardButton(lbl_ignore, 3, self, action="ignore_pp"))
            btn_custom = discord.ui.Button(label=lbl_custom, style=discord.ButtonStyle.secondary)
            async def cb_custom(i):
                titre = t(self.langue, "wizard_m2_title", langue=self.langue, defaut="Might Filter")
                await i.response.send_modal(CustomMinMaxModal(titre, "Ex: 1000000", "Ex: 5000000", self, "pp_min", "pp_max", 3, self.langue))
            btn_custom.callback = cb_custom
            self.add_item(btn_custom)
            self.add_item(WizardButton(lbl_back, 1, self, action="back", style=discord.ButtonStyle.secondary, row=1))

        elif self.step == 3:
            embed.title = t(self.langue, "wizard_s3_title", langue=self.langue, defaut="Step 3/7 : Distance")
            embed.description = t(self.langue, "wizard_s3_desc", langue=self.langue, defaut="How far from your main castle?")
            
            self.add_item(WizardButton(lbl_ignore, 4, self, action="ignore_dist"))
            btn_custom = discord.ui.Button(label=lbl_custom, style=discord.ButtonStyle.secondary)
            async def cb_custom(i):
                titre = t(self.langue, "wizard_m3_title", langue=self.langue, defaut="Distance Filter")
                await i.response.send_modal(CustomMinMaxModal(titre, "Ex: 10", "Ex: 150", self, "dist_min", "dist_max", 4, self.langue))
            btn_custom.callback = cb_custom
            self.add_item(btn_custom)
            self.add_item(WizardButton(lbl_back, 2, self, action="back", style=discord.ButtonStyle.secondary, row=1))

        elif self.step == 4:
            embed.title = t(self.langue, "wizard_s4_title", langue=self.langue, defaut="Step 4/7 : Honor")
            embed.description = t(self.langue, "wizard_s4_desc", langue=self.langue, defaut="Are you looking for a specific amount of Honor?")
            
            self.add_item(WizardButton(lbl_ignore, 5, self, action="ignore_honor"))
            btn_custom = discord.ui.Button(label=lbl_custom, style=discord.ButtonStyle.secondary)
            async def cb_custom(i):
                titre = t(self.langue, "wizard_m4_title", langue=self.langue, defaut="Honor Filter")
                await i.response.send_modal(CustomMinMaxModal(titre, "Ex: 1000", "Ex: 99999", self, "honor_min", "honor_max", 5, self.langue))
            btn_custom.callback = cb_custom
            self.add_item(btn_custom)
            self.add_item(WizardButton(lbl_back, 3, self, action="back", style=discord.ButtonStyle.secondary, row=1))

        elif self.step == 5:
            embed.title = t(self.langue, "wizard_s5_title", langue=self.langue, defaut="Step 5/7 : Glory")
            embed.description = t(self.langue, "wizard_s5_desc", langue=self.langue, defaut="Are you looking for a specific amount of Glory?")
            
            self.add_item(WizardButton(lbl_ignore, 6, self, action="ignore_glory"))
            btn_custom = discord.ui.Button(label=lbl_custom, style=discord.ButtonStyle.secondary)
            async def cb_custom(i):
                titre = t(self.langue, "wizard_m5_title", langue=self.langue, defaut="Glory Filter")
                await i.response.send_modal(CustomMinMaxModal(titre, "Ex: 10000", "Ex: 500000", self, "glory_min", "glory_max", 6, self.langue))
            btn_custom.callback = cb_custom
            self.add_item(btn_custom)
            self.add_item(WizardButton(lbl_back, 4, self, action="back", style=discord.ButtonStyle.secondary, row=1))

        elif self.step == 6:
            embed.title = t(self.langue, "wizard_s6_title", langue=self.langue, defaut="Step 6/7 : Loot")
            embed.description = t(self.langue, "wizard_s6_desc", langue=self.langue, defaut="Want to target players with available resources?")
            
            self.add_item(WizardButton(lbl_ignore, 7, self, action="ignore_loot"))
            btn_custom = discord.ui.Button(label=lbl_custom, style=discord.ButtonStyle.secondary)
            async def cb_custom(i):
                titre = t(self.langue, "wizard_m6_title", langue=self.langue, defaut="Loot Filter")
                await i.response.send_modal(CustomMinMaxModal(titre, "Ex: 50000", "Ex: 9999999", self, "loot_min", "loot_max", 7, self.langue))
            btn_custom.callback = cb_custom
            self.add_item(btn_custom)
            self.add_item(WizardButton(lbl_back, 5, self, action="back", style=discord.ButtonStyle.secondary, row=1))

        elif self.step == 7:
            embed.title = t(self.langue, "wizard_s7_title", langue=self.langue, defaut="Step 7/7 : Advanced Filters")
            embed.description = t(self.langue, "wizard_s7_desc", langue=self.langue, defaut="Final adjustments before saving:")
            
            d_on = t(self.langue, "wizard_btn_dove_on", langue=self.langue, defaut="Doves: Included")
            d_off = t(self.langue, "wizard_btn_dove_off", langue=self.langue, defaut="Doves: Hidden")
            self.add_item(ToggleButton("show_doves", self, d_on, d_off))
            
            a_on = t(self.langue, "wizard_btn_alli_on", langue=self.langue, defaut="Alliance: Mandatory")
            a_off = t(self.langue, "wizard_btn_alli_off", langue=self.langue, defaut="Alliance: Not required")
            self.add_item(ToggleButton("only_with_alliance", self, a_on, a_off))
            
            lbl_all = t(self.langue, "wizard_btn_act_all", langue=self.langue, defaut="Active & Ruins")
            lbl_act = t(self.langue, "wizard_btn_act_on", langue=self.langue, defaut="Active Players (Only)")
            lbl_inact = t(self.langue, "wizard_btn_act_off", langue=self.langue, defaut="Ruins (Only)")
            
            activity_states = [
                {"label": lbl_all, "value": "all", "style": discord.ButtonStyle.primary},
                {"label": lbl_act, "value": "active", "style": discord.ButtonStyle.success},
                {"label": lbl_inact, "value": "inactive", "style": discord.ButtonStyle.secondary}
            ]
            self.add_item(CycleButton("activity_filter", self, activity_states, row=1))
            
            lbl_excl = t(self.langue, "wizard_btn_exclude", langue=self.langue, defaut="Alliances to exclude")
            btn_excl = discord.ui.Button(label=lbl_excl, style=discord.ButtonStyle.secondary, row=1)
            async def cb_excl(i):
                titre = t(self.langue, "wizard_m7_title", langue=self.langue, defaut="Exclude alliances")
                lbl = t(self.langue, "wizard_m7_lbl", langue=self.langue, defaut="Names (comma separated)")
                ph = t(self.langue, "wizard_m7_ph", langue=self.langue, defaut="Ex: The Wolves, Academy")
                await i.response.send_modal(CustomTextModal(titre, lbl, ph, self, "excluded_alliances", 7))
            btn_excl.callback = cb_excl
            self.add_item(btn_excl)
            
            lbl_save = t(self.langue, "wizard_btn_save", langue=self.langue, defaut="Save Filter")
            b_finish = discord.ui.Button(label=lbl_save, style=discord.ButtonStyle.primary, row=2)
            async def finish_cb(i):
                self.step = 8
                self.save_config()
                await self.update_view(i)
            b_finish.callback = finish_cb
            self.add_item(b_finish)
            
            self.add_item(WizardButton(lbl_back, 6, self, action="back", style=discord.ButtonStyle.secondary, row=2))

        elif self.step == 8:
            embed = self.generate_summary_embed()
            embed.color = discord.Color.green()
            
            lbl_edit = t(self.langue, "wizard_btn_edit", langue=self.langue, defaut="Edit Filter")
            self.add_item(WizardButton(lbl_edit, 1, self, action="back", style=discord.ButtonStyle.secondary))
            
            lbl_close = t(self.langue, "wizard_btn_close", langue=self.langue, defaut="Close")
            b_close = discord.ui.Button(label=lbl_close, style=discord.ButtonStyle.danger)
            async def close_cb(i):
                msg_fermeture = t(self.langue, "wizard_msg_done", langue=self.langue, defaut="Search Engine configured. Run /target search to use it.")
                await i.response.edit_message(content=msg_fermeture, embed=None, view=None)
            b_close.callback = close_cb
            self.add_item(b_close)

        if initial:
            await interaction.response.send_message(content=content, embed=embed, view=self, ephemeral=True)
        else:
            await interaction.response.edit_message(content=None, embed=embed, view=self)

    def save_config(self):
        path_users = CONFIG_DIR / 'target.json'
        try:
            data = {}
            if path_users.exists():
                with open(path_users, encoding='utf-8') as f:
                    data = json.load(f)
            data.setdefault(self.user_id, {})["custom_rules"] = self.config
            with open(path_users, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            utils.clear_config_cache()
        except Exception as e:
            logger.error(f"Error saving user config : {e}")

    def generate_summary_embed(self):
        titre = t(self.langue, "wizard_sum_title", langue=self.langue, defaut="Search Engine Ready!")
        desc = t(self.langue, "wizard_sum_desc", langue=self.langue, defaut="Here are your radar scan criteria:")
        embed = discord.Embed(title=titre, description=desc)
        
        def fmt_val(val):
            return "♾️" if val == -1 else format_num(val)
        
        txt_yes = t(self.langue, "wizard_val_yes", langue=self.langue, defaut="Yes")
        txt_no = t(self.langue, "wizard_val_no", langue=self.langue, defaut="No")
        
        txt_doves = txt_yes if self.config.get("show_doves") else txt_no
        txt_alli = txt_yes if self.config.get("only_with_alliance") else txt_no
        txt_excl = self.config.get("excluded_alliances", "") or t(self.langue, "wizard_val_none", langue=self.langue, defaut="None")
        
        act = self.config.get("activity_filter", "all")
        if act == "active": txt_act = t(self.langue, "wizard_val_act", langue=self.langue, defaut="Active only")
        elif act == "inactive": txt_act = t(self.langue, "wizard_val_inact", langue=self.langue, defaut="Ruins only")
        else: txt_act = t(self.langue, "wizard_val_all", langue=self.langue, defaut="Active & Ruins")

        l_lvl = t(self.langue, "wizard_lbl_lvl", langue=self.langue, defaut="Levels")
        l_pp = t(self.langue, "wizard_lbl_pp", langue=self.langue, defaut="Might")
        l_dist = t(self.langue, "wizard_lbl_dist", langue=self.langue, defaut="Distance")
        f1_val = (
            f"**{l_lvl}** : {fmt_val(self.config.get('lvl_min', -1))} - {fmt_val(self.config.get('lvl_max', -1))}\n"
            f"**{l_pp}** : {fmt_val(self.config.get('pp_min', -1))} - {fmt_val(self.config.get('pp_max', -1))}\n"
            f"**{l_dist}** : {fmt_val(self.config.get('dist_min', -1))} - {fmt_val(self.config.get('dist_max', -1))}"
        )
        
        l_hon = t(self.langue, "wizard_lbl_honor", langue=self.langue, defaut="Honor")
        l_glo = t(self.langue, "wizard_lbl_glory", langue=self.langue, defaut="Glory")
        l_loo = t(self.langue, "wizard_lbl_loot", langue=self.langue, defaut="Loot")
        f2_val = (
            f"**{l_hon}** : {fmt_val(self.config.get('honor_min', -1))} - {fmt_val(self.config.get('honor_max', -1))}\n"
            f"**{l_glo}** : {fmt_val(self.config.get('glory_min', -1))} - {fmt_val(self.config.get('glory_max', -1))}\n"
            f"**{l_loo}** : {fmt_val(self.config.get('loot_min', -1))} - {fmt_val(self.config.get('loot_max', -1))}"
        )
        
        l_col = t(self.langue, "wizard_lbl_dove", langue=self.langue, defaut="Include Doves")
        l_for = t(self.langue, "wizard_lbl_for", langue=self.langue, defaut="Force Alliance")
        l_act = t(self.langue, "wizard_lbl_act", langue=self.langue, defaut="Activity")
        l_excl = t(self.langue, "wizard_lbl_excl", langue=self.langue, defaut="Exclusions")
        f3_val = f"**{l_col}** : {txt_doves}\n**{l_for}** : {txt_alli}\n**{l_act}** : {txt_act}\n**{l_excl}** : {txt_excl}"

        t1 = t(self.langue, "wizard_t1", langue=self.langue, defaut="Core Criteria")
        t2 = t(self.langue, "wizard_t2", langue=self.langue, defaut="Reward Criteria")
        t3 = t(self.langue, "wizard_t3", langue=self.langue, defaut="Advanced Filters")

        embed.add_field(name=t1, value=f1_val, inline=False)
        embed.add_field(name=t2, value=f2_val, inline=False)
        embed.add_field(name=t3, value=f3_val, inline=False)
        return embed

# ========================================================
# 🎛️ UI COMPONENT : PAGINATION + LIVE RERUN
# ========================================================
class CiblePaginationView(discord.ui.View):
    def __init__(self, cog, attacker, sort_by, target_alliance, embeds, langue="en", owner_id=None):
        super().__init__(timeout=1800)
        self.cog = cog
        self.attacker = attacker
        self.sort_by = sort_by
        self.target_alliance = target_alliance
        self.embeds = embeds
        self.current_page = 0
        self.langue = langue
        self.owner_id = owner_id

        self.btn_prev.label = t(langue, "target_btn_prev", langue=self.langue, defaut="Previous Page")
        self.btn_next.label = t(langue, "target_btn_next", langue=self.langue, defaut="Next Page")
        self.btn_rerun.label = t(langue, "target_btn_rerun", langue=self.langue, defaut="Rerun scan")
        self.update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            return False
        return True

    def update_buttons(self):
        self.btn_prev.disabled = self.current_page == 0
        self.btn_next.disabled = self.current_page == len(self.embeds) - 1

    @discord.ui.button(emoji="<:lastpage:1533554126984581283>", style=discord.ButtonStyle.secondary, custom_id="cible_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(emoji="<:nextpage:1533554128230420590>", style=discord.ButtonStyle.secondary, custom_id="cible_next")
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(emoji="<:refresh:1533433306610274425>", style=discord.ButtonStyle.primary, custom_id="cible_rerun")
    async def btn_rerun(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
            
        msg = t(self.langue, "target_lbl_calc_rerun", langue=self.langue, defaut="Launching a new radar scan...")
        await interaction.response.edit_message(content=msg, embed=None, view=self)
        
        await self.cog._execute_cible(
            interaction, 
            self.attacker, 
            self.sort_by, 
            self.target_alliance,
            message_to_edit=interaction.message
        )

# ==========================================
# ⚔️ COG : TARGET
# ==========================================
class TargetCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.clr_cible = discord.Color.from_rgb(183,0,0)

    def get_target_rules(self, user_id):
        path_users = CONFIG_DIR / 'target.json'
        if path_users.exists():
            try:
                with open(path_users, encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get(str(user_id), {}).get("custom_rules", None)
            except: pass
        return None

    target_group = app_commands.Group(
        name="target", 
        description="Search engine to find specific targets on the map"
    )

    @target_group.command(name="setup", description="Set up your search engine filters")
    async def target_setup(self, interaction: discord.Interaction):
        langue, _ = await get_server_config(interaction)
        user_id = str(interaction.user.id)
        current_rules = self.get_target_rules(user_id)
        
        wizard = TargetWizard(user_id, langue, current_rules)
        await wizard.start(interaction)

    @target_group.command(name="search", description="Launch the radar based on your active filters")
    @app_commands.autocomplete(origin_player=joueur_autocomplete)
    @app_commands.autocomplete(target_alliance=alliance_autocomplete)
    async def target_search(self, interaction: discord.Interaction, origin_player: str, target_alliance: str = None):
        langue, _ = await get_server_config(interaction)
        user_id = str(interaction.user.id)
        
        user_rules = self.get_target_rules(user_id)
        
        if not user_rules:
            wizard = TargetWizard(user_id, langue)
            err_msg = t(langue, "target_err_no_setup", langue=langue, defaut="Stop! You must set up your search engine filters first:")
            return await wizard.start(interaction, content=err_msg)

        try: await interaction.response.defer(thinking=True)
        except: return
        await self._execute_cible(interaction, origin_player, "aleatoire", target_alliance)

    async def _execute_cible(self, interaction: discord.Interaction, origin_player: str, sort_by: str = "aleatoire", target_alliance: str = None, message_to_edit=None):
        langue, serveur = await get_server_config(interaction)

        user_id = str(interaction.user.id)
        user_rules = self.get_target_rules(user_id) or {}
        
        config = {
            "lvl_min": user_rules.get("lvl_min", -1),
            "lvl_max": user_rules.get("lvl_max", -1),
            "pp_min": user_rules.get("pp_min", -1),
            "pp_max": user_rules.get("pp_max", -1),
            "dist_min": user_rules.get("dist_min", -1),
            "dist_max": user_rules.get("dist_max", -1),
            "honor_min": user_rules.get("honor_min", -1),
            "honor_max": user_rules.get("honor_max", -1),
            "glory_min": user_rules.get("glory_min", -1),
            "glory_max": user_rules.get("glory_max", -1),
            "loot_min": user_rules.get("loot_min", -1),
            "loot_max": user_rules.get("loot_max", -1),
            "excluded_alliances": user_rules.get("excluded_alliances") or "",
            "activity_filter": user_rules.get("activity_filter", "all")
        }
        show_doves = user_rules.get("show_doves", False)
        only_with_alliance = user_rules.get("only_with_alliance", False)

        excluded_raw = config["excluded_alliances"] if isinstance(config["excluded_alliances"], str) else ""
        excluded_list = [a.strip().lower() for a in excluded_raw.split(",") if a.strip()]

        headers = await get_api_headers(custom_server=serveur)
        session_active = self.bot.session

        # ----------------------------------------------------
        # SETUP: GET ORIGIN PLAYER
        # ----------------------------------------------------
        loading_title = t(langue, "target_load_title", langue=langue, defaut="GGE-Tracker Radar Scan...")
        loading_desc = t(langue, "target_load_desc", langue=langue, defaut="Querying database directly and sorting...")
        
        loading_embed = discord.Embed(title=loading_title, color=discord.Color.orange())
        loading_embed.description = loading_desc
        if message_to_edit: await message_to_edit.edit(content=None, embed=loading_embed, view=None)
        else: await interaction.edit_original_response(content=None, embed=loading_embed, view=None)

        a_name_real = origin_player
        a_coords = {"x": None, "y": None}
        
        try:
            search_url = f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(origin_player)}"
            async with session_active.get(search_url, headers=headers, timeout=5) as r:
                if r.status == 200:
                    p_data = await r.json()
                    if isinstance(p_data, list) and p_data: p_data = p_data[0]
                    if isinstance(p_data, dict):
                        a_name_real = str(p_data.get("playerName") or p_data.get("name") or origin_player)
            
            coords_url = f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(a_name_real)}"
            async with session_active.get(coords_url, headers=headers, timeout=5) as r:
                if r.status == 200:
                    c_data = await r.json()
                    if isinstance(c_data, dict): c_data = [c_data]
                    if isinstance(c_data, list):
                        for c in c_data:
                            if str(c.get('kingdomId', c.get('kingdom_id', 'X'))) == "0" and str(c.get('type', c.get('castle_type', 'X'))) == "1":
                                a_coords['x'] = c.get('positionX') or c.get('position_x') or c.get('x')
                                a_coords['y'] = c.get('positionY') or c.get('position_y') or c.get('y')
                                break
        except: pass

        if a_coords['x'] is None or a_coords['y'] is None:
            err_msg = t(langue, "target_err_atk_not_found", langue=langue, atk=origin_player, defaut="Cannot locate player on the map to calculate distances.")
            if message_to_edit: await message_to_edit.edit(content=err_msg, embed=None, view=None)
            else: await interaction.edit_original_response(content=err_msg, embed=None, view=None)
            return

        # ----------------------------------------------------
        # STEP 1: API REQUEST PARAMS
        # ----------------------------------------------------
        params = {
            "page": 1,
            "orderBy": "distance",
            "orderType": "ASC",
            "playerNameForDistance": a_name_real,
            "minMight": config.get("pp_min", -1),
            "maxMight": config.get("pp_max", -1),
            "minHonor": config.get("honor_min", -1),
            "maxHonor": config.get("honor_max", -1),
            "minLoot": config.get("loot_min", -1),
            "maxLoot": config.get("loot_max", -1),
            "minLevel": -1,
            "minLegendaryLevel": -1,
            "maxLevel": -1,
            "maxLegendaryLevel": -1,
            "allianceFilter": 1 if only_with_alliance else -1,
            "protectionFilter": -1 if show_doves else 0,
            "banFilter": -1,
            "inactiveFilter": -1
        }

        act_filter = config.get("activity_filter", "all")
        if act_filter == "active": params["inactiveFilter"] = 0
        elif act_filter == "inactive": params["inactiveFilter"] = 1

        l_min = config.get("lvl_min", -1)
        if l_min != -1:
            if l_min <= 70:
                params["minLevel"] = l_min
            else:
                params["minLevel"] = 70
                params["minLegendaryLevel"] = l_min - 70

        l_max = config.get("lvl_max", -1)
        if l_max != -1:
            if l_max <= 70:
                params["maxLevel"] = l_max
            else:
                params["maxLevel"] = 70
                params["maxLegendaryLevel"] = l_max - 70

        if target_alliance: params["alliance"] = target_alliance

        mots_interdits_mur, alliances_mur_alerte = ["repos", "deuil", "hospitalisé", "rest", "hospital", "mourning", "ruhe", "krankenhaus"], []
        try:
            fichier_murs = BASE_DATA_PATH / 'murs_scans' / serveur / 'murs_alliances.json'
            if fichier_murs.exists():
                with open(fichier_murs, encoding='utf-8') as f:
                    for aname_json, desc in json.load(f).items():
                        if any(mot in str(desc).lower() for mot in mots_interdits_mur):
                            alliances_mur_alerte.append("".join(c for c in str(aname_json).lower() if c.isalnum()))
        except: pass

        # ----------------------------------------------------
        # STEP 2: PROBE & FETCH
        # ----------------------------------------------------
        total_pages = 10 
        
        params["page"] = 1
        query_string = urllib.parse.urlencode(params)
        probe_url = f"https://api.gge-tracker.com/api/v1/players?{query_string}"
        try:
            async with session_active.get(probe_url, headers=headers, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    if isinstance(data, dict):
                        total_pages = data.get("totalPages", data.get("total_pages", data.get("last_page", 10)))
                        if "total" in data and isinstance(data["total"], int):
                            total_pages = max(1, math.ceil(data["total"] / 50))
        except: pass
        
        total_pages = max(1, min(total_pages, 200))
        
        nb_pages_to_visit = min(12, total_pages)
        pages_to_visit = random.sample(range(1, total_pages + 1), nb_pages_to_visit)
        
        final_targets = []
        
        for page in pages_to_visit:
            if len(final_targets) >= 40: break 
            
            params["page"] = page
            query_string = urllib.parse.urlencode(params)
            api_url = f"https://api.gge-tracker.com/api/v1/players?{query_string}"
            
            try:
                async with session_active.get(api_url, headers=headers, timeout=10) as r:
                    if r.status != 200: continue
                    data = await r.json()
                    
                    players_list = data.get("players", data.get("items", data.get("data", data))) if isinstance(data, dict) else data
                    if not players_list or not isinstance(players_list, list): continue
                    
                    async def process_player(p):
                        t_name = p.get("player_name") or p.get("name") or ""
                        if not t_name or t_name.lower() == a_name_real.lower(): return None
                        
                        t_alli = p.get("alliance_name") or ""
                        
                        t_lvl = int(p.get("level", 0))
                        t_leg = int(p.get("legendary_level", 0))
                        t_abs_lvl = t_lvl + t_leg
                        
                        if config['lvl_min'] != -1 and t_abs_lvl < config['lvl_min']: return None
                        if config['lvl_max'] != -1 and t_abs_lvl > config['lvl_max']: return None
                        
                        t_pp = int(p.get("might_current", 0))
                        if config['pp_min'] != -1 and t_pp < config['pp_min']: return None
                        if config['pp_max'] != -1 and t_pp > config['pp_max']: return None
                        
                        t_honor = int(p.get("honor", 0))
                        if config['honor_min'] != -1 and t_honor < config['honor_min']: return None
                        if config['honor_max'] != -1 and t_honor > config['honor_max']: return None
                        
                        t_loot = int(p.get("loot_current", 0))
                        if config['loot_min'] != -1 and t_loot < config['loot_min']: return None
                        if config['loot_max'] != -1 and t_loot > config['loot_max']: return None
                        
                        t_glory = int(p.get("current_fame", p.get("highest_fame", 0)))
                        if config['glory_min'] != -1 and t_glory < config['glory_min']: return None
                        if config['glory_max'] != -1 and t_glory > config['glory_max']: return None
                        
                        tx, ty, dist = "???", "???", 9999
                        try:
                            async with session_active.get(f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(t_name)}", headers=headers, timeout=5) as res:
                                if res.status == 200:
                                    c_data = await res.json()
                                    if isinstance(c_data, dict): c_data = [c_data]
                                    for c in c_data:
                                        if str(c.get('kingdomId', c.get('kingdom_id', 'X'))) == "0" and str(c.get('type', c.get('castle_type', 'X'))) == "1":
                                            tx = c.get('positionX') or c.get('position_x') or c.get('x')
                                            ty = c.get('positionY') or c.get('position_y') or c.get('y')
                                            if tx is not None and ty is not None:
                                                dist = math.sqrt((float(tx) - float(a_coords['x']))**2 + (float(ty) - float(a_coords['y']))**2)
                                            break
                        except: pass
                        
                        if tx == "???" or (config['dist_min'] != -1 and dist < config['dist_min']) or (config['dist_max'] != -1 and dist > config['dist_max']): 
                            return None
                        
                        alli_clean = "".join(c for c in t_alli.lower() if c.isalnum())
                        has_wall_warning = (alli_clean in alliances_mur_alerte or any(mot in str(t_alli).lower() for mot in mots_interdits_mur))
                        
                        return {
                            "name": t_name, "alliance": str(t_alli),
                            "lvl": t_lvl, "leg": t_leg,
                            "abs_lvl": t_abs_lvl,
                            "pp": t_pp,
                            "honor": t_honor,
                            "glory": t_glory,
                            "loot": t_loot,
                            "dist": dist, "x": tx, "y": ty,
                            "wall_warning": has_wall_warning,
                            "peace_disabled_at": p.get("peace_disabled_at", "null")
                        }

                    tasks = [process_player(p) for p in players_list]
                    results = await asyncio.gather(*tasks)
                    
                    for res in results:
                        if res:
                            final_targets.append(res)
                            
            except Exception as e:
                logger.error(f"Error API _execute_cible : {e}")
                continue

        # ----------------------------------------------------
        # FINAL DISPLAY
        # ----------------------------------------------------
        if not final_targets:
            empty_msg = t(langue, "target_err_no_target_valid", langue=langue, defaut="No player matching your filters was found.")
            if message_to_edit: await message_to_edit.edit(content=empty_msg, embed=None, view=None)
            else: await interaction.edit_original_response(content=empty_msg, embed=None, view=None)
            return

        random.shuffle(final_targets)
        best_targets = final_targets[:10]
        best_targets.sort(key=lambda x: x['dist'])
        
        actualisation_dt = discord.utils.utcnow()

        embeds = []
        chunk_size = 5 
        nb_pages = max(1, (len(best_targets) - 1) // chunk_size + 1)
        
        target_a_str = t(langue, "target_cible_alli_target", langue=langue, a=target_alliance, defaut=f" (Targeted Alliance: {target_alliance})")
        titre_alliance = target_a_str if target_alliance else ""
        
        lbl_alli = t(langue, "target_cible_field_alli", langue=langue, defaut="Alliance:")
        lbl_lvl = t(langue, "target_cible_field_lvl", langue=langue, defaut="Level:")
        lbl_puiss = t(langue, "target_cible_field_pp", langue=langue, defaut="Might:")
        lbl_honneur = t(langue, "target_cible_field_honor", langue=langue, defaut="Honor:")
        lbl_gloire = t(langue, "target_cible_field_glory", langue=langue, defaut="Glory:")
        lbl_loot = t(langue, "target_cible_field_loot", langue=langue, defaut="Loot:")
        lbl_dist = t(langue, "target_cible_field_dist", langue=langue, defaut="Distance:")
        lbl_coords = t(langue, "target_cible_field_coords", langue=langue, defaut="Coordinates:")
        
        for i in range(0, len(best_targets), chunk_size):
            chunk = best_targets[i:i+chunk_size]
            page_num = (i // chunk_size) + 1
            
            titre_emb = t(langue, "target_res_title", langue=langue, a=a_name_real, t=titre_alliance, defaut=f"Scan Results from {a_name_real}{titre_alliance}")
            desc_emb = t(langue, "target_res_desc", langue=langue, shown=len(best_targets), total=len(final_targets), defaut=f"**{len(best_targets)} targets displayed** (randomly selected from {len(final_targets)} valid targets found).\n━━━━━━━━━━━━━━━━━━━━━━━")
            
            embed = discord.Embed(title=titre_emb, color=self.clr_cible)
            
            lbl_date = t(langue, "target_lbl_date_data", langue=langue, defaut="⏱️ **Data dated from:**")
            embed.description = f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{desc_emb}"

            for j, t_cnd in enumerate(chunk):
                index_global = i + j + 1
                
                dist_str = t(langue, "target_dist_lieues", langue=langue, d=int(t_cnd['dist']), defaut=f"{int(t_cnd['dist'])} leagues")
                
                is_under_colombe = False
                if t_cnd.get('peace_disabled_at') and t_cnd['peace_disabled_at'] != "null":
                    try:
                        dt_peace = datetime.fromisoformat(t_cnd['peace_disabled_at'].replace('Z', '+00:00'))
                        if dt_peace > discord.utils.utcnow():
                            is_under_colombe = True
                    except: pass

                target_icon = t(langue, "e_peace", langue=langue, defaut="🕊️") if is_under_colombe else t(langue, "e_players", langue=langue, defaut="👤")

                warnings = []
                if t_cnd['wall_warning']: warnings.append(t(langue, "target_warn_wall", langue=langue, defaut="\n**CHECK WALL:** Sensitive alliance description!"))
                if is_under_colombe: warnings.append(t(langue, "target_warn_peace", langue=langue, defaut="\n**PLAYER UNDER DOVE: Protection active!**"))
                
                warning_txt = "".join(warnings) if warnings else ""
                
                description_cible = (
                    f"{t(langue, 'e_alliance_icon', langue=langue, defaut='🛡️')} {lbl_alli} **{t_cnd['alliance']}**\n"
                    f"{t(langue, 'e_lvl', langue=langue, defaut='⭐')} {lbl_lvl} {t_cnd['lvl']}/{t_cnd['leg']}\n"
                    f"{t(langue, 'e_pp1', langue=langue, defaut='⚔️')} {lbl_puiss} **{format_num(t_cnd['pp'])}**\n"
                    f"{t(langue, 'e_honor', langue=langue, defaut='🎖️')} {lbl_honneur} **{format_num(t_cnd['honor'])}**\n"
                    f"{t(langue, 'e_glory', langue=langue, defaut='🌟')} {lbl_gloire} **{format_num(t_cnd['glory'])}**\n"
                    f"{t(langue, 'e_loot', langue=langue, defaut='📦')} {lbl_loot} **{format_num(t_cnd['loot'])}**\n"
                    f"{t(langue, 'e_compass', langue=langue, defaut='📍')} {lbl_dist} **{dist_str}**\n"
                    f"{t(langue, 'e_map', langue=langue, defaut='🗺️')} {lbl_coords} `{t_cnd['x']}:{t_cnd['y']}`\n"
                    f"{warning_txt}\n━━━━━━━━━━━━━━━━━━━━━━━"
                )
                
                name_cible = t(langue, "target_cible_field_title", langue=langue, idx=index_global, n=t_cnd['name'], icon=target_icon, defaut=f"{target_icon} Target #{index_global} : {t_cnd['name']}")
                embed.add_field(name=name_cible, value=description_cible, inline=False)

            titre_page = t(langue, "target_cible_footer_page", langue=langue, cur=page_num, tot=nb_pages, defaut=f"Page {page_num}/{nb_pages}")
            val_spy = t(langue, "target_cible_footer_spy", langue=langue, defaut="**SPY REQUIRED** before attack.")
            embed.add_field(name=titre_page, value=val_spy, inline=False)
            
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        view = CiblePaginationView(self, origin_player, sort_by, target_alliance, embeds, langue, owner_id=interaction.user.id)
        
        if message_to_edit:
            await message_to_edit.edit(content=None, embed=embeds[0], view=view)
        else:
            await interaction.edit_original_response(content=None, embed=embeds[0], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=15, langue=langue)

async def setup(bot: commands.Bot):
    await bot.add_cog(TargetCog(bot))