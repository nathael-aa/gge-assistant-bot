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
    PaginationView,
    _get_api_timestamp,
    alliance_autocomplete,
    format_num,
    get_api_headers,
    get_cached_data,
    get_server_config,
    joueur_autocomplete,
    prompt_vote_if_lucky,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")

# ========================================================
# 🎛️ COMPOSANTS UI : DASHBOARD ET MODAL PERSO
# ========================================================
class TargetSetupModal(discord.ui.Modal):
    # Les labels sont définis dynamiquement dans __init__
    min_dist = discord.ui.TextInput(custom_id="min_dist", label="...", required=True)
    tier_diff = discord.ui.TextInput(custom_id="tier_diff", label="...", required=True)
    pp_min = discord.ui.TextInput(custom_id="pp_min", label="...", required=True)
    pp_max = discord.ui.TextInput(custom_id="pp_max", label="...", required=True)

    def __init__(self, dashboard_view, langue="en"):
        # Titre du modal traduit
        super().__init__(title=t(langue, "target_modal_title", defaut="⚙️ Personal Radar Rules"))
        self.dashboard_view = dashboard_view
        self.langue = langue
        config = self.dashboard_view.config
        
        # Traduction et pré-remplissage des champs
        self.min_dist.label = t(langue, "target_modal_dist_lbl", defaut="Minimum distance (leagues)")
        self.min_dist.placeholder = "Ex: 10"
        self.min_dist.default = str(config.get("min_dist", 0))
        
        self.tier_diff.label = t(langue, "target_modal_tier_lbl", defaut="Max Tier difference")
        self.tier_diff.placeholder = t(langue, "target_modal_tier_ph", defaut="0 = Same tier, 1 = +1 tier")
        self.tier_diff.default = str(config.get("tier_diff", 0))
        
        self.pp_min.label = t(langue, "target_modal_ppmin_lbl", defaut="Max PP Difference (Downward)")
        self.pp_min.placeholder = "Ex: -3000000"
        self.pp_min.default = str(config.get("pp_min", -3000000))
        
        self.pp_max.label = t(langue, "target_modal_ppmax_lbl", defaut="Max PP Difference (Upward)")
        self.pp_max.placeholder = "Ex: 10000000"
        self.pp_max.default = str(config.get("pp_max", 10000000))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            self.dashboard_view.config["min_dist"] = int(self.min_dist.value)
            self.dashboard_view.config["tier_diff"] = int(self.tier_diff.value)
            self.dashboard_view.config["pp_min"] = int(self.pp_min.value)
            self.dashboard_view.config["pp_max"] = int(self.pp_max.value)
        except ValueError:
            err = t(self.langue, "target_modal_err_val", defaut="❌ **Error:** You must enter numbers only!")
            return await interaction.response.send_message(err, ephemeral=True)

        self.dashboard_view.save_config()
        self.dashboard_view.update_buttons()
        await interaction.response.edit_message(embed=self.dashboard_view.generate_embed(), view=self.dashboard_view)

class TargetDashboardView(discord.ui.View):
    def __init__(self, user_id, langue="en"):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.langue = langue
        
        cache_users = utils.USERS_CONFIG_CACHE or {}

        self.config = cache_users.get(user_id, {}).get("custom_rules", {
            "min_dist": 0, "tier_diff": 0, "pp_min": -3000000, "pp_max": 10000000,
            "show_doves": False, "ignore_tiers": False, "only_with_alliance": True
        })
        
        # Traduction des boutons
        self.btn_doves.label = t(langue, "target_dash_btn_doves", defaut="Doves")
        self.btn_tiers.label = t(langue, "target_dash_btn_tiers", defaut="Ignore Tiers")
        self.btn_alli.label = t(langue, "target_dash_btn_alli", defaut="Alliance Filter")
        self.btn_numbers.label = t(langue, "target_dash_btn_numbers", defaut="Edit Numbers")
        
        self.update_buttons()

    def update_buttons(self):
        self.btn_doves.style = discord.ButtonStyle.success if self.config.get("show_doves") else discord.ButtonStyle.secondary
        self.btn_tiers.style = discord.ButtonStyle.success if self.config.get("ignore_tiers") else discord.ButtonStyle.secondary
        self.btn_alli.style = discord.ButtonStyle.success if self.config.get("only_with_alliance") else discord.ButtonStyle.secondary

    def generate_embed(self):
        title = t(self.langue, "target_dash_title", defaut="⚙️ Personal Radar Configuration")
        embed = discord.Embed(title=title, color=discord.Color.blue())
        
        txt_doves = t(self.langue, "target_dash_on", defaut="✅ Included") if self.config.get("show_doves") else t(self.langue, "target_dash_off", defaut="❌ Hidden")
        txt_tiers = t(self.langue, "target_dash_tiers_on", defaut="✅ Yes (No-limit)") if self.config.get("ignore_tiers") else t(self.langue, "target_dash_tiers_off", defaut="❌ No (Strict matching)")
        txt_alli = t(self.langue, "target_dash_alli_on", defaut="✅ Yes") if self.config.get("only_with_alliance") else t(self.langue, "target_dash_alli_off", defaut="❌ No (Includes no-alliance)")

        f1_name = t(self.langue, "target_dash_f1_name", defaut="🔢 Numeric Variables")
        f1_val = t(self.langue, "target_dash_f1_val", d=self.config.get('min_dist'), t=self.config.get('tier_diff'), p1=self.config.get('pp_min'), p2=self.config.get('pp_max'), defaut=f"**Min distance** : {self.config.get('min_dist')} leagues\n**Tier diff** : +{self.config.get('tier_diff')}\n**PP diff** : {self.config.get('pp_min')} to +{self.config.get('pp_max')}")
        
        f2_name = t(self.langue, "target_dash_f2_name", defaut="🎛️ Advanced Filters")
        f2_val = t(self.langue, "target_dash_f2_val", doves=txt_doves, tiers=txt_tiers, alli=txt_alli, defaut=f"**Doves** : {txt_doves}\n**Ignore Tiers** : {txt_tiers}\n**Alliance Only** : {txt_alli}")

        embed.add_field(name=f1_name, value=f1_val, inline=False)
        embed.add_field(name=f2_name, value=f2_val, inline=False)
        return embed

    @discord.ui.button(emoji="🕊️", custom_id="dash_doves")
    async def btn_doves(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.config["show_doves"] = not self.config.get("show_doves")
        self.save_config()
        self.update_buttons()
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    @discord.ui.button(emoji="⚖️", custom_id="dash_tiers")
    async def btn_tiers(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.config["ignore_tiers"] = not self.config.get("ignore_tiers")
        self.save_config()
        self.update_buttons()
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    @discord.ui.button(emoji="🛡️", custom_id="dash_alli")
    async def btn_alli(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.config["only_with_alliance"] = not self.config.get("only_with_alliance")
        self.save_config()
        self.update_buttons()
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    @discord.ui.button(emoji="🔢", style=discord.ButtonStyle.primary, row=1)
    async def btn_numbers(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TargetSetupModal(self, self.langue))

    def save_config(self):
        '''
        Écrit dans users.json (utilisé dans get_server_config) au
        lieu du target.json (non utilisé) : donc les règles perso étaient
        perdues à chaque redémarrage du bot ou /setup d'un autre utilisateur
        '''
        path_users = CONFIG_DIR / 'users.json'
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
            logger.error(f"Erreur sauvegarde config perso : {e}")

# ========================================================
# 🎛️ COMPOSANT UI : PAGINATION DES CIBLES + RELANCE EN DIRECT
# ========================================================
class CiblePaginationView(discord.ui.View):
    def __init__(self, cog, attacker, sort_by, target_alliance, embeds, langue="fr", owner_id=None):
        super().__init__(timeout=1800)
        self.cog = cog
        self.attacker = attacker
        self.sort_by = sort_by
        self.target_alliance = target_alliance
        self.embeds = embeds
        self.current_page = 0
        self.langue = langue
        self.owner_id = owner_id

        self.btn_prev.label = t(langue, "guerre_btn_prev", defaut="Page Précédente")
        self.btn_next.label = t(langue, "guerre_btn_next", defaut="Page Suivante")
        self.btn_rerun.label = t(langue, "guerre_btn_rerun", defaut="Relancer une vague")
        self.update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        '''
        Ce message est public : sans ce contrôle, n'importe quel membre du salon
        peut cliquer 'Relancer une vague' et donc écraser le résultat de son auteur
        avec un scan lancé sous sa propre configuration
        '''
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            # Si besoin d'afficher un message, commentaires à retirer :
            # msg = t(self.langue, "guerre_err_not_owner", defaut="<:error:1512505075220611172> Seul l'auteur de la recherche peut utiliser ces boutons. Lance ta propre commande `/target search`.")
            # await interaction.response.send_message(msg, ephemeral=True)
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
            
        msg = t(self.langue, "guerre_lbl_calc_rerun", defaut="<:icon_search:1512505406474293438> *Calcul d'une nouvelle vague de cibles aléatoires en cours...*")
        await interaction.response.edit_message(content=msg, embed=None, view=self)
        
        await self.cog._execute_cible(
            interaction, 
            self.attacker, 
            self.sort_by, 
            self.target_alliance, 
            message_to_edit=interaction.message
        )

# ==========================================
# ⚔️ LE COG GUERRE
# ==========================================
class GuerreCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.clr_proximite = discord.Color.from_rgb(204,0,0)
        self.clr_cible     = discord.Color.from_rgb(183,0,0)
        self.clr_hr        = discord.Color.from_rgb(146,0,0)

    # ==========================================
    # 📍 COMMANDE : PROXIMITY
    # ==========================================
    @app_commands.command(name="proximity", description="Find the enemy castles closest to you")
    @app_commands.autocomplete(my_player=joueur_autocomplete)
    @app_commands.autocomplete(enemy_alliance=alliance_autocomplete)
    async def proximity(self, interaction: discord.Interaction, my_player: str, enemy_alliance: str):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        langue, serveur = await get_server_config(interaction)

        cache_data = await get_cached_data(serveur)
        local_data = cache_data.get('players_data', {})

        target_id = None
        for p_info in local_data.values():
            a_obj = p_info.get('alliance')
            a_name = a_obj.get('name') if isinstance(a_obj, dict) else (p_info.get('alliance_name') or a_obj)
            
            if a_name and str(a_name).lower() == enemy_alliance.lower():
                aid = p_info.get('allianceId') or p_info.get('alliance_id')
                if not aid and isinstance(a_obj, dict):
                    aid = a_obj.get('allianceId') or a_obj.get('alliance_id')
                
                if aid:
                    target_id = str(aid)
                    enemy_alliance = str(a_name) 
                    break

        if not target_id:
            return await interaction.followup.send(t(langue, "guerre_err_alli_cache2", a=enemy_alliance, defaut=f"<:error:1512505075220611172> Alliance **{enemy_alliance}** introuvable dans le cache."))

        headers = await get_api_headers(custom_server=serveur)
        
        my_x, my_y = None, None
        url_me = f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(my_player)}"
        try:
            async with self.bot.session.get(url_me, headers=headers, timeout=5) as r:
                if r.status == 200:
                    c_data = await r.json()
                    if isinstance(c_data, dict): c_data = [c_data]
                    if isinstance(c_data, list):
                        for c in c_data:
                            if str(c.get('kingdomId', c.get('kingdom_id'))) == "0" and str(c.get('type', c.get('castle_type'))) == "1":
                                my_x = c.get('positionX') or c.get('position_x') or c.get('x')
                                my_y = c.get('positionY') or c.get('position_y') or c.get('y')
                                break
        except: pass
        
        if my_x is None or my_y is None:
            return await interaction.followup.send(t(langue, "guerre_err_no_coords", p=my_player, defaut=f"<:error:1512505075220611172> Impossible de trouver les coordonnées exactes de **{my_player}**."))

        my_x, my_y = int(my_x), int(my_y)

        url_alli = f"https://api.gge-tracker.com/api/v1/alliances/id/{target_id}"
        try:
            async with self.bot.session.get(url_alli, headers=headers, timeout=10) as r:
                if r.status != 200: return await interaction.followup.send(t(langue, "guerre_err_api", defaut="<:error:1512505075220611172> Erreur de l'API GGE-Tracker (Alliance)."))
                data = await r.json()
                if isinstance(data, list) and data: data = data[0]
        except Exception as e:
            return await interaction.followup.send(t(langue, "guerre_err_api_join", e=str(e), defaut=f"<:error:1512505075220611172> Impossible de joindre l'API : {e}"))

        members = data.get("players", data.get("members", data.get("playerList", [])))
        if not members: return await interaction.followup.send(t(langue, "guerre_err_alli_empty2", defaut="<:error:1512505075220611172> L'alliance ennemie semble vide."))

        actualisation_dt = _get_api_timestamp(data)
        txt_unk = t(langue, "prof_unknown", defaut="Inconnu")

        async def get_enemy_coords(m):
            p_name = m.get('player_name', m.get('playerName', m.get('name', txt_unk)))
            p_pp = int(m.get('might_current', m.get('might', m.get('main_points', 0))))
            p_peace = m.get('peace_disabled_at')
            is_protected = False
            if p_peace and p_peace != "null":
                try:
                    if datetime.fromisoformat(p_peace.replace('Z', '+00:00')) > discord.utils.utcnow():
                        is_protected = True
                except: pass
            
            url_s = f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(p_name)}"
            try:
                async with self.bot.session.get(url_s, headers=headers, timeout=10) as res:
                    if res.status == 200:
                        c_data = await res.json()
                        if isinstance(c_data, dict): c_data = [c_data]
                        if isinstance(c_data, list):
                            for c in c_data:
                                if str(c.get('kingdomId', c.get('kingdom_id'))) == "0" and str(c.get('type', c.get('castle_type'))) == "1":
                                    x = c.get('positionX') or c.get('position_x') or c.get('x')
                                    y = c.get('positionY') or c.get('position_y') or c.get('y')
                                    if x is not None and y is not None:
                                        dist = math.hypot(int(x) - my_x, int(y) - my_y)
                                        return {"name": p_name, "x": int(x), "y": int(y), "dist": dist, "pp": p_pp, "protected": is_protected}
            except: pass
            return None

        tasks = [get_enemy_coords(m) for m in members]
        results = await asyncio.gather(*tasks)

        valid_targets = [res for res in results if res is not None]
        valid_targets.sort(key=lambda t: t["dist"])

        if not valid_targets:
            return await interaction.followup.send(t(langue, "guerre_err_no_castle_loc", defaut="<:error:1512505075220611172> Impossible de localiser les châteaux de cette alliance sur la carte Principale."))

        embeds = []
        chunk_size = 5 
        nb_pages = max(1, (len(valid_targets) - 1) // chunk_size + 1)
        
        for i in range(0, len(valid_targets), chunk_size):
            chunk = valid_targets[i:i+chunk_size]
            page_num = (i // chunk_size) + 1
            
            embed = discord.Embed(
                title=t(langue, "guerre_prox_title", a=enemy_alliance, defaut=rf"\<:attaque:1512570903886692474> Cibles de Proximité : {enemy_alliance}"), 
                color=self.clr_proximite
            )
            
            desc_i18n = t(langue, "guerre_prox_desc", p=my_player, x=my_x, y=my_y, n=len(valid_targets), defaut=f"🛰️ Ton point de départ : **{my_player}** (`{my_x}:{my_y}`)\n<:icon_search:1512505406474293438> **{len(valid_targets)}** cibles localisées au total.")
            
            lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
            embed.description = f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{desc_i18n}"
            
            lbl_dist = t(langue, "guerre_prox_field_dist", defaut="Distance :")
            lbl_coords = t(langue, "guerre_prox_field_coords", defaut="Coords :")
            lbl_pp = t(langue, "guerre_prox_field_pp", defaut="Puissance :")
            lbl_col_yes = t(langue, "guerre_prox_colombe", defaut="<:peace:1512503935892586566> **SOUS COLOMBE**")
            lbl_col_no = t(langue, "guerre_prox_vuln", defaut=r"\<:attaque:1512570903886692474> **VULNÉRABLE**")

            for j, tg in enumerate(chunk):
                index_global = i + j + 1
                colombe_txt = lbl_col_yes if tg['protected'] else lbl_col_no
                embed.add_field(
                    name=f"#{index_global} - {tg['name']}", 
                    value=f"<:icon_world:1512517516012814537> {lbl_dist} **{int(tg['dist'])} lieues**\n<:compass:1512504625364729987> {lbl_coords} `{tg['x']}:{tg['y']}`\n<:pp2:1512571027119538335> {lbl_pp} {format_num(tg['pp'])}\n{colombe_txt}", 
                    inline=False
                )

            embed.add_field(name=t(langue, "guerre_prox_footer_page", cur=page_num, tot=nb_pages, defaut=f"Page {page_num}/{nb_pages}"), value=t(langue, "guerre_prox_footer_tri", defaut="*Tri effectué du plus proche au plus éloigné.*"), inline=False)
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        if len(embeds) == 1: await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=15, langue=langue)

    # ==========================================
    # 🎯 GROUPE DE COMMANDES : TARGET
    # ==========================================
    target_group = app_commands.Group(
        name="target", 
        description="Radar tools and rules arbitration based on your personal profile"
    )

    @target_group.command(name="setup", description="Configure your personal radar rules")
    async def target_setup(self, interaction: discord.Interaction):
        langue, _ = await get_server_config(interaction)
        user_id = str(interaction.user.id)
        view = TargetDashboardView(user_id, langue)
        await interaction.response.send_message(embed=view.generate_embed(), view=view, ephemeral=True)

    @target_group.command(name="search", description="Find targets based on your personal rules")
    @app_commands.autocomplete(attacker=joueur_autocomplete)
    @app_commands.autocomplete(target_alliance=alliance_autocomplete)
    async def target_search(self, interaction: discord.Interaction, attacker: str, target_alliance: str = None):
        langue, _ = await get_server_config(interaction)
        import utils
        user_id = str(interaction.user.id)
        has_rules = utils.USERS_CONFIG_CACHE and user_id in utils.USERS_CONFIG_CACHE and "custom_rules" in utils.USERS_CONFIG_CACHE[user_id]
        
        if not has_rules:
            view = TargetDashboardView(user_id, langue)
            err_msg = t(langue, "target_err_no_setup", defaut="⚠️ **Stop!** You must configure your targeting rules first.\nHere is your dashboard to set up your custom radar:")
            return await interaction.response.send_message(err_msg, embed=view.generate_embed(), view=view, ephemeral=True)

        try: await interaction.response.defer(thinking=True)
        except: return
        await self._execute_cible(interaction, attacker, "aleatoire", target_alliance)

    @target_group.command(name="hr", description="Check if an attack complies with your personal rules")
    @app_commands.autocomplete(attacker=joueur_autocomplete)
    @app_commands.autocomplete(defender=joueur_autocomplete)
    async def target_hr(self, interaction: discord.Interaction, attacker: str, defender: str):
        langue, _ = await get_server_config(interaction)
        import utils
        user_id = str(interaction.user.id)
        has_rules = utils.USERS_CONFIG_CACHE and user_id in utils.USERS_CONFIG_CACHE and "custom_rules" in utils.USERS_CONFIG_CACHE[user_id]
        
        if not has_rules:
            view = TargetDashboardView(user_id, langue)
            err_msg = t(langue, "target_err_no_setup", defaut="⚠️ **Stop!** You must configure your targeting rules first.\nHere is your dashboard to set up your custom radar:")
            return await interaction.response.send_message(err_msg, embed=view.generate_embed(), view=view, ephemeral=True)
            
        try: await interaction.response.defer(thinking=True)
        except: return
        await self._execute_hr(interaction, attacker, defender)

    # ==========================================
    # ⚙️ MOTEUR D'EXÉCUTION CENTRALISÉ DU SCAN
    # ==========================================
    async def _execute_cible(self, interaction: discord.Interaction, attacker: str, sort_by: str = "aleatoire", target_alliance: str = None, message_to_edit=None):
        langue, serveur = await get_server_config(interaction)

        def get_tier(lvl, leg):
            if lvl < 70: return 0
            if leg <= 120: return 1
            if leg <= 360: return 2
            if leg <= 650: return 3
            if leg <= 949: return 4
            return 5

        # -----------------------------------------------------
        # CHARGEMENT STRICT DES RÈGLES PERSOS DU JOUEUR
        # -----------------------------------------------------
        user_id = str(interaction.user.id)
        user_rules = utils.USERS_CONFIG_CACHE.get(user_id, {}).get("custom_rules", {})
        
        config = {
            "nom": "Règles Personnelles",
            "check_api_limit": False,
            "allowed_tiers_relative": list(range(user_rules.get("tier_diff", 0) + 1)),
            "pp_offset_min": user_rules.get("pp_min", -3000000),
            "pp_offset_max": user_rules.get("pp_max", 10000000),
            "min_distance": user_rules.get("min_dist", 0),
            "ignore_tiers": user_rules.get("ignore_tiers", False),
            "affichage": {"max_attaques": "Variables", "cooldown": "Selon envies"}
        }
        show_doves = user_rules.get("show_doves", False)
        only_with_alliance = user_rules.get("only_with_alliance", True)

        def is_legal_target(a_pp, a_tier, a_lvl, t_pp, t_tier, t_lvl):
            if not config.get("ignore_tiers", False):
                diff_tier = t_tier - a_tier
                allowed_tiers = config.get("allowed_tiers_relative", [0])
                if diff_tier not in allowed_tiers: return False

                if a_tier == 0 and t_tier == 0:
                    max_lvl_diff = 10
                    if abs(a_lvl - t_lvl) > max_lvl_diff: return False

            if "pp_offset_min" in config and t_pp < (a_pp + config["pp_offset_min"]): return False
            if "pp_offset_max" in config and t_pp > (a_pp + config["pp_offset_max"]): return False

            return True

        headers = await get_api_headers(custom_server=serveur)
        cache_data = await get_cached_data(serveur)
        local_data = cache_data.get('players_data', {})
        session_active = self.bot.session

        a_info, a_name_real = None, attacker
        a_coords = {"x": None, "y": None}
        
        try:
            search_url = f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(attacker)}"
            async with session_active.get(search_url, headers=headers, timeout=10) as r:
                if r.status == 200:
                    p_data = await r.json()
                    if isinstance(p_data, list) and p_data: p_data = p_data[0]
                    if isinstance(p_data, dict):
                        a_info = p_data
                        a_name_real = a_info.get("playerName", a_info.get("name", a_info.get("player_name", attacker)))
            
            coords_url = f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(a_name_real)}"
            async with session_active.get(coords_url, headers=headers, timeout=10) as r:
                if r.status == 200:
                    c_data = await r.json()
                    if isinstance(c_data, dict): c_data = [c_data]
                    if isinstance(c_data, list):
                        for c in c_data:
                            if str(c.get('kingdomId', c.get('kingdom_id', 'X'))) == "0" and str(c.get('type', c.get('castle_type', 'X'))) == "1":
                                ax = c.get('positionX') or c.get('position_x') or c.get('x')
                                ay = c.get('positionY') or c.get('position_y') or c.get('y')
                                if ax is not None and ay is not None:
                                    a_coords['x'], a_coords['y'] = float(ax), float(ay)
                                break
        except: pass

        if not a_info:
            for name, info in local_data.items():
                if name.lower() == attacker.lower():
                    a_info, a_name_real = info, name
                    break

        if not a_info:
            err_msg = t(langue, "guerre_err_atk_not_found", atk=attacker, defaut=f"<:error:1512505075220611172> Attaquant **{attacker}** introuvable.")
            if message_to_edit: await message_to_edit.edit(content=err_msg, view=None)
            else: await interaction.followup.send(err_msg)
            return

        a_lvl = int(a_info.get('level', 0))
        a_leg = int(a_info.get('legendary_level', a_info.get('legendaryLevel', 0)))
        a_pp = int(a_info.get('might_current', a_info.get('main_points', a_info.get('might', 0))))
        a_tier = get_tier(a_lvl, a_leg)
        
        mots_interdits_mur, alliances_mur_alerte = ["repos", "deuil", "hospitalisé"], []
        try:
            fichier_murs = BASE_DATA_PATH / 'murs_scans' / serveur / 'murs_alliances.json'
            if fichier_murs.exists():
                with open(fichier_murs, encoding='utf-8') as f:
                    for aname_json, desc in json.load(f).items():
                        if any(mot in str(desc).lower() for mot in mots_interdits_mur):
                            alliances_mur_alerte.append("".join(c for c in str(aname_json).lower() if c.isalnum()))
        except: pass

        pool_candidats = []
        txt_sans_alliance = t(langue, "guerre_sa", defaut="Sans alliance")

        for t_name, t_info in local_data.items():
            if t_name.lower() == a_name_real.lower(): continue
            
            raw_t_alliance = t_info.get('alliance') or t_info.get('alliance_name') or t_info.get('allianceName') or ''
            if isinstance(raw_t_alliance, dict): 
                t_alliance = raw_t_alliance.get('name') or raw_t_alliance.get('alliance_name') or ''
            else: 
                t_alliance = str(raw_t_alliance)
                
            if only_with_alliance and (not t_alliance or t_alliance == txt_sans_alliance): 
                continue

            if not show_doves:
                p_peace = t_info.get('peace_disabled_at')
                if p_peace and p_peace != "null":
                    try:
                        if datetime.fromisoformat(p_peace.replace('Z', '+00:00')) > discord.utils.utcnow():
                            continue 
                    except: pass
            
            alli_clean = "".join(c for c in t_alliance.lower() if c.isalnum())
            if target_alliance and t_alliance.lower() != target_alliance.lower(): continue

            t_lvl = int(t_info.get('level', 0))
            t_leg = int(t_info.get('legendary_level', 0))
            t_pp = int(t_info.get('main_points', 0))
            t_tier = get_tier(t_lvl, t_leg)

            if not is_legal_target(a_pp, a_tier, a_lvl, t_pp, t_tier, t_lvl): continue

            has_wall_warning = (alli_clean in alliances_mur_alerte or any(mot in str(t_alliance).lower() for mot in mots_interdits_mur))
            
            pool_candidats.append({
                "name": t_name, "alliance": str(t_alliance), "lvl": t_lvl, "leg": t_leg,
                "tier": t_tier, "pp": t_pp, "honor": 0, "dist": 9999, "x": "???", "y": "???",
                "is_upper_tier": (t_tier > a_tier), "wall_warning": has_wall_warning, "is_ghost": False,
                "peace_disabled_at": t_info.get('peace_disabled_at', "null") 
            })

            if not pool_candidats:
                nom_regle = "Règles Personnelles"
                no_targets_msg = t(langue, "guerre_err_no_target_crit", regle=nom_regle, defaut=f"<:error:1512505075220611172> Aucune cible trouvée respectant les critères (**{nom_regle}**) dans la base de données.")
                if message_to_edit: await message_to_edit.edit(content=no_targets_msg, view=None)
                else: await interaction.followup.send(no_targets_msg)
                return

        random.shuffle(pool_candidats)
        final_targets = []
        chunk_size_api = 5
        
        for k in range(0, len(pool_candidats), chunk_size_api):
            if len(final_targets) >= 10: break
            chunk_candidats = pool_candidats[k:k+chunk_size_api]

            async def fetch_live_target(t_cnd):
                try:
                    async with session_active.get(f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(t_cnd['name'])}", headers=headers, timeout=10) as r:
                        if r.status == 200:
                            d = await r.json()
                            if isinstance(d, list) and d: d = d[0]
                            if isinstance(d, dict):
                                t_cnd['lvl'] = int(d.get('level', t_cnd['lvl']))
                                t_cnd['leg'] = int(d.get('legendary_level', d.get('legendaryLevel', t_cnd['leg'])))
                                t_cnd['pp'] = int(d.get('might_current', d.get('might', t_cnd['pp'])))
                                t_cnd['honor'] = int(d.get('honor', 0)) 
                                t_cnd['tier'] = get_tier(t_cnd['lvl'], t_cnd['leg'])
                                t_cnd['is_upper_tier'] = (t_cnd['tier'] > a_tier)
                                t_cnd['peace_disabled_at'] = d.get('peace_disabled_at', "null")
                                if "updated_at" in d:
                                    t_cnd["updated_at"] = d["updated_at"]
                            else: t_cnd['is_ghost'] = True
                        elif r.status == 429: await asyncio.sleep(1.5)
                except: pass
                
                if not t_cnd['is_ghost']:
                    try:
                        async with session_active.get(f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(t_cnd['name'])}", headers=headers, timeout=10) as r:
                            if r.status == 200:
                                c_data = await r.json()
                                if isinstance(c_data, dict): c_data = [c_data]
                                if isinstance(c_data, list):
                                    for c in c_data:
                                        if str(c.get('kingdomId', c.get('kingdom_id', 'X'))) == "0" and str(c.get('type', c.get('castle_type', 'X'))) == "1":
                                            tx = c.get('positionX') or c.get('position_x') or c.get('x')
                                            ty = c.get('positionY') or c.get('position_y') or c.get('y')
                                            if tx is not None and ty is not None: 
                                                t_cnd['x'], t_cnd['y'] = str(tx), str(ty)
                                                if a_coords['x'] is not None and a_coords['y'] is not None:
                                                    t_cnd['dist'] = math.sqrt((float(tx) - a_coords['x'])**2 + (float(ty) - a_coords['y'])**2)
                                            break
                            elif r.status == 429: await asyncio.sleep(1.5)
                    except: pass

            await asyncio.gather(*(fetch_live_target(t_cnd) for t_cnd in chunk_candidats))
            
            for t_cnd in chunk_candidats:
                if t_cnd['is_ghost'] or t_cnd['x'] == "???": continue
                
                if is_legal_target(a_pp, a_tier, a_lvl, t_cnd['pp'], t_cnd['tier'], t_cnd['lvl']):
                    min_dist = config.get("min_distance", 0)
                    if t_cnd['dist'] < min_dist: 
                        continue
                    final_targets.append(t_cnd)
            
            await asyncio.sleep(0.15)

        if not final_targets:
            nom_regle = "Règles Personnelles"
            empty_msg = t(langue, "guerre_err_no_target_valid", regle=nom_regle, defaut=f"<:error:1512505075220611172> Les cibles potentielles ne respectent plus les règles (**{nom_regle}**) avec leurs puissances actuelles ou sont hors-ligne.")
            if message_to_edit: await message_to_edit.edit(content=empty_msg, view=None)
            else: await interaction.followup.send(empty_msg)
            return

        actualisation_dt = _get_api_timestamp(a_info, final_targets)

        best_targets = final_targets[:10]
        embeds = []
        chunk_size = 5 
        nb_pages = max(1, (len(best_targets) - 1) // chunk_size + 1)
        
        lbl_alli_target = t(langue, "guerre_cible_alli_target", a=target_alliance, defaut=f" (Alliance : {target_alliance})")
        titre_alliance = lbl_alli_target if target_alliance else ""
        nom_regle_titre = "Règles Personnelles"
        
        lbl_alli = t(langue, "guerre_cible_field_alli", defaut="Alliance :")
        lbl_lvl = t(langue, "guerre_cible_field_lvl", defaut="Niveau :")
        lbl_palier = t(langue, "guerre_cible_field_palier", defaut="Palier")
        lbl_puiss = t(langue, "guerre_cible_field_pp", defaut="Puissance :")
        lbl_honneur = t(langue, "guerre_cible_field_honor", defaut="Honneur :")
        lbl_dist = t(langue, "guerre_cible_field_dist", defaut="Distance :")
        lbl_coords = t(langue, "guerre_cible_field_coords", defaut="Coordonnées :")
        
        for i in range(0, len(best_targets), chunk_size):
            chunk = best_targets[i:i+chunk_size]
            page_num = (i // chunk_size) + 1
            
            titre_emb = t(langue, "guerre_cible_title", r=nom_regle_titre, a=a_name_real, ta=titre_alliance, defaut=f"<:attaque:1512570903886692474> Cibles {nom_regle_titre} pour {a_name_real}{titre_alliance}")
            desc_emb = t(langue, "guerre_cible_desc", pp=format_num(a_pp), t=a_tier, c=len(final_targets), defaut=f"<:pp2:1512571027119538335> Ta Puissance : **{format_num(a_pp)}** | <:lvl:1512571152524906596> Ton Palier : **{a_tier}**\n<:icon_search:1512505406474293438> **{len(final_targets)} cibles valides détectées**.\n\n━━━━━━━━━━━━━━━━━━━━━━━")
            
            embed = discord.Embed(title=titre_emb, color=self.clr_cible)
            
            lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
            embed.description = f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{desc_emb}"

            for j, t_cnd in enumerate(chunk):
                index_global = i + j + 1
                
                dist_str = t(langue, "guerre_dist_lieues", d=int(t_cnd['dist']), defaut=f"{int(t_cnd['dist'])} lieues")
                diff_pp = t_cnd['pp'] - a_pp
                
                txt_plus = t(langue, "guerre_cible_diff_plus", pp=format_num(diff_pp), defaut=f"(+{format_num(diff_pp)} PP)")
                txt_moins = t(langue, "guerre_cible_diff_moins", pp=format_num(diff_pp), defaut=f"({format_num(diff_pp)} PP)")
                diff_txt = txt_plus if diff_pp > 0 else txt_moins
                
                is_under_colombe = False
                if t_cnd.get('peace_disabled_at') and t_cnd['peace_disabled_at'] != "null":
                    try:
                        dt_peace = datetime.fromisoformat(t_cnd['peace_disabled_at'].replace('Z', '+00:00'))
                        if dt_peace > discord.utils.utcnow():
                            is_under_colombe = True
                    except: pass

                target_icon = "<:peace:1512503935892586566>" if is_under_colombe else "<:players:1512504277392953426>"

                warnings = []
                if t_cnd['wall_warning']: warnings.append(t(langue, "guerre_warn_wall", defaut="\n<:error:1512505075220611172> **VÉRIFIEZ LE MUR :** Description d'alliance sensible !"))
                if t_cnd['is_upper_tier'] and not config.get("ignore_tiers"): warnings.append(t(langue, "guerre_warn_tier", defaut="\n<:error:1512505075220611172> **RISQUE DE REPRESAILLES :** Joueur du palier supérieur !"))
                if is_under_colombe: warnings.append(t(langue, "guerre_warn_peace", defaut="\n<:peace:1512503935892586566> **JOUEUR SOUS COLOMBE : Protection active (Inattaquable) !**"))
                
                warning_txt = "".join(warnings) if warnings else ""
                
                description_cible = (
                    f"<:icon_alliance:1512573872774451210> {lbl_alli} **{t_cnd['alliance']}**\n"
                    f"<:lvl:1512571152524906596> {lbl_lvl} {t_cnd['lvl']}/{t_cnd['leg']} ({lbl_palier} {t_cnd['tier']})\n"
                    f"<:pp1:1512438903821570160> {lbl_puiss} {format_num(t_cnd['pp'])} {diff_txt}\n"
                    f"<:honor2:1512573861521260544> {lbl_honneur} **{format_num(t_cnd['honor'])}**\n"
                    f"<:map:1512573907788501242> {lbl_dist} **{dist_str}**\n"
                    f"<:coords:1512574624112578580> {lbl_coords} `{t_cnd['x']}:{t_cnd['y']}`\n"
                    f"{warning_txt}\n━━━━━━━━━━━━━━━━━━━━━━━"
                )
                
                name_cible = t(langue, "guerre_cible_field_title", idx=index_global, n=t_cnd['name'], icon=target_icon, defaut=f"{target_icon} Cible #{index_global} : {t_cnd['name']}")
                embed.add_field(name=name_cible, value=description_cible, inline=False)

            titre_page = t(langue, "guerre_cible_footer_page", cur=page_num, tot=nb_pages, defaut=f"Page {page_num}/{nb_pages}")
            val_spy = t(langue, "guerre_cible_footer_spy", defaut="<:icon_name:1512505444172697611> **SPY OBLIGATOIRE** avant impact.")
            embed.add_field(name=titre_page, value=val_spy, inline=False)
            
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        view = CiblePaginationView(self, attacker, sort_by, target_alliance, embeds, langue, owner_id=interaction.user.id)
        
        if message_to_edit:
            await message_to_edit.edit(content=None, embed=embeds[0], view=view)
        else:
            await interaction.followup.send(embed=embeds[0], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=15, langue=langue)

    # ==========================================
    # ⚖️ MOTEUR D'EXÉCUTION CENTRALISÉ HR
    # ==========================================
    async def _execute_hr(self, interaction: discord.Interaction, attacker: str, defender: str):
        langue, serveur = await get_server_config(interaction)

        if attacker.lower() == defender.lower(): 
            msg = t(langue, "guerre_hr_err_self", defaut="<:error:1512505075220611172> Tu ne peux pas t'attaquer toi-même, voyons ! 😂")
            return await interaction.followup.send(msg)

        user_id = str(interaction.user.id)
        user_rules = utils.USERS_CONFIG_CACHE.get(user_id, {}).get("custom_rules", {})
        
        config = {
            "nom": "Règles Personnelles",
            "allowed_tiers_relative": list(range(user_rules.get("tier_diff", 0) + 1)),
            "pp_offset_min": user_rules.get("pp_min", -3000000),
            "pp_offset_max": user_rules.get("pp_max", 10000000),
            "min_distance": user_rules.get("min_dist", 0),
            "ignore_tiers": user_rules.get("ignore_tiers", False)
        }
        nom_regle_titre = t(langue, "rules_name_perso", defaut="Règles Personnelles")

        cache_data = await get_cached_data(serveur)
        local_data = cache_data.get('players_data', {})

        def get_tier(lvl, leg):
            if lvl < 70: return 0
            if leg <= 120: return 1
            if leg <= 360: return 2
            if leg <= 650: return 3
            if leg <= 949: return 4
            return 5

        a_info, d_info, a_name, d_name = None, None, attacker, defender
        for name, info in local_data.items():
            if name.lower() == attacker.lower(): a_info, a_name = info, name
            if name.lower() == defender.lower(): d_info, d_name = info, name

        if not a_info: return await interaction.followup.send(t(langue, "guerre_hr_err_atk", a=attacker, defaut=f"<:error:1512505075220611172> L'attaquant **{attacker}** est introuvable."))
        if not d_info: return await interaction.followup.send(t(langue, "guerre_hr_err_def", d=defender, defaut=f"<:error:1512505075220611172> Le défenseur **{defender}** est introuvable."))

        a_lvl, a_leg, a_pp = int(a_info.get('level', 0)), int(a_info.get('legendary_level', 0)), int(a_info.get('main_points', 0))
        d_lvl, d_leg, d_pp = int(d_info.get('level', 0)), int(d_info.get('legendary_level', 0)), int(d_info.get('main_points', 0))
        a_tier, d_tier = get_tier(a_lvl, a_leg), get_tier(d_lvl, d_leg)
        
        a_alli = a_info.get('alliance') or a_info.get('alliance_name')
        if isinstance(a_alli, dict): a_alli = a_alli.get('name')
        d_alli = d_info.get('alliance') or d_info.get('alliance_name')
        if isinstance(d_alli, dict): d_alli = d_alli.get('name')

        a_coords, d_coords = None, None
        headers = await get_api_headers(interaction)
        
        live_castles = []
        for p_name, is_atk in [(a_name, True), (d_name, False)]:
            try:
                async with self.bot.session.get(f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(p_name)}", headers=headers, timeout=5) as r:
                    if r.status == 200:
                        c_data = await r.json()
                        if isinstance(c_data, dict): c_data = [c_data]
                        if isinstance(c_data, list) and c_data:
                            live_castles.append(c_data[0])
                            for c in c_data:
                                if str(c.get('kingdomId', c.get('kingdom_id', 'X'))) == "0" and str(c.get('type', c.get('castle_type', 'X'))) == "1":
                                    x, y = c.get('positionX') or c.get('position_x') or c.get('x'), c.get('positionY') or c.get('position_y') or c.get('y')
                                    if is_atk: a_coords = (int(x), int(y))
                                    else: d_coords = (int(x), int(y))
                                    break
            except: pass

        actualisation_dt = _get_api_timestamp(live_castles)
        distance = math.hypot(d_coords[0] - a_coords[0], d_coords[1] - a_coords[1]) if a_coords and d_coords else None
        
        infractions, avertissements = [], []
        
        if d_alli:
            d_alli_clean = "".join(c for c in str(d_alli).lower() if c.isalnum())
            try:
                fichier_murs = BASE_DATA_PATH / 'murs_scans' / serveur / 'murs_alliances.json'
                if fichier_murs.exists():
                    with open(fichier_murs, encoding='utf-8') as f:
                        for nom_json, desc in json.load(f).items():
                            if "".join(c for c in str(nom_json).lower() if c.isalnum()) == d_alli_clean:
                                desc_mur = str(desc).lower()
                                mot_trouve = next((mot for mot in ["repos", "deuil", "hospitalisé"] if mot in desc_mur), None)
                                if mot_trouve: 
                                    avertissements.append(t(langue, "guerre_hr_diplo_wall", m=mot_trouve.capitalize(), defaut=f"<:alliance_icon:1512574688415580242> **Mur d'alliance** : Mot-clé sensible détecté (**{mot_trouve.capitalize()}**)."))
                                break
            except: pass

        min_dist = config.get("min_distance", 0)
        if distance is not None and distance < min_dist: 
            infractions.append(t(langue, "guerre_hr_dist_short", d=int(distance), m=min_dist, defaut=f"<:icon_search:1512505406474293438> **Distance** : Cible trop proche ! Distance : **{int(distance)} lieues** (Règlement exige Min: {min_dist})."))

        if "pp_offset_min" in config and d_pp < (a_pp + config["pp_offset_min"]):
            infractions.append(t(langue, "guerre_hr_pp_diff_low", d1=format_num(a_pp - d_pp), d2=format_num(abs(config['pp_offset_min'])), defaut=f"<:pp1:1512438903821570160> **Écart de Puissance** : Tu as {format_num(a_pp - d_pp)} PP de plus (L'écart max autorisé vers le bas est de {format_num(abs(config['pp_offset_min']))})."))
        if "pp_offset_max" in config and d_pp > (a_pp + config["pp_offset_max"]):
            avertissements.append(t(langue, "guerre_hr_pp_diff_high", d1=format_num(d_pp - a_pp), defaut=f"<:pp1:1512438903821570160> **Défenseur plus fort** : Le défenseur a {format_num(d_pp - a_pp)} PP de plus que toi. Prudence."))

        if not config.get("ignore_tiers", False):
            diff_tier = d_tier - a_tier
            allowed_tiers = config.get("allowed_tiers_relative", [0])
            
            if diff_tier < min(allowed_tiers):
                infractions.append(t(langue, "guerre_hr_tier_low", at=a_tier, dt=d_tier, defaut=f"<:lvl:1512571152524906596> **Écart de Palier** : Tu (Palier {a_tier}) n'as pas le droit d'attaquer un joueur de Palier inférieur ({d_tier})."))
            elif diff_tier > max(allowed_tiers):
                avertissements.append(t(langue, "guerre_hr_tier_high", dt=d_tier, defaut=f"<:lvl:1512571152524906596> **Niveau élevé** : Tu attaques un Palier supérieur ({d_tier}). Risque de représailles."))

        dist_txt = f"{int(distance)} lieues" if distance else t(langue, "guerre_prox_dist_unk", defaut="<:error:1512505075220611172> Inconnue")

        diff_pp = d_pp - a_pp
        if diff_pp > 0:
            diff_txt = t(langue, "guerre_hr_diff_txt_high", pp=format_num(diff_pp), defaut=f"+{format_num(diff_pp)} PP (Défenseur plus fort)")
        elif diff_pp < 0:
            diff_txt = t(langue, "guerre_hr_diff_txt_low", pp=format_num(abs(diff_pp)), defaut=f"-{format_num(abs(diff_pp))} PP (Défenseur plus faible)")
        else:
            diff_txt = t(langue, "guerre_hr_diff_txt_eq", defaut="Égalité stricte")

        embed = discord.Embed(title=t(langue, "guerre_hr_title", r=nom_regle_titre, a=a_name, d=d_name, defaut=f"<:4_:1512574743369224303> Arbitrage {nom_regle_titre} : {a_name} 🆚 {d_name}"), color=self.clr_hr)
        
        a_sa = t(langue, "guerre_sa", defaut="Sans alliance")
        a_alli_txt = a_alli or a_sa
        d_alli_txt = d_alli or a_sa
        
        embed.add_field(name=t(langue, "guerre_hr_field_atk", a=a_name, defaut=f"⚔️ Attaquant : {a_name}"), value=f"<:icon_alliance:1512573872774451210> {a_alli_txt}\n<:lvl:1512571152524906596> Lvl {a_lvl}/{a_leg} (Palier {a_tier})\n<:pp2:1512571027119538335> {format_num(a_pp)} PP", inline=True)
        embed.add_field(name=t(langue, "guerre_hr_field_def", d=d_name, defaut=f"🛡️ Défenseur : {d_name}"), value=f"<:icon_alliance:1512573872774451210> {d_alli_txt}\n<:lvl:1512571152524906596> Lvl {d_lvl}/{d_leg} (Palier {d_tier})\n<:pp2:1512571027119538335> {format_num(d_pp)} PP", inline=True)
        
        lbl_dist_data = t(langue, "guerre_cible_field_dist", defaut="Distance :")
        lbl_diff_data = t(langue, "guerre_hr_field_diff", defaut="Différence :")
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
        
        embed.add_field(
            name=t(langue, "guerre_hr_field_data_title", defaut="📊 Données entre les joueurs"), 
            value=f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n<:compass:1512504625364729987> {lbl_dist_data} **{dist_txt}**\n<:pp2:1512571027119538335> {lbl_diff_data} **{diff_txt}**", 
            inline=False
        )
        
        await setup_embed_footer(embed, interaction, langue)

        if infractions:
            embed.color = discord.Color.red()
            embed.add_field(name=t(langue, "guerre_hr_res_red_t", defaut="❌ HORS RÈGLES (HR)"), value=t(langue, "guerre_hr_res_red_d", defaut="__L'attaque est formellement interdite selon ton profil :__\n\n") + "\n".join([f"• {i}" for i in infractions]), inline=False)
            if avertissements:
                embed.add_field(name=t(langue, "guerre_hr_res_warn", defaut="⚠️ Autres observations"), value="\n".join([f"• {a}" for a in avertissements]), inline=False)
            await interaction.followup.send(embed=embed)
        elif avertissements:
            embed.color = discord.Color.orange()
            embed.add_field(name=t(langue, "guerre_hr_res_ora_t", defaut="⚠️ ATTAQUE EN RÈGLES (Mais Risquée)"), value=t(langue, "guerre_hr_res_ora_d", defaut="__L'attaque respecte tes limites, mais attention :__\n\n") + "\n".join([f"• {a}" for a in avertissements]), inline=False)
            await interaction.followup.send(embed=embed)
        else:
            embed.color = discord.Color.green()
            embed.add_field(name=t(langue, "guerre_hr_res_gre_t", defaut="✅ ATTAQUE EN RÈGLES"), value=t(langue, "guerre_hr_res_gre_d", defaut="Aucune infraction ni avertissement détecté selon tes limites."), inline=False)
            await interaction.followup.send(embed=embed)
        await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)


async def setup(bot: commands.Bot):
    await bot.add_cog(GuerreCog(bot))