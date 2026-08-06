# -*- coding: utf-8 -*-
import os
import json
import asyncio
import aiohttp
import urllib.parse
import math
import random
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands

from utils import (
    BASE_DATA_PATH, 
    ALLIANCES_DIR,  
    CONFIG_DIR,     
    _get_api_timestamp,
    t,              
    joueur_autocomplete, 
    alliance_autocomplete, 
    format_num, 
    BOT_VERSION,
    MON_ID_DISCORD,
    setup_embed_footer,
    get_cached_data,
    load_diplo_async,
    save_diplo_async,
    PaginationView,
    get_api_headers,     
    get_server_config    
)

logger = logging.getLogger("GGE_Bot")

def get_alliance_diplo_key(data, alliance_name):
    if not alliance_name: return None
    for key in data.keys():
        if key.lower() == alliance_name.lower():
            return key
    return alliance_name

REGLEMENTS_FILE = ALLIANCES_DIR / 'reglements.json'

async def load_reglements_async():
    """Charge dynamiquement le fichier JSON des règlements avec un repli par défaut."""
    if not REGLEMENTS_FILE.exists():
        return {
            "cdr": {
                "nom": "Règles CDR Strictes",
                "check_api_limit": True,
                "api_limit_threshold": 50000000,
                "allowed_tiers_relative": [0, 1],
                "pp_offset_min": -3000000,
                "pp_offset_max": 10000000,
                "tier_0_max_lvl_diff": 10
            }
        }
    try:
        with open(REGLEMENTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"❌ [CRITIQUE] Impossible de lire reglements.json : {e}")
        return {
            "cdr": {"check_api_limit": True, "api_limit_threshold": 50000000, "allowed_tiers_relative": [0, 1], "pp_offset_min": -3000000, "pp_offset_max": 10000000, "tier_0_max_lvl_diff": 10}
        }

# ========================================================
# 🎛️ COMPOSANT UI : PAGINATION DES CIBLES + RELANCE EN DIRECT
# ========================================================
class CiblePaginationView(discord.ui.View):
    def __init__(self, cog, attacker, sort_by, target_alliance, embeds, ruleset="cdr", langue="fr"):
        super().__init__(timeout=1800)
        self.cog = cog
        self.attacker = attacker
        self.sort_by = sort_by
        self.target_alliance = target_alliance
        self.embeds = embeds
        self.current_page = 0
        self.ruleset = ruleset
        self.langue = langue
        
        self.btn_prev.label = t(langue, "guerre_btn_prev", defaut="⏮️ Page Précédente")
        self.btn_next.label = t(langue, "guerre_btn_next", defaut="Page Suivante ⏭️")
        self.btn_rerun.label = t(langue, "guerre_btn_rerun", defaut="🔄 Relancer une vague")
        self.update_buttons()

    def update_buttons(self):
        self.btn_prev.disabled = self.current_page == 0
        self.btn_next.disabled = self.current_page == len(self.embeds) - 1

    @discord.ui.button(style=discord.ButtonStyle.secondary, custom_id="cible_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(style=discord.ButtonStyle.secondary, custom_id="cible_next")
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(style=discord.ButtonStyle.primary, custom_id="cible_rerun")
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
            message_to_edit=interaction.message, 
            ruleset=self.ruleset
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
                title=t(langue, "guerre_prox_title", a=enemy_alliance, defaut=f"\<:attaque:1512570903886692474> Cibles de Proximité : {enemy_alliance}"), 
                color=self.clr_proximite
            )
            
            desc_i18n = t(langue, "guerre_prox_desc", p=my_player, x=my_x, y=my_y, n=len(valid_targets), defaut=f"🛰️ Ton point de départ : **{my_player}** (`{my_x}:{my_y}`)\n<:icon_search:1512505406474293438> **{len(valid_targets)}** cibles localisées au total.")
            
            lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
            embed.description = f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{desc_i18n}"
            
            lbl_dist = t(langue, "guerre_prox_field_dist", defaut="Distance :")
            lbl_coords = t(langue, "guerre_prox_field_coords", defaut="Coords :")
            lbl_pp = t(langue, "guerre_prox_field_pp", defaut="Puissance :")
            lbl_col_yes = t(langue, "guerre_prox_colombe", defaut="<:peace:1512503935892586566> **SOUS COLOMBE**")
            lbl_col_no = t(langue, "guerre_prox_vuln", defaut="\<:attaque:1512570903886692474> **VULNÉRABLE**")

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

    # ==========================================
    # 🎯 COMMANDE : TARGET
    # ==========================================
    @app_commands.command(name="target", description="Find legal targets according to the chosen regulations")
    @app_commands.autocomplete(attacker=joueur_autocomplete)
    @app_commands.autocomplete(target_alliance=alliance_autocomplete)
    @app_commands.choices(ruleset=[
        app_commands.Choice(name="⚖️ Comité des Rois (CDR)", value="cdr"),
        app_commands.Choice(name="⚔️ QG ETERNITY", value="eternity"),
        app_commands.Choice(name="🛡️ SØNS ØF GOD (SOG)", value="sog")
    ])
    async def target(self, interaction: discord.Interaction, attacker: str, ruleset: str, target_alliance: str = None):
        try: await interaction.response.defer(thinking=True)
        except: return
        await self._execute_cible(interaction, attacker, "aleatoire", target_alliance, ruleset=ruleset)

    # ==========================================
    # ⚙️ MOTEUR D'EXÉCUTION CENTRALISÉ DU SCAN
    # ==========================================
    async def _execute_cible(self, interaction: discord.Interaction, attacker: str, sort_by: str = "aleatoire", target_alliance: str = None, message_to_edit=None, ruleset: str = "cdr"):
        langue, serveur = await get_server_config(interaction)

        def get_tier(lvl, leg):
            if lvl < 70: return 0
            if leg <= 120: return 1
            if leg <= 360: return 2
            if leg <= 650: return 3
            if leg <= 949: return 4
            return 5

        all_rules = await load_reglements_async()
        rules = all_rules.get(ruleset, all_rules.get("cdr"))

        def is_legal_target(a_pp, a_tier, a_lvl, t_pp, t_tier, t_lvl):
            if rules.get("check_api_limit"):
                limit = rules.get("api_limit_threshold", 50000000)
                if a_pp >= limit:
                    if t_pp < limit: return False
                    if rules.get("api_limit_no_rules_above"): return True
                elif t_pp >= limit:
                    return False 

            config = rules
            if rules.get("tranches_pp"):
                matched = False
                for tranche in rules["tranches_pp"]:
                    if tranche.get("a_min", 0) <= a_pp <= tranche.get("a_max", 999999999):
                        config = tranche
                        matched = True
                        break
                if not matched: return False

            if not config.get("ignore_tiers", rules.get("ignore_tiers", False)):
                diff_tier = t_tier - a_tier
                allowed_tiers = config.get("allowed_tiers_relative", rules.get("allowed_tiers_relative", [0]))
                if diff_tier not in allowed_tiers: return False

                if a_tier == 0 and t_tier == 0:
                    max_lvl_diff = config.get("tier_0_max_lvl_diff", rules.get("tier_0_max_lvl_diff", 10))
                    if abs(a_lvl - t_lvl) > max_lvl_diff: return False

            if "t_min" in config and t_pp < config["t_min"]: return False
            if "t_max" in config and t_pp > config["t_max"]: return False
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
        
        raw_a_alliance = a_info.get('alliance_name') or a_info.get('allianceName') or a_info.get('alliance') or ''
        if isinstance(raw_a_alliance, dict): 
            a_alliance = raw_a_alliance.get('name') or raw_a_alliance.get('alliance_name') or ''
        else: 
            a_alliance = str(raw_a_alliance)

        txt_sans_alliance = t(langue, "guerre_sa", defaut="Sans alliance")
        a_alli_clean = "".join(c for c in a_alliance.lower() if c.isalnum()) if a_alliance and a_alliance != txt_sans_alliance else ""

        allies_et_pna_clean = []
        try:
            if a_alliance and a_alliance != txt_sans_alliance:
                diplo_data = await load_diplo_async()
                for key, diplo in diplo_data.items():
                    if key.lower() == a_alliance.lower() and diplo.get("guild_id") == interaction.guild_id:
                        allies_et_pna_clean = ["".join(c for c in str(a).lower() if c.isalnum()) for a in diplo.get("allies", []) + diplo.get("pna", [])]
                        break
        except: pass

        mots_interdits_mur, alliances_mur_alerte = ["repos", "deuil", "hospitalisé"], []
        try:
            fichier_murs = BASE_DATA_PATH / 'murs_scans' / serveur / 'murs_alliances.json'
            if fichier_murs.exists():
                with open(fichier_murs, 'r', encoding='utf-8') as f:
                    for aname_json, desc in json.load(f).items():
                        if any(mot in str(desc).lower() for mot in mots_interdits_mur):
                            alliances_mur_alerte.append("".join(c for c in str(aname_json).lower() if c.isalnum()))
        except: pass

        pool_candidats = []
        for t_name, t_info in local_data.items():
            if t_name.lower() == a_name_real.lower(): continue
            
            raw_t_alliance = t_info.get('alliance') or t_info.get('alliance_name') or t_info.get('allianceName') or ''
            if isinstance(raw_t_alliance, dict): 
                t_alliance = raw_t_alliance.get('name') or raw_t_alliance.get('alliance_name') or ''
            else: 
                t_alliance = str(raw_t_alliance)
                
            if not t_alliance or t_alliance == txt_sans_alliance: continue
            
            alli_clean = "".join(c for c in t_alliance.lower() if c.isalnum())

            if a_alli_clean and alli_clean == a_alli_clean: continue
            if target_alliance and t_alliance.lower() != target_alliance.lower(): continue
            if alli_clean in allies_et_pna_clean: continue

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
                nom_regle_raw = rules.get("nom", ruleset.upper())
                nom_regle = t(langue, nom_regle_raw, defaut=nom_regle_raw)
            
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
                    min_dist = rules.get("min_distance", 0)
                    if t_cnd['dist'] < min_dist: 
                        continue
                    final_targets.append(t_cnd)
            
            await asyncio.sleep(0.15)

        if not final_targets:
            nom_regle = {
                "cdr": t(langue, "rules_name_cdr", defaut="Règles CDR"), 
                "eternity": t(langue, "rules_name_eternity", defaut="Règlement Eternity"), 
                "sog": t(langue, "rules_name_sog", defaut="Règles SOG")
            }.get(ruleset, "Inconnue")
            
            empty_msg = t(langue, "guerre_err_no_target_valid", regle=nom_regle, defaut=f"<:error:1512505075220611172> Les cibles potentielles ne respectent plus les règles (**{nom_regle}**) avec leurs puissances actuelles ou sont hors-ligne.")
            if message_to_edit: await message_to_edit.edit(content=empty_msg, view=None)
            else: await interaction.followup.send(empty_msg)
            return

        actualisation_dt = _get_api_timestamp(a_info, final_targets)
        active_affichage = rules.get("affichage", {})
        if rules.get("tranches_pp"):
            for tranche in rules["tranches_pp"]:
                if tranche.get("a_min", 0) <= a_pp <= tranche.get("a_max", 999999999):
                    active_affichage = tranche.get("affichage", active_affichage)
                    break
        
        key_feu = active_affichage.get("feu_min_soldats")
        txt_feu = t(langue, key_feu, defaut=str(key_feu)) if key_feu else t(langue, "guerre_regle_none", defaut="Non défini")
        
        key_ap = active_affichage.get("ap_regle")
        txt_ap = t(langue, key_ap, defaut=str(key_ap)) if key_ap else t(langue, "guerre_regle_none", defaut="Non défini")
        
        key_attente = active_affichage.get("cooldown")
        txt_attente = t(langue, key_attente, defaut=str(key_attente)) if key_attente else t(langue, "guerre_regle_none", defaut="Non défini")
        
        key_max_att = active_affichage.get("max_attaques")
        txt_max_att = t(langue, key_max_att, defaut=str(key_max_att)) if key_max_att else t(langue, "guerre_regle_none", defaut="Non défini")
        
        key_interdit = active_affichage.get("interdictions")
        txt_interdit = t(langue, key_interdit, defaut=str(key_interdit)) if key_interdit else t(langue, "guerre_regle_none_spec", defaut="Aucune spécifique")

        best_targets = final_targets[:10]
        embeds = []
        chunk_size = 5 
        nb_pages = max(1, (len(best_targets) - 1) // chunk_size + 1)
        
        lbl_alli_target = t(langue, "guerre_cible_alli_target", a=target_alliance, defaut=f" (Alliance : {target_alliance})")
        titre_alliance = lbl_alli_target if target_alliance else ""
        
        nom_regle_titre = {
            "cdr": t(langue, "rules_name_cdr", defaut="Règles CDR"), 
            "eternity": t(langue, "rules_name_eternity", defaut="Règlement Eternity"), 
            "sog": t(langue, "rules_name_sog", defaut="Règles SOG")
        }.get(ruleset, "")
        
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
                if t_cnd['is_upper_tier'] and not rules.get("ignore_tiers"): warnings.append(t(langue, "guerre_warn_tier", defaut="\n<:error:1512505075220611172> **RISQUE DE REPRESAILLES :** Joueur du palier supérieur !"))
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

            reglement_texte = t(langue, "guerre_cible_rules", ma=txt_max_att, att=txt_attente, f=txt_feu, ap=txt_ap, inter=txt_interdit, defaut=(
                f"⚔️ Limite d'attaque : **{txt_max_att}** | ⏳ Attente : **{txt_attente}**\n"
                f"<:fire:1512573853774254303> Si feu : **{txt_feu}** | <:avp:1512572561647468555> Règle AVP : **{txt_ap}**\n"
                f"🚫 Interdictions : *{txt_interdit}*"
            ))
            
            titre_regles = t(langue, "guerre_cible_rules_title", defaut="<:members:1512573912305766652> Règlement en vigueur\n\n")
            embed.add_field(name=titre_regles, value=reglement_texte, inline=False)

            titre_page = t(langue, "guerre_cible_footer_page", cur=page_num, tot=nb_pages, defaut=f"Page {page_num}/{nb_pages}")
            val_spy = t(langue, "guerre_cible_footer_spy", defaut="<:icon_name:1512505444172697611> **SPY OBLIGATOIRE** avant impact.")
            embed.add_field(name=titre_page, value=val_spy, inline=False)
            
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        view = CiblePaginationView(self, attacker, sort_by, target_alliance, embeds, ruleset, langue)
        
        if message_to_edit:
            await message_to_edit.edit(content=None, embed=embeds[0], view=view)
        else:
            await interaction.followup.send(embed=embeds[0], view=view)

    # ==========================================
    # ⚖️ COMMANDE : HR
    # ==========================================
    @app_commands.command(name="hr", description="Check if an attack between two players complies with the chosen rules.")
    @app_commands.autocomplete(attacker=joueur_autocomplete)
    @app_commands.autocomplete(defender=joueur_autocomplete)
    @app_commands.choices(ruleset=[
        app_commands.Choice(name="⚖️ Comité des Rois (CDR)", value="cdr"),
        app_commands.Choice(name="⚔️ QG ETERNITY", value="eternity"),
        app_commands.Choice(name="🛡️ SØNS ØF GOD", value="sog")
    ])
    async def hr(self, interaction: discord.Interaction, attacker: str, defender: str, ruleset: str):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        langue, serveur = await get_server_config(interaction)

        if attacker.lower() == defender.lower(): 
            msg = t(langue, "guerre_hr_err_self", defaut="<:error:1512505075220611172> Tu ne peux pas t'attaquer toi-même, voyons ! 😂")
            return await interaction.followup.send(msg)

        all_rules = await load_reglements_async()
        rules = all_rules.get(ruleset, all_rules.get("cdr"))
        nom_regle_raw = rules.get("nom", ruleset.upper())
        nom_regle_titre = t(langue, nom_regle_raw, defaut=nom_regle_raw)

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
        allies_propres, pna_propres, diplo_privee = [], [], False
        
        try:
            if a_alli:
                diplo_data = await load_diplo_async()
                for key, diplo in diplo_data.items():
                    if key.lower() == a_alli.lower():
                        if diplo.get("guild_id") == interaction.guild_id:
                            allies_propres = ["".join(c for c in str(a).lower() if c.isalnum()) for a in diplo.get("allies", [])]
                            pna_propres = ["".join(c for c in str(a).lower() if c.isalnum()) for a in diplo.get("pna", [])]
                        else: diplo_privee = True
                        break
        except: pass

        if d_alli:
            d_alli_clean = "".join(c for c in str(d_alli).lower() if c.isalnum())
            if d_alli_clean in allies_propres: 
                infractions.append(t(langue, "guerre_hr_diplo_ally", d_alli=d_alli, defaut=f"<:4_:1512574743369224303> **Diplomatie** : Le défenseur est dans une alliance ALLIÉE (*{d_alli}*)."))
            elif d_alli_clean in pna_propres: 
                infractions.append(t(langue, "guerre_hr_diplo_pna", d_alli=d_alli, defaut=f"<:4_:1512574743369224303> **Diplomatie** : Le défenseur est dans une alliance PNA (*{d_alli}*)."))
            
            try:
                fichier_murs = BASE_DATA_PATH / 'murs_scans' / serveur / 'murs_alliances.json'
                if fichier_murs.exists():
                    with open(fichier_murs, 'r', encoding='utf-8') as f:
                        for nom_json, desc in json.load(f).items():
                            if "".join(c for c in str(nom_json).lower() if c.isalnum()) == d_alli_clean:
                                desc_mur = str(desc).lower()
                                mot_trouve = next((mot for mot in ["repos", "deuil", "hospitalisé"] if mot in desc_mur), None)
                                if mot_trouve: 
                                    avertissements.append(t(langue, "guerre_hr_diplo_wall", m=mot_trouve.capitalize(), defaut=f"<:alliance_icon:1512574688415580242> **Mur d'alliance** : Mot-clé sensible détecté (**{mot_trouve.capitalize()}**)."))
                                break
            except: pass

        config = rules
        if rules.get("tranches_pp"):
            for tranche in rules["tranches_pp"]:
                if tranche.get("a_min", 0) <= a_pp <= tranche.get("a_max", 999999999):
                    config = tranche
                    break

        skip_other_checks = False

        if rules.get("check_api_limit"):
            limit = rules.get("api_limit_threshold", 50000000)
            if a_pp >= limit:
                if d_pp < limit: 
                    infractions.append(t(langue, "guerre_hr_cross_league", l=format_num(limit), defaut=f"<:pp2:1512571027119538335> **Ligue croisée** : L'attaquant (> {format_num(limit)}) ne peut pas cibler un joueur sous le seuil."))
                elif rules.get("api_limit_no_rules_above"):
                    skip_other_checks = True 
            else:
                if d_pp >= limit: 
                    avertissements.append(t(langue, "guerre_hr_suicide", l=format_num(limit), defaut=f"<:pp2:1512571027119538335> **Mission Suicide** : Tu attaques un joueur au-dessus du seuil ({format_num(limit)}). Risque fort de représailles."))

        min_dist = config.get("min_distance", rules.get("min_distance", 0))
        if distance is not None and distance < min_dist: 
            infractions.append(t(langue, "guerre_hr_dist_short", d=int(distance), m=min_dist, defaut=f"<:icon_search:1512505406474293438> **Distance** : Cible trop proche ! Distance : **{int(distance)} lieues** (Règlement exige Min: {min_dist})."))

        if not skip_other_checks:
            if "t_min" in config and d_pp < config["t_min"]:
                infractions.append(t(langue, "guerre_hr_pp_low", min=format_num(config['t_min']), defaut=f"<:pp1:1512438903821570160> **Écart de Puissance** : La cible a trop peu de PP (Min exigé par ta tranche: {format_num(config['t_min'])})."))
            if "t_max" in config and d_pp > config["t_max"]:
                avertissements.append(t(langue, "guerre_hr_pp_high", max=format_num(config['t_max']), defaut=f"<:pp1:1512438903821570160> **Puissance élevée** : La cible dépasse la limite de ta tranche (Max conseillé: {format_num(config['t_max'])})."))

            if "pp_offset_min" in config and d_pp < (a_pp + config["pp_offset_min"]):
                infractions.append(t(langue, "guerre_hr_pp_diff_low", d1=format_num(a_pp - d_pp), d2=format_num(abs(config['pp_offset_min'])), defaut=f"<:pp1:1512438903821570160> **Écart de Puissance** : Tu as {format_num(a_pp - d_pp)} PP de plus (L'écart max autorisé vers le bas est de {format_num(abs(config['pp_offset_min']))})."))
            if "pp_offset_max" in config and d_pp > (a_pp + config["pp_offset_max"]):
                avertissements.append(t(langue, "guerre_hr_pp_diff_high", d1=format_num(d_pp - a_pp), defaut=f"<:pp1:1512438903821570160> **Défenseur plus fort** : Le défenseur a {format_num(d_pp - a_pp)} PP de plus que toi. Prudence."))

            if not config.get("ignore_tiers", rules.get("ignore_tiers", False)):
                diff_tier = d_tier - a_tier
                allowed_tiers = config.get("allowed_tiers_relative", rules.get("allowed_tiers_relative", [0]))
                
                if diff_tier < min(allowed_tiers):
                    infractions.append(t(langue, "guerre_hr_tier_low", at=a_tier, dt=d_tier, defaut=f"<:lvl:1512571152524906596> **Écart de Palier** : Tu (Palier {a_tier}) n'as pas le droit d'attaquer un joueur de Palier inférieur ({d_tier})."))
                elif diff_tier > max(allowed_tiers):
                    avertissements.append(t(langue, "guerre_hr_tier_high", dt=d_tier, defaut=f"<:lvl:1512571152524906596> **Niveau élevé** : Tu attaques un Palier supérieur ({d_tier}). Risque de représailles."))

                if a_tier == 0 and d_tier == 0:
                    max_lvl_diff = config.get("tier_0_max_lvl_diff", rules.get("tier_0_max_lvl_diff", 10))
                    if a_lvl > d_lvl + max_lvl_diff:
                        infractions.append(t(langue, "guerre_hr_lvl_low", dl=a_lvl - d_lvl, m=max_lvl_diff, defaut=f"<:lvl:1512571152524906596> **Niveaux <70** : Tu as {a_lvl - d_lvl} niveaux de plus (Max autorisé: +{max_lvl_diff})."))
                    elif a_lvl < d_lvl - max_lvl_diff:
                        avertissements.append(t(langue, "guerre_hr_lvl_high", dl=d_lvl - a_lvl, defaut=f"<:lvl:1512571152524906596> **Défenseur plus fort** : La cible a {d_lvl - a_lvl} niveaux de plus que toi."))

        affichage = config.get("affichage", rules.get("affichage", {}))
        
        key_ap = affichage.get("ap_regle")
        txt_ap = t(langue, key_ap, defaut=str(key_ap)) if key_ap else t(langue, "guerre_regle_none", defaut="Non défini")
        
        key_troops = affichage.get("feu_min_soldats")
        min_troops = t(langue, key_troops, defaut=str(key_troops)) if key_troops else t(langue, "guerre_regle_none", defaut="Non défini")
        
        key_max_att = affichage.get("max_attaques")
        txt_max_att = t(langue, key_max_att, defaut=str(key_max_att)) if key_max_att else t(langue, "guerre_regle_none", defaut="Non défini")
        
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
        
        lbl_att = t(langue, "guerre_hr_lbl_att", defaut="Limite d'attaques :")
        lbl_ap = t(langue, "guerre_hr_lbl_ap", defaut="Cibles AP :")
        lbl_troops = t(langue, "guerre_hr_lbl_troops", defaut="Min. Soldats (Si feu) :")
        
        embed.add_field(name=t(langue, "guerre_hr_field_cond_title", defaut="\<:attaque:1512570903886692474> Conditions de l'attaque"), value=f"<:Porteurs_de_bouclier:1512574622271279114> {lbl_att} **{txt_max_att}**\n<:castle4:1512573820752498839> {lbl_ap} **{txt_ap}**\n<:troop:1512573768893989015> {lbl_troops} **{min_troops}**", inline=False)
        
        if diplo_privee: embed.add_field(name=t(langue, "guerre_hr_diplo_priv_title", defaut="<:4_:1512574743369224303> Sécurité Diplomatique"), value=t(langue, "guerre_hr_diplo_priv_val", defaut="La diplomatie de l'attaquant est gérée par un autre serveur. Liens d'alliance ignorés."), inline=False)
        await setup_embed_footer(embed, interaction, langue)

        if infractions:
            embed.color = discord.Color.red()
            embed.add_field(name=t(langue, "guerre_hr_res_red_t", defaut="❌ HORS RÈGLES (HR)"), value=t(langue, "guerre_hr_res_red_d", defaut="__L'attaque is formellement interdite selon le règlement :__\n\n") + "\n".join([f"• {i}" for i in infractions]), inline=False)
            if avertissements:
                embed.add_field(name=t(langue, "guerre_hr_res_warn", defaut="⚠️ Autres observations"), value="\n".join([f"• {a}" for a in avertissements]), inline=False)
            await interaction.followup.send(embed=embed)

        elif avertissements:
            embed.color = discord.Color.orange()
            embed.add_field(name=t(langue, "guerre_hr_res_ora_t", defaut="⚠️ ATTAQUE EN RÈGLES (Mais Risquée)"), value=t(langue, "guerre_hr_res_ora_d", defaut="__L'attaque respecte les règles, mais attention :__\n\n") + "\n".join([f"• {a}" for a in avertissements]), inline=False)
            await interaction.followup.send(embed=embed)

        else:
            embed.color = discord.Color.green()
            embed.add_field(name=t(langue, "guerre_hr_res_gre_t", defaut="✅ ATTAQUE EN RÈGLES"), value=t(langue, "guerre_hr_res_gre_d", defaut="Aucune infraction ni avertissement détecté selon ce traité."), inline=False)
            await interaction.followup.send(embed=embed)

    # ==========================================
    # 🤝 GROUPE DE COMMANDES : DIPLOMACY
    # ==========================================
    diplo_group = app_commands.Group(
        name="diplomacy", 
        description="Diplomatic relations management (Admin only)",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True
    )

    @diplo_group.command(name="add", description="Defines the diplomatic status between two alliances")
    @app_commands.guild_only()
    @app_commands.autocomplete(my_alliance=alliance_autocomplete)
    @app_commands.autocomplete(target=alliance_autocomplete)
    @app_commands.choices(status=[
        app_commands.Choice(name="🟢 Ally", value="allies"),
        app_commands.Choice(name="🟡 PNA (Non-Aggression Pact)", value="pna"),
        app_commands.Choice(name="🔴 At War", value="guerre")
    ])
    async def d_add(self, interaction: discord.Interaction, my_alliance: str, target: str, status: app_commands.Choice[str]):
        langue, _ = await get_server_config(interaction)
        
        data = await load_diplo_async()
        source_key = get_alliance_diplo_key(data, my_alliance)
        
        if source_key not in data:
            data[source_key] = {"allies": [], "pna": [], "guerre": [], "guild_id": interaction.guild_id}
        else:
            owner_id = data[source_key].get("guild_id")
            if owner_id and owner_id != interaction.guild_id:
                return await interaction.response.send_message(t(langue, "guerre_diplo_err_owner", defaut="<:error:1512505075220611172> **Accès refusé** : La diplomatie de cette alliance est gérée par un autre serveur Discord."), ephemeral=True)
            elif not owner_id:
                data[source_key]["guild_id"] = interaction.guild_id
                
        target_lower = target.lower()
        for key in ["allies", "pna", "guerre"]:
            data[source_key][key] = [a for a in data[source_key][key] if a.lower() != target_lower]
            
        data[source_key][status.value].append(target)
        await save_diplo_async(data)
        
        emojis = {"allies": "🟢", "pna": "🟡", "guerre": "🔴"}
        msg = t(langue, "guerre_diplo_add_succ", e=emojis[status.value], a=my_alliance, c=target, s=status.name, defaut=f"<:4_:1512574743369224303> {emojis[status.value]} Pour **{my_alliance}**, l'alliance **{target}** a été enregistrée en tant que **{status.name}**.")
        await interaction.response.send_message(msg, ephemeral=True)

    @diplo_group.command(name="remove", description="Removes an alliance from your diplomacy (becomes neutral)")
    @app_commands.guild_only()
    @app_commands.autocomplete(my_alliance=alliance_autocomplete)
    @app_commands.autocomplete(target=alliance_autocomplete)
    async def d_remove(self, interaction: discord.Interaction, my_alliance: str, target: str):
        langue, _ = await get_server_config(interaction)
        
        data = await load_diplo_async()
        source_key = get_alliance_diplo_key(data, my_alliance)
        
        if source_key not in data:
            return await interaction.response.send_message(t(langue, "guerre_diplo_err_empty", a=my_alliance, defaut=f"<:4_:1512574743369224303> **{my_alliance}** n'a aucune diplomatie enregistrée."), ephemeral=True)

        owner_id = data[source_key].get("guild_id")
        if owner_id and owner_id != interaction.guild_id:
            return await interaction.response.send_message(t(langue, "guerre_diplo_err_owner", defaut="<:error:1512505075220611172> **Accès refusé** : La diplomatie de cette alliance est gérée par un autre serveur Discord."), ephemeral=True)

        target_lower = target.lower()
        trouve = False
        
        for key in ["allies", "pna", "guerre"]:
            if any(a.lower() == target_lower for a in data[source_key][key]):
                data[source_key][key] = [a for a in data[source_key][key] if a.lower() != target_lower]
                trouve = True
                
        if trouve:
            await save_diplo_async(data)
            await interaction.response.send_message(t(langue, "guerre_diplo_rem_succ", c=target, defaut=f"<:4_:1512574743369224303> L'alliance **{target}** a été retirée. Elle est redevenue Neutre."), ephemeral=True)
        else:
            await interaction.response.send_message(t(langue, "guerre_diplo_rem_fail", c=target, a=my_alliance, defaut=f"<:error:1512505075220611172> **{target}** n'est dans aucune liste diplomatique de **{my_alliance}**."), ephemeral=True)

    @diplo_group.command(name="list", description="Displays the diplomatic board of an alliance (Private)")
    @app_commands.guild_only()
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def d_list(self, interaction: discord.Interaction, alliance_name: str):
        langue, _ = await get_server_config(interaction)
        
        data = await load_diplo_async()
        source_key = get_alliance_diplo_key(data, alliance_name)
        
        if source_key not in data:
            return await interaction.response.send_message(t(langue, "guerre_diplo_list_empty", a=alliance_name, defaut=f"<:4_:1512574743369224303> Le registre diplomatique de **{alliance_name}** est vide."), ephemeral=True)
            
        diplo_alli = data[source_key]
        owner_id = diplo_alli.get("guild_id")
        
        if owner_id and owner_id != interaction.guild_id:
            return await interaction.response.send_message(t(langue, "guerre_diplo_list_denied", a=alliance_name, defaut=f"<:error:1512505075220611172> **Accès classifié** : Vous n'avez pas l'autorisation de consulter la diplomatie de **{alliance_name}** depuis ce serveur."), ephemeral=True)
        
        embed = discord.Embed(title=t(langue, "guerre_diplo_list_title", a=alliance_name, defaut=f"📜 Registre Diplomatique : {alliance_name}"), color=discord.Color.gold())
        
        aucun = t(langue, "guerre_diplo_list_none", defaut="*Aucun*")
        
        lbl_diplo_allies = t(langue, "guerre_diplo_allies", defaut="🟢 ALLIÉS")
        lbl_diplo_pna = t(langue, "guerre_diplo_pna", defaut="🟡 PNA")
        lbl_diplo_war = t(langue, "guerre_diplo_war", defaut="🔴 GUERRE")
        
        embed.add_field(name=lbl_diplo_allies, value="\n".join([f"🛡️ {a}" for a in diplo_alli.get("allies", [])]) or aucun, inline=True)
        embed.add_field(name=lbl_diplo_pna, value="\n".join([f"🤝 {a}" for a in diplo_alli.get("pna", [])]) or aucun, inline=True)
        embed.add_field(name=lbl_diplo_war, value="\n".join([f"⚔️ {a}" for a in diplo_alli.get("guerre", [])]) or aucun, inline=False)
        await setup_embed_footer(embed, interaction, langue)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ==========================================
    # 👻 ANTI-SUPPRESSION POUR i18n_sync
    # Ce code n'est jamais exécuté. Il sert juste à forcer 
    # le scanner à garder les clés dynamiques de reglements.json
    # ==========================================
    def _keep_json_translations_alive():
        # Noms des règlements
        t("fr", "rules_name_cdr")
        t("fr", "rules_name_eternity")
        t("fr", "rules_name_sog")

        # Cooldowns
        t("fr", "rules_cd_1week")
        t("fr", "rules_cd_72h")
        t("fr", "rules_cd_24h_losses")
        t("fr", "rules_cd_none")

        # Limites d'attaques
        t("fr", "rules_max_3fulls")
        t("fr", "rules_max_5fulls")

        # Troupes (Feu)
        t("fr", "rules_fire_5k")
        t("fr", "rules_fire_20k")
        t("fr", "rules_fire_50k")

        # Cibles AP
        t("fr", "rules_ap_gt_30m")
        t("fr", "rules_ap_multi_top150")
        t("fr", "rules_ap_none")

        # Interdictions
        t("fr", "rules_interdit_cdr_low")
        t("fr", "rules_interdit_cdr_high")
        t("fr", "rules_interdit_ete_high")
        t("fr", "rules_interdit_sog")

async def setup(bot: commands.Bot):
    await bot.add_cog(GuerreCog(bot))