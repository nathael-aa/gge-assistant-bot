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

# 🛠️ Import de la boîte à outils unifiée (Ajout des versions ASYNC)
from utils import (
    BASE_DATA_PATH, 
    joueur_autocomplete, 
    alliance_autocomplete, 
    format_num, 
    BOT_VERSION,
    MON_ID_DISCORD,
    setup_embed_footer,
    get_cached_data,
    load_diplo_async,
    save_diplo_async,
    PaginationView
)

logger = logging.getLogger("GGE_Bot")

def get_alliance_diplo_key(data, alliance_name):
    if not alliance_name: return None
    for key in data.keys():
        if key.lower() == alliance_name.lower():
            return key
    return alliance_name

REGLEMENTS_FILE = BASE_DATA_PATH / 'reglements.json'

async def load_reglements_async():
    """Charge dynamiquement le fichier JSON des règlements avec un repli par défaut."""
    if not REGLEMENTS_FILE.exists():
        # Configuration de secours par défaut si le fichier n'existe pas encore
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
        logger.error(f"🚨 [CRITIQUE] Impossible de lire reglements.json, utilisation des règles de secours : {e}")
        return {
            "cdr": {"check_api_limit": True, "api_limit_threshold": 50000000, "allowed_tiers_relative": [0, 1], "pp_offset_min": -3000000, "pp_offset_max": 10000000, "tier_0_max_lvl_diff": 10}
        }

# ========================================================
# 🎛️ COMPOSANT UI : PAGINATION DES CIBLES + RELANCE EN DIRECT
# ========================================================
class CiblePaginationView(discord.ui.View):
    def __init__(self, cog, attaquant, tri, alliance_cible, embeds, reglement="cdr"):
        super().__init__(timeout=1800) # 🧹 Corrigé : Actif pendant 30 min max pour préserver la RAM
        self.cog = cog
        self.attaquant = attaquant
        self.tri = tri
        self.alliance_cible = alliance_cible
        self.embeds = embeds
        self.current_page = 0
        self.reglement = reglement
        self.update_buttons()

    def update_buttons(self):
        self.btn_prev.disabled = self.current_page == 0
        self.btn_next.disabled = self.current_page == len(self.embeds) - 1

    @discord.ui.button(label="◀️ Page Précédente", style=discord.ButtonStyle.secondary, custom_id="cible_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(label="Page Suivante ▶️", style=discord.ButtonStyle.secondary, custom_id="cible_next")
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(label="🔄 Relancer une vague", style=discord.ButtonStyle.primary, custom_id="cible_rerun")
    async def btn_rerun(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="<:icon_search:1512505406474293438> *Calcul d'une nouvelle vague de cibles aléatoires en cours...*", embed=None, view=self)
        
        # 🛠️ C'est ici qu'on ajoute la mémorisation du règlement
        await self.cog._execute_cible(
            interaction, 
            self.attaquant, 
            self.tri, 
            self.alliance_cible, 
            message_to_edit=interaction.message, 
            reglement=self.reglement
        )

# ==========================================
# ⚔️ LE COG GUERRE
# ==========================================
class GuerreCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # 🎨 PALETTE GRAPHIQUE DES COGS
        self.clr_scanner   = discord.Color.from_rgb(115, 0, 0)    
        self.clr_proximite = discord.Color.from_rgb(166, 0, 0)    
        self.clr_compare_j = discord.Color.from_rgb(217, 0, 0)    
        self.clr_compare_a = discord.Color.from_rgb(255, 38, 38)  
        self.clr_cible     = discord.Color.from_rgb(255, 92, 92)  
        self.clr_hr        = discord.Color.from_rgb(140, 35, 35)  

    # ==========================================
    # 🔍 COMMANDE : ALLIANCE SCANNER
    # ==========================================
    @app_commands.command(name="alliance_scanner", description="Analyse le roster ennemi en temps réel (Colombes, PP, Cibles)")
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    async def alliance_scanner(self, interaction: discord.Interaction, alliance: str):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        logger.info(f"🔍 [Alliance Scanner] Utilisé par {interaction.user.name} pour : {alliance}")

        cache_data = await get_cached_data()
        local_data = cache_data.get('players_data', {})

        target_id = None
        for p_info in local_data.values():
            a_obj = p_info.get('alliance')
            a_name = a_obj.get('name') if isinstance(a_obj, dict) else (p_info.get('alliance_name') or a_obj)
            
            if a_name and str(a_name).lower() == alliance.lower():
                aid = p_info.get('allianceId') or p_info.get('alliance_id')
                if not aid and isinstance(a_obj, dict):
                    aid = a_obj.get('allianceId') or a_obj.get('alliance_id')
                
                if aid:
                    target_id = str(aid)
                    alliance = str(a_name)
                    break

        if not target_id:
            return await interaction.followup.send(f"<:error:1512505075220611172> Alliance **{alliance}** introuvable dans le cache local.")

        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        url = f"https://api.gge-tracker.com/api/v1/alliances/id/{target_id}"
        
        try:
            # ⚡ Optimisation : Utilisation du pool global unique du bot
            async with self.bot.session.get(url, headers=headers, timeout=10) as r:
                if r.status != 200:
                    return await interaction.followup.send("<:error:1512505075220611172> Erreur de l'API GGE-Tracker (Impossible d'obtenir les données live).")
                data = await r.json()
                if isinstance(data, list) and data: data = data[0]
        except Exception as e:
            return await interaction.followup.send(f"<:error:1512505075220611172> Impossible de joindre l'API : {e}")

        members = data.get("players", data.get("members", data.get("playerList", [])))
        if not members:
            return await interaction.followup.send("<:error:1512505075220611172> L'alliance semble vide ou l'API ne renvoie pas les membres.")

        maintenant = discord.utils.utcnow()
        colombes = []
        cibles_libres = []
        
        for m in members:
            name = m.get('player_name', m.get('playerName', m.get('name', 'Inconnu')))
            pp = int(m.get('might_current', m.get('might', m.get('main_points', 0))))
            peace = m.get('peace_disabled_at')
            
            is_protected = False
            if peace and peace != "null":
                try:
                    dt_peace = datetime.fromisoformat(peace.replace('Z', '+00:00'))
                    if dt_peace > maintenant:
                        is_protected = True
                        colombes.append({"name": name, "pp": pp, "fin": int(dt_peace.timestamp())})
                except: pass
                
            if not is_protected:
                cibles_libres.append({"name": name, "pp": pp})

        cibles_libres.sort(key=lambda x: x["pp"], reverse=True)
        colombes.sort(key=lambda x: x["fin"])

        embeds = []
        chunk_size = 10 
        
        lignes_colombes = [f"<:peace:1512503935892586566> **{c['name']}** ({format_num(c['pp'])} PP) ➔ Fin: <t:{c['fin']}:R>" for c in colombes]
        lignes_cibles = [f"<:cible:1512573711134490775> **{c['name']}** ➔ **{format_num(c['pp'])} PP**" for c in cibles_libres]

        def creer_base_embed(titre_page):
            embed = discord.Embed(title=f"<:icon_search:1512505406474293438> Scanner de Guerre : {alliance}", color=self.clr_scanner, timestamp=maintenant)
            embed.description = f"<:players:1512504277392953426> **Membres Actifs :** {len(members)}\n<:peace:1512503935892586566> **Sous protection :** {len(colombes)}\n<:cible:1512573711134490775> **Cibles vulnérables :** {len(cibles_libres)}\n\n**{titre_page}**"
            setup_embed_footer(embed, interaction)
            return embed

        if lignes_colombes:
            nb_pages_col = max(1, (len(lignes_colombes) - 1) // chunk_size + 1)
            for i in range(0, len(lignes_colombes), chunk_size):
                chunk = lignes_colombes[i:i+chunk_size]
                num_page = (i // chunk_size) + 1
                embed = creer_base_embed(f"<:icon_peace:1512573882010435624> Colombes (Page {num_page}/{nb_pages_col})")
                embed.add_field(name="Prochaines à tomber", value="\n".join(chunk), inline=False)
                embeds.append(embed)

        if lignes_cibles:
            nb_pages_cib = max(1, (len(lignes_cibles) - 1) // chunk_size + 1)
            for i in range(0, len(lignes_cibles), chunk_size):
                chunk = lignes_cibles[i:i+chunk_size]
                num_page = (i // chunk_size) + 1
                embed = creer_base_embed(f"<:squarecastle:1512573757426892911> Cibles Libres (Page {num_page}/{nb_pages_cib})")
                embed.add_field(name="Cibles triées par Puissance", value="\n".join(chunk), inline=False)
                embeds.append(embed)

        if not embeds: return await interaction.followup.send("<:error:1512505075220611172> L'alliance ne contient aucun membre exploitable.")

        if len(embeds) == 1: await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

    # ==========================================
    # 📍 COMMANDE : PROXIMITÉ
    # ==========================================
    @app_commands.command(name="proximite", description="Trouve les châteaux ennemis les plus proches de toi")
    @app_commands.autocomplete(mon_pseudo=joueur_autocomplete)
    @app_commands.autocomplete(alliance_ennemie=alliance_autocomplete)
    async def proximite(self, interaction: discord.Interaction, mon_pseudo: str, alliance_ennemie: str):
        try: await interaction.response.defer(thinking=True)
        except: return

        logger.info(f"<:compass:1512504625364729987> [Proximité] Utilisé par {interaction.user.name} (Moi: {mon_pseudo}, Ennemie: {alliance_ennemie})")

        cache_data = await get_cached_data()
        local_data = cache_data.get('players_data', {})

        target_id = None
        for p_info in local_data.values():
            a_obj = p_info.get('alliance')
            a_name = a_obj.get('name') if isinstance(a_obj, dict) else (p_info.get('alliance_name') or a_obj)
            
            if a_name and str(a_name).lower() == alliance_ennemie.lower():
                aid = p_info.get('allianceId') or p_info.get('alliance_id')
                if not aid and isinstance(a_obj, dict):
                    aid = a_obj.get('allianceId') or a_obj.get('alliance_id')
                
                if aid:
                    target_id = str(aid)
                    alliance_ennemie = str(a_name) 
                    break

        if not target_id:
            return await interaction.followup.send(f"<:error:1512505075220611172> Alliance **{alliance_ennemie}** introuvable dans le cache.")

        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        
        my_x, my_y = None, None
        url_me = f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(mon_pseudo)}"
        try:
            # ⚡ Optimisation : Utilisation du pool de session global
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
            return await interaction.followup.send(f"<:error:1512505075220611172> Impossible de trouver les coordonnées exactes de **{mon_pseudo}**.")

        my_x, my_y = int(my_x), int(my_y)

        url_alli = f"https://api.gge-tracker.com/api/v1/alliances/id/{target_id}"
        try:
            async with self.bot.session.get(url_alli, headers=headers, timeout=10) as r:
                if r.status != 200: return await interaction.followup.send("<:error:1512505075220611172> Erreur de l'API GGE-Tracker (Alliance).")
                data = await r.json()
                if isinstance(data, list) and data: data = data[0]
        except Exception as e:
            return await interaction.followup.send(f"<:error:1512505075220611172> Impossible de joindre l'API : {e}")

        members = data.get("players", data.get("members", data.get("playerList", [])))
        if not members: return await interaction.followup.send("<:error:1512505075220611172> L'alliance ennemie semble vide.")

        async def get_enemy_coords(m):
            p_name = m.get('player_name', m.get('playerName', m.get('name', 'Inconnu')))
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
            return await interaction.followup.send("<:error:1512505075220611172> Impossible de localiser les châteaux de cette alliance sur la carte Principale.")

        embeds = []
        chunk_size = 5 
        nb_pages = max(1, (len(valid_targets) - 1) // chunk_size + 1)
        
        for i in range(0, len(valid_targets), chunk_size):
            chunk = valid_targets[i:i+chunk_size]
            page_num = (i // chunk_size) + 1
            
            embed = discord.Embed(
                title=f"<:cible:1512573711134490775> Cibles de Proximité : {alliance_ennemie}", 
                description=f"🛰️ Ton point de départ : **{mon_pseudo}** (`{my_x}:{my_y}`)\n<:icon_search:1512505406474293438> **{len(valid_targets)}** cibles localisées au total.", 
                color=self.clr_proximite, 
                timestamp=discord.utils.utcnow()
            )
            
            for j, t in enumerate(chunk):
                index_global = i + j + 1
                colombe_txt = "<:peace:1512503935892586566> **SOUS COLOMBE**" if t['protected'] else "<:cible:1512573711134490775> **VULNÉRABLE**"
                embed.add_field(
                    name=f"#{index_global} - {t['name']}", 
                    value=f"<:icon_world:1512517516012814537> Distance : **{int(t['dist'])} lieues**\n<:compass:1512504625364729987> Coords : `{t['x']}:{t['y']}`\n<:pp2:1512571027119538335> Puissance : {format_num(t['pp'])}\n{colombe_txt}", 
                    inline=False
                )

            embed.add_field(name=f"Page {page_num}/{nb_pages}", value="*Tri effectué du plus proche au plus éloigné.*", inline=False)
            setup_embed_footer(embed, interaction)
            embeds.append(embed)

        if len(embeds) == 1: await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

    # ==========================================
    # 🎯 COMMANDE : CIBLE
    # ==========================================
    @app_commands.command(name="cible", description="Trouve des cibles légales selon le règlement choisi")
    @app_commands.autocomplete(attaquant=joueur_autocomplete)
    @app_commands.autocomplete(alliance_cible=alliance_autocomplete)
    @app_commands.describe(
        attaquant="Le pseudo du joueur qui va attaquer",
        reglement="Le set de règles à appliquer pour la recherche",
        alliance_cible="Optionnel : Restreindre la recherche à une alliance spécifique"
    )
    @app_commands.choices(reglement=[
        app_commands.Choice(name="⚖️ Comité des Rois (CDR)", value="cdr"),
        app_commands.Choice(name="⚔️ QG ETERNITY", value="eternity"),
        app_commands.Choice(name="🛡️ SØNS ØF GOD (SOG)", value="sog")
    ])
    async def cible(self, interaction: discord.Interaction, attaquant: str, reglement: str, alliance_cible: str = None):
        try: 
            await interaction.response.defer(thinking=True)
        except: 
            return
        await self._execute_cible(interaction, attaquant, "aleatoire", alliance_cible, reglement=reglement)

    # ==========================================
    # ⚙️ MOTEUR D'EXÉCUTION CENTRALISÉ DU SCAN
    # ==========================================
    async def _execute_cible(self, interaction: discord.Interaction, attaquant: str, tri: str = "aleatoire", alliance_cible: str = None, message_to_edit=None, reglement: str = "cdr"):
        def get_tier(lvl, leg):
            if lvl < 70: return 0
            if leg <= 120: return 1
            if leg <= 360: return 2
            if leg <= 650: return 3
            if leg <= 949: return 4
            return 5

        # 1. Chargement des règles depuis le JSON
        all_rules = await load_reglements_async()
        rules = all_rules.get(reglement, all_rules.get("cdr"))

        # 2. Le Moteur de validation universel
        def is_legal_target(a_pp, a_tier, a_lvl, t_pp, t_tier, t_lvl):
            # 1. Gestion de la Ligue des Grands (> 50M)
            if rules.get("check_api_limit"):
                limit = rules.get("api_limit_threshold", 50000000)
                if a_pp >= limit:
                    if t_pp < limit: return False
                    # Si la règle dit "Au-dessus de 50M il n'y a plus de règles", on valide direct
                    if rules.get("api_limit_no_rules_above"): return True
                elif t_pp >= limit:
                    return False # Un petit ne peut pas taper un grand

            # 2. Détermination de la configuration active (Globale ou par Tranche PP)
            config = rules
            if rules.get("tranches_pp"):
                matched = False
                for tranche in rules["tranches_pp"]:
                    if tranche.get("a_min", 0) <= a_pp <= tranche.get("a_max", 999999999):
                        config = tranche
                        matched = True
                        break
                if not matched: return False

            # 3. Application des filtres de Paliers (Tiers)
            if not config.get("ignore_tiers", rules.get("ignore_tiers", False)):
                diff_tier = t_tier - a_tier
                allowed_tiers = config.get("allowed_tiers_relative", rules.get("allowed_tiers_relative", [0]))
                if diff_tier not in allowed_tiers: return False

                # Règle spéciale Lvl 1 à 69 (+/- 10 lvl)
                if a_tier == 0 and t_tier == 0:
                    max_lvl_diff = config.get("tier_0_max_lvl_diff", rules.get("tier_0_max_lvl_diff", 10))
                    if abs(a_lvl - t_lvl) > max_lvl_diff: return False

            # 4. Application des filtres de Puissance (PP)
            # A) Limites absolues (ex: SOG, la cible doit avoir entre 15M et 50M)
            if "t_min" in config and t_pp < config["t_min"]: return False
            if "t_max" in config and t_pp > config["t_max"]: return False

            # B) Limites relatives (ex: CDR/ETERNITY, la cible a droit à +/- 10M par rapport à l'attaquant)
            if "pp_offset_min" in config and t_pp < (a_pp + config["pp_offset_min"]): return False
            if "pp_offset_max" in config and t_pp > (a_pp + config["pp_offset_max"]): return False

            return True

        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        cache_data = await get_cached_data()
        local_data = cache_data.get('players_data', {})
        session_active = self.bot.session

        a_info, a_name_real = None, attaquant
        a_coords = {"x": None, "y": None}
        
        try:
            search_url = f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(attaquant)}"
            async with session_active.get(search_url, headers=headers, timeout=10) as r:
                if r.status == 200:
                    p_data = await r.json()
                    if isinstance(p_data, list) and p_data: p_data = p_data[0]
                    if isinstance(p_data, dict):
                        a_info = p_data
                        a_name_real = a_info.get("playerName", a_info.get("name", a_info.get("player_name", attaquant)))
            
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
                if name.lower() == attaquant.lower():
                    a_info, a_name_real = info, name
                    break

        if not a_info:
            err_msg = f"<:error:1512505075220611172> Attaquant **{attaquant}** introuvable."
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

        a_alli_clean = "".join(c for c in a_alliance.lower() if c.isalnum()) if a_alliance and a_alliance != "Sans alliance" else ""

        allies_et_pna_clean = []
        try:
            if a_alliance and a_alliance != "Sans alliance":
                diplo_data = await load_diplo_async()
                for key, diplo in diplo_data.items():
                    if key.lower() == a_alliance.lower() and diplo.get("guild_id") == interaction.guild_id:
                        allies_et_pna_clean = ["".join(c for c in str(a).lower() if c.isalnum()) for a in diplo.get("allies", []) + diplo.get("pna", [])]
                        break
        except: pass

        mots_interdits_mur, alliances_mur_alerte = ["repos", "deuil", "hospitalisé"], []
        try:
            fichier_murs = BASE_DATA_PATH / 'murs_scans' / 'murs_alliances.json'
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
                
            if not t_alliance or t_alliance == "Sans alliance": continue
            
            alli_clean = "".join(c for c in t_alliance.lower() if c.isalnum())

            if a_alli_clean and alli_clean == a_alli_clean: continue
            if alliance_cible and t_alliance.lower() != alliance_cible.lower(): continue
            if alli_clean in allies_et_pna_clean: continue

            t_lvl = int(t_info.get('level', 0))
            t_leg = int(t_info.get('legendary_level', 0))
            t_pp = int(t_info.get('main_points', 0))
            t_tier = get_tier(t_lvl, t_leg)

            # Application de la validation dynamique
            if not is_legal_target(a_pp, a_tier, a_lvl, t_pp, t_tier, t_lvl): continue

            has_wall_warning = (alli_clean in alliances_mur_alerte or any(mot in str(t_alliance).lower() for mot in mots_interdits_mur))
            
            pool_candidats.append({
                "name": t_name, "alliance": str(t_alliance), "lvl": t_lvl, "leg": t_leg,
                "tier": t_tier, "pp": t_pp, "dist": 9999, "x": "???", "y": "???",
                "is_upper_tier": (t_tier > a_tier), "wall_warning": has_wall_warning, "is_ghost": False,
                "peace_disabled_at": t_info.get('peace_disabled_at', "null") 
            })

        if not pool_candidats:
            nom_regle = {"cdr": "Règles CDR", "guerre": "Mode Guerre", "defensif": "Règles Défensives"}.get(reglement, "Inconnue")
            no_targets_msg = f"<:error:1512505075220611172> Aucune cible trouvée respectant les critères (**{nom_regle}**) dans la base de données."
            if message_to_edit: await message_to_edit.edit(content=no_targets_msg, view=None)
            else: await interaction.followup.send(no_targets_msg)
            return

        random.shuffle(pool_candidats)
        final_targets = []
        chunk_size_api = 5
        
        for k in range(0, len(pool_candidats), chunk_size_api):
            if len(final_targets) >= 10: break
            chunk_candidats = pool_candidats[k:k+chunk_size_api]

            async def fetch_live_target(t):
                try:
                    async with session_active.get(f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(t['name'])}", headers=headers, timeout=10) as r:
                        if r.status == 200:
                            d = await r.json()
                            if isinstance(d, list) and d: d = d[0]
                            if isinstance(d, dict):
                                t['lvl'] = int(d.get('level', t['lvl']))
                                t['leg'] = int(d.get('legendary_level', d.get('legendaryLevel', t['leg'])))
                                t['pp'] = int(d.get('might_current', d.get('might', t['pp'])))
                                t['tier'] = get_tier(t['lvl'], t['leg'])
                                t['is_upper_tier'] = (t['tier'] > a_tier)
                                t['peace_disabled_at'] = d.get('peace_disabled_at', "null")
                            else: t['is_ghost'] = True
                        elif r.status == 429: await asyncio.sleep(1.5)
                except: pass
                
                if not t['is_ghost']:
                    try:
                        async with session_active.get(f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(t['name'])}", headers=headers, timeout=10) as r:
                            if r.status == 200:
                                c_data = await r.json()
                                if isinstance(c_data, dict): c_data = [c_data]
                                if isinstance(c_data, list):
                                    for c in c_data:
                                        if str(c.get('kingdomId', c.get('kingdom_id', 'X'))) == "0" and str(c.get('type', c.get('castle_type', 'X'))) == "1":
                                            tx = c.get('positionX') or c.get('position_x') or c.get('x')
                                            ty = c.get('positionY') or c.get('position_y') or c.get('y')
                                            if tx is not None and ty is not None: 
                                                t['x'], t['y'] = str(tx), str(ty)
                                                if a_coords['x'] is not None and a_coords['y'] is not None:
                                                    t['dist'] = math.sqrt((float(tx) - a_coords['x'])**2 + (float(ty) - a_coords['y'])**2)
                                                break
                            elif r.status == 429: await asyncio.sleep(1.5)
                    except: pass

            await asyncio.gather(*(fetch_live_target(t) for t in chunk_candidats))
            
            for t in chunk_candidats:
                if t['is_ghost'] or t['x'] == "???": continue
                
                if is_legal_target(a_pp, a_tier, a_lvl, t['pp'], t['tier'], t['lvl']):
                    
                    # Vérification dynamique de la distance
                    min_dist = rules.get("min_distance", 0)
                    if t['dist'] < min_dist: 
                        continue
                        
                    final_targets.append(t)
            
            await asyncio.sleep(0.15)

        if not final_targets:
            nom_regle = {"cdr": "Règles CDR", "eternity": "Règlement du QG Eternity", "sog": "Règles SØNS ØF GOD"}.get(reglement, "Inconnue")
            empty_msg = f"<:error:1512505075220611172> Les cibles potentielles ne respectent plus les règles (**{nom_regle}**) avec leurs puissances actuelles ou sont hors-ligne."
            if message_to_edit: await message_to_edit.edit(content=empty_msg, view=None)
            else: await interaction.followup.send(empty_msg)
            return

        # --- RÉCUPÉRATION DES RÈGLES D'AFFICHAGE ---
        active_affichage = rules.get("affichage", {})
        if rules.get("tranches_pp"):
            for tranche in rules["tranches_pp"]:
                if tranche.get("a_min", 0) <= a_pp <= tranche.get("a_max", 999999999):
                    active_affichage = tranche.get("affichage", active_affichage)
                    break
        
        # Valeurs par défaut si le JSON est incomplet
        txt_feu = active_affichage.get("feu_min_soldats", "Non défini")
        txt_ap = active_affichage.get("ap_regle", "Non défini")
        txt_attente = active_affichage.get("cooldown", "Non défini")
        txt_max_att = active_affichage.get("max_attaques", "Non défini")
        txt_interdit = active_affichage.get("interdictions", "Aucune spécifique")

        best_targets = final_targets[:10]
        embeds = []
        chunk_size = 5 
        nb_pages = max(1, (len(best_targets) - 1) // chunk_size + 1)
        titre_alliance = f" (Alliance : {alliance_cible})" if alliance_cible else ""
        nom_regle_titre = {"cdr": "Règles CDR (Comité des Rois)", "eternity": "Règlement du QG Eternity", "sog": "Règles SØNS ØF GOD"}.get(reglement, "")
        
        for i in range(0, len(best_targets), chunk_size):
            chunk = best_targets[i:i+chunk_size]
            page_num = (i // chunk_size) + 1
            
            embed = discord.Embed(
                title=f"<:attaque:1512570903886692474> Cibles {nom_regle_titre} pour {a_name_real}{titre_alliance}", 
                description=f"<:pp2:1512571027119538335> Ta Puissance : **{format_num(a_pp)}** | <:lvl:1512571152524906596> Ton Palier : **{a_tier}**\n<:icon_search:1512505406474293438> **{len(final_targets)} cibles valides détectées**.\n\n━━━━━━━━━━━━━━━━━━━━━━━",
                color=self.clr_cible,
                timestamp=discord.utils.utcnow()
            )

            # 1. On affiche UNIQUEMENT les infos des joueurs
            for j, t in enumerate(chunk):
                index_global = i + j + 1
                
                dist_str = f"{int(t['dist'])} lieues"
                diff_pp = t['pp'] - a_pp
                diff_txt = f"(+{format_num(diff_pp)} PP)" if diff_pp > 0 else f"({format_num(diff_pp)} PP)"
                
                is_under_colombe = False
                if t.get('peace_disabled_at') and t['peace_disabled_at'] != "null":
                    try:
                        dt_peace = datetime.fromisoformat(t['peace_disabled_at'].replace('Z', '+00:00'))
                        if dt_peace > discord.utils.utcnow():
                            is_under_colombe = True
                    except: pass

                target_icon = "<:peace:1512503935892586566>" if is_under_colombe else "<:players:1512504277392953426>"

                warnings = []
                if t['wall_warning']: warnings.append("\n<:error:1512505075220611172> **VÉRIFIEZ LE MUR :** Description d'alliance sensible !")
                if t['is_upper_tier'] and not rules.get("ignore_tiers"): warnings.append("\n<:error:1512505075220611172> **RISQUE DE REPRESAILLES :** Joueur du palier supérieur !")
                if is_under_colombe: warnings.append("\n<:peace:1512503935892586566> **JOUEUR SOUS COLOMBE : Protection active (Inattaquable) !**")
                
                warning_txt = "".join(warnings) if warnings else ""
                
                description_cible = (
                    f"<:icon_alliance:1512573872774451210> Alliance : **{t['alliance']}**\n"
                    f"<:lvl:1512571152524906596> Niveau : {t['lvl']}/{t['leg']} (Palier {t['tier']})\n"
                    f"<:pp1:1512438903821570160> Puissance : {format_num(t['pp'])} {diff_txt}\n"
                    f"<:map:1512573907788501242> Distance : **{dist_str}**\n"
                    f"<:coords:1512574624112578580> Coordonnées : `{t['x']}:{t['y']}`\n"
                    f"{warning_txt}\n━━━━━━━━━━━━━━━━━━━━━━━"
                )
                
                embed.add_field(
                    name=f"{target_icon} Cible #{index_global} : {t['name']}", 
                    value=description_cible, 
                    inline=False
                )

            # 2. On affiche le règlement UNE SEULE FOIS en bas
            reglement_texte = (
                f"⚔️ Limite d'attaque : **{txt_max_att}** | ⏳ Attente : **{txt_attente}**\n"
                f"<:fire:1512573853774254303> Si feu : **{txt_feu}** | <:avp:1512572561647468555> Règle AVP : **{txt_ap}**\n"
                f"🚫 Interdictions : *{txt_interdit}*"
            )
            embed.add_field(name="<:members:1512573912305766652> Règlement en vigueur\n\n", value=reglement_texte, inline=False)

            # 3. Le footer habituel
            embed.add_field(name=f"Page {page_num}/{nb_pages}", value="<:icon_name:1512505444172697611> **SPY OBLIGATOIRE** avant impact.", inline=False)
            
            setup_embed_footer(embed, interaction)
            embeds.append(embed)

        view = CiblePaginationView(self, attaquant, tri, alliance_cible, embeds, reglement)
        
        if message_to_edit:
            await message_to_edit.edit(content=None, embed=embeds[0], view=view)
        else:
            await interaction.followup.send(embed=embeds[0], view=view)

    # ==========================================
    # ⚖️ COMMANDE : HR
    # ==========================================
    @app_commands.command(name="hr", description="Vérifie si une attaque entre deux joueurs respecte les règles choisies")
    @app_commands.autocomplete(attaquant=joueur_autocomplete)
    @app_commands.autocomplete(defenseur=joueur_autocomplete)
    @app_commands.describe(
        attaquant="Le pseudo de l'attaquant",
        defenseur="Le pseudo du défenseur",
        reglement="Le set de règles à appliquer pour l'arbitrage"
    )
    @app_commands.choices(reglement=[
        app_commands.Choice(name="⚖️ Comité des Rois (CDR)", value="cdr"),
        app_commands.Choice(name="⚔️ QG ETERNITY", value="eternity"),
        app_commands.Choice(name="🛡️ SØNS ØF GOD", value="sog")
    ])
    async def hr(self, interaction: discord.Interaction, attaquant: str, defenseur: str, reglement: str):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        logger.info(f"⚖️ [HR] Arbitrage utilisé par {interaction.user.name} ({attaquant} vs {defenseur} - Règle: {reglement})")

        if attaquant.lower() == defenseur.lower(): 
            return await interaction.followup.send("<:error:1512505075220611172> Tu ne peux pas t'attaquer toi-même, voyons ! 😂")

        # 1. Chargement du règlement
        all_rules = await load_reglements_async()
        rules = all_rules.get(reglement, all_rules.get("cdr"))
        nom_regle_titre = rules.get("nom", reglement.upper())

        cache_data = await get_cached_data()
        local_data = cache_data.get('players_data', {})

        def get_tier(lvl, leg):
            if lvl < 70: return 0
            if leg <= 120: return 1
            if leg <= 360: return 2
            if leg <= 650: return 3
            if leg <= 949: return 4
            return 5

        a_info, d_info, a_name, d_name = None, None, attaquant, defenseur
        for name, info in local_data.items():
            if name.lower() == attaquant.lower(): a_info, a_name = info, name
            if name.lower() == defenseur.lower(): d_info, d_name = info, name

        if not a_info: return await interaction.followup.send(f"<:error:1512505075220611172> L'attaquant **{attaquant}** est introuvable.")
        if not d_info: return await interaction.followup.send(f"<:error:1512505075220611172> Le défenseur **{defenseur}** est introuvable.")

        a_lvl, a_leg, a_pp = int(a_info.get('level', 0)), int(a_info.get('legendary_level', 0)), int(a_info.get('main_points', 0))
        d_lvl, d_leg, d_pp = int(d_info.get('level', 0)), int(d_info.get('legendary_level', 0)), int(d_info.get('main_points', 0))
        a_tier, d_tier = get_tier(a_lvl, a_leg), get_tier(d_lvl, d_leg)
        
        a_alli = a_info.get('alliance') or a_info.get('alliance_name')
        if isinstance(a_alli, dict): a_alli = a_alli.get('name')
        d_alli = d_info.get('alliance') or d_info.get('alliance_name')
        if isinstance(d_alli, dict): d_alli = d_alli.get('name')

        a_coords, d_coords, headers = None, None, {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        
        for p_name, is_atk in [(a_name, True), (d_name, False)]:
            try:
                async with self.bot.session.get(f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(p_name)}", headers=headers, timeout=5) as r:
                    if r.status == 200:
                        c_data = await r.json()
                        if isinstance(c_data, dict): c_data = [c_data]
                        if isinstance(c_data, list):
                            for c in c_data:
                                if str(c.get('kingdomId', c.get('kingdom_id', 'X'))) == "0" and str(c.get('type', c.get('castle_type', 'X'))) == "1":
                                    x, y = c.get('positionX') or c.get('position_x') or c.get('x'), c.get('positionY') or c.get('position_y') or c.get('y')
                                    if is_atk: a_coords = (int(x), int(y))
                                    else: d_coords = (int(x), int(y))
                                    break
            except: pass

        distance = math.hypot(d_coords[0] - a_coords[0], d_coords[1] - a_coords[1]) if a_coords and d_coords else None
        
        infractions = []
        avertissements = []
        allies_propres, pna_propres, diplo_privee = [], [], False
        
        # --- VÉRIFICATIONS DIPLOMATIQUES (Universelles) ---
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
            if d_alli_clean in allies_propres: infractions.append(f"<:4_:1512574743369224303> **Diplomatie** : Le défenseur est dans une alliance ALLIÉE (*{d_alli}*).")
            elif d_alli_clean in pna_propres: infractions.append(f"<:4_:1512574743369224303> **Diplomatie** : Le défenseur est dans une alliance PNA (*{d_alli}*).")
            
            try:
                fichier_murs = BASE_DATA_PATH / 'murs_scans' / 'murs_alliances.json'
                if fichier_murs.exists():
                    with open(fichier_murs, 'r', encoding='utf-8') as f:
                        for nom_json, desc in json.load(f).items():
                            if "".join(c for c in str(nom_json).lower() if c.isalnum()) == d_alli_clean:
                                desc_mur = str(desc).lower()
                                mot_trouve = next((mot for mot in ["repos", "deuil", "hospitalisé"] if mot in desc_mur), None)
                                if mot_trouve: avertissements.append(f"<:alliances:1512574688415580242> **Mur d'alliance** : Mot-clé sensible détecté (**{mot_trouve.capitalize()}**).")
                                break
            except: pass

        # --- DÉTERMINATION DE LA TRANCHE PP ACTIVE ---
        config = rules
        if rules.get("tranches_pp"):
            for tranche in rules["tranches_pp"]:
                if tranche.get("a_min", 0) <= a_pp <= tranche.get("a_max", 999999999):
                    config = tranche
                    break

        skip_other_checks = False

        # --- VÉRIFICATIONS DYNAMIQUES BASÉES SUR JSON ---
        
        # 1. Limite de Ligue (> 50M)
        if rules.get("check_api_limit"):
            limit = rules.get("api_limit_threshold", 50000000)
            if a_pp >= limit:
                if d_pp < limit: 
                    infractions.append(f"<:pp2:1512571027119538335> **Ligue croisée** : L'attaquant (> {format_num(limit)}) ne peut pas cibler un joueur sous le seuil.")
                elif rules.get("api_limit_no_rules_above"):
                    skip_other_checks = True # Les deux sont >50M et la règle annule le reste
            else:
                if d_pp >= limit: 
                    avertissements.append(f"<:pp2:1512571027119538335> **Mission Suicide** : Tu attaques un joueur au-dessus du seuil ({format_num(limit)}). Risque fort de représailles.")

        # 2. Distance
        min_dist = config.get("min_distance", rules.get("min_distance", 0))
        if distance is not None and distance < min_dist: 
            infractions.append(f"<:icon_search:1512505406474293438> **Distance** : Cible trop proche ! Distance : **{int(distance)} lieues** (Règlement exige Min: {min_dist}).")

        # 3. Écarts de Puissance (PP) et Paliers
        if not skip_other_checks:
            
            # Limites PP Absolues (ex: SOG)
            if "t_min" in config and d_pp < config["t_min"]:
                infractions.append(f"<:pp1:1512438903821570160> **Écart de Puissance** : La cible a trop peu de PP (Min exigé par ta tranche: {format_num(config['t_min'])}).")
            if "t_max" in config and d_pp > config["t_max"]:
                avertissements.append(f"<:pp1:1512438903821570160> **Puissance élevée** : La cible dépasse la limite de ta tranche (Max conseillé: {format_num(config['t_max'])}).")

            # Limites PP Relatives/Offsets (ex: CDR / Eternity)
            if "pp_offset_min" in config and d_pp < (a_pp + config["pp_offset_min"]):
                infractions.append(f"<:pp1:1512438903821570160> **Écart de Puissance** : Tu as {format_num(a_pp - d_pp)} PP de plus (L'écart max autorisé vers le bas est de {format_num(abs(config['pp_offset_min']))}).")
            if "pp_offset_max" in config and d_pp > (a_pp + config["pp_offset_max"]):
                avertissements.append(f"<:pp1:1512438903821570160> **Défenseur plus fort** : Le défenseur a {format_num(d_pp - a_pp)} PP de plus que toi. Prudence.")

            # Paliers (Tiers)
            if not config.get("ignore_tiers", rules.get("ignore_tiers", False)):
                diff_tier = d_tier - a_tier
                allowed_tiers = config.get("allowed_tiers_relative", rules.get("allowed_tiers_relative", [0]))
                
                if diff_tier < min(allowed_tiers):
                    infractions.append(f"<:lvl:1512571152524906596> **Écart de Palier** : Tu (Palier {a_tier}) n'as pas le droit d'attaquer un joueur de Palier inférieur ({d_tier}).")
                elif diff_tier > max(allowed_tiers):
                    avertissements.append(f"<:lvl:1512571152524906596> **Niveau élevé** : Tu attaques un Palier supérieur ({d_tier}). Risque de représailles.")

                # Règle Lvl 1-69 spéciale
                if a_tier == 0 and d_tier == 0:
                    max_lvl_diff = config.get("tier_0_max_lvl_diff", rules.get("tier_0_max_lvl_diff", 10))
                    if a_lvl > d_lvl + max_lvl_diff:
                        infractions.append(f"<:lvl:1512571152524906596> **Niveaux <70** : Tu as {a_lvl - d_lvl} niveaux de plus (Max autorisé: +{max_lvl_diff}).")
                    elif a_lvl < d_lvl - max_lvl_diff:
                        avertissements.append(f"<:lvl:1512571152524906596> **Défenseur plus fort** : La cible a {d_lvl - a_lvl} niveaux de plus que toi.")

        # --- GESTION DE L'AFFICHAGE TEXTUEL ---
        affichage = config.get("affichage", rules.get("affichage", {}))
        txt_ap = affichage.get("ap_regle", "Non défini")
        min_troops = affichage.get("feu_min_soldats", "Non défini")
        txt_max_att = affichage.get("max_attaques", "Non défini")
        
        dist_txt = f"{int(distance)} lieues" if distance else "<:error:1512505075220611172> Inconnue"

        # Calcul propre de la différence de PP
        diff_pp = d_pp - a_pp
        if diff_pp > 0:
            diff_txt = f"+{format_num(diff_pp)} PP (Défenseur plus fort)"
        elif diff_pp < 0:
            diff_txt = f"-{format_num(abs(diff_pp))} PP (Défenseur plus faible)"
        else:
            diff_txt = "Égalité stricte"

        embed = discord.Embed(title=f"<:4_:1512574743369224303> Arbitrage {nom_regle_titre} : {a_name} 🆚 {d_name}", color=self.clr_hr, timestamp=discord.utils.utcnow())
        
        # Ligne 1 : Les deux profils côte à côte
        embed.add_field(name=f"⚔️ Attaquant : {a_name}", value=f"<:icon_alliance:1512573872774451210> {a_alli or 'Sans alliance'}\n<:lvl:1512571152524906596> Lvl {a_lvl}/{a_leg} (Palier {a_tier})\n<:pp2:1512571027119538335> {format_num(a_pp)} PP", inline=True)
        embed.add_field(name=f"🛡️ Défenseur : {d_name}", value=f"<:icon_alliance:1512573872774451210> {d_alli or 'Sans alliance'}\n<:lvl:1512571152524906596> Lvl {d_lvl}/{d_leg} (Palier {d_tier})\n<:pp2:1512571027119538335> {format_num(d_pp)} PP", inline=True)
        
        # Ligne 2 : Les calculs spatiaux et de puissance (Nouveau bloc)
        embed.add_field(name="📊 Données entre les joueurs", value=f"<:compass:1512504625364729987> Distance : **{dist_txt}**\n<:pp2:1512571027119538335> Différence : **{diff_txt}**", inline=False)
        
        # Ligne 3 : Les conditions pures issues du règlement
        embed.add_field(name="<:667420141394329610:1512573711134490775> Conditions de l'attaque", value=f"<:Porteurs_de_bouclier:1512574622271279114> Limite d'attaques : **{txt_max_att}**\n<:castle4:1512573820752498839> Cibles AP : **{txt_ap}**\n<:troop:1512573768893989015> Min. Soldats (Si feu) : **{min_troops}**", inline=False)
        
        if diplo_privee: embed.add_field(name="<:4_:1512574743369224303> Sécurité Diplomatique", value="La diplomatie de l'attaquant est gérée par un autre serveur. Liens d'alliance ignorés.", inline=False)
        setup_embed_footer(embed, interaction)

        # Rendu final (Infraction = Bloquant / Avertissement = Prudence / Vert = OK)
        if infractions:
            embed.color = discord.Color.red()
            embed.add_field(name="❌ HORS RÈGLES (HR)", value="__L'attaque est formellement interdite selon le règlement :__\n\n" + "\n".join([f"• {i}" for i in infractions]), inline=False)
            if avertissements:
                embed.add_field(name="⚠️ Autres observations", value="\n".join([f"• {a}" for a in avertissements]), inline=False)
            await interaction.followup.send(embed=embed)

        elif avertissements:
            embed.color = discord.Color.orange()
            embed.add_field(name="⚠️ ATTAQUE EN RÈGLES (Mais Risquée)", value="__L'attaque respecte les règles, mais attention :__\n\n" + "\n".join([f"• {a}" for a in avertissements]), inline=False)
            await interaction.followup.send(embed=embed)

        else:
            embed.color = discord.Color.green()
            embed.add_field(name="✅ ATTAQUE EN RÈGLES", value="Aucune infraction ni avertissement détecté selon ce traité.", inline=False)
            await interaction.followup.send(embed=embed)

    # ========================================================
    # 🥊 COMMANDE : COMPARE_JOUEUR
    # ========================================================
    @app_commands.command(name="compare_joueur", description="Analyse comparative responsive et calcul de l'indice de dangerosité")
    @app_commands.autocomplete(joueur1=joueur_autocomplete)
    @app_commands.autocomplete(joueur2=joueur_autocomplete)
    async def compare_joueur(self, interaction: discord.Interaction, joueur1: str, joueur2: str):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        logger.info(f"🥊 [Compare Joueur] Traitement Précision lancé par {interaction.user.name} ({joueur1} vs {joueur2})")

        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        session = self.bot.session

        async def scruter_profil_tactique(nom_joueur: str):
            profil = {
                "nom": nom_joueur, "id": None, "lvl": 0, "leg": 0, 
                "alliance": "Sans", "alli_might": 0, "alli_id": 0,
                "pp": 0, "gloire": 0, "butin": 0, "rang": 9999, 
                "feux_count": 0, "feux_txt": "0 feu",
                "colombe_txt": "Libre", "malus_colombe": 0, "malus_feu": 0,
                "event_averages": {"nomades": 0, "samourais": 0, "corbeaux": 0, "etrangers": 0},
                "valide": False
            }
            
            try:
                base_player_url = "https://api.gge-tracker.com/api/v1/players/"
                async with session.get(f"{base_player_url}{urllib.parse.quote(nom_joueur)}", headers=headers, timeout=8) as r:
                    if r.status != 200: return profil
                    res_base = await r.json()
                    
                    if isinstance(res_base, list) and res_base: res_base = res_base[0]
                    if not res_base: return profil
                    
                    raw_id = str(res_base.get("player_id", res_base.get("id", "")))
                    profil["id"] = raw_id + '164' if raw_id and not raw_id.endswith('164') else raw_id
                    profil["nom"] = res_base.get("player_name", nom_joueur)
                    profil["lvl"] = int(res_base.get("level", 0))
                    profil["leg"] = int(res_base.get("legendary_level", 0))
                    
                    all_name_raw = res_base.get("alliance_name")
                    profil["alliance"] = all_name_raw if all_name_raw else "Sans alliance"
                    profil["alli_id"] = res_base.get("allianceId", res_base.get("alliance_id", 0))
                    profil["valide"] = True

                    peace = res_base.get("peace_disabled_at")
                    if peace and peace != "null":
                        try:
                            if datetime.fromisoformat(peace.replace('Z', '+00:00')) > discord.utils.utcnow():
                                profil["colombe_txt"] = "Protégé"
                                profil["malus_colombe"] = -1.0
                        except: pass

                if not profil["id"]: return profil

                rank_endpoint = f"https://api.gge-tracker.com/api/v1/statistics/ranking/player/{profil['id']}"
                stats_endpoint = f"https://api.gge-tracker.com/api/v1/statistics/player/{profil['id']}"
                search_endpoint = f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(profil['nom'])}"

                # 🛠️ Corrigé : Remplacement de l'appel get_event_loop déprécié par asyncio.create_task natif
                res_rank, res_stats, res_castles = await asyncio.gather(
                    *[
                        asyncio.create_task(session.get(rank_endpoint, headers=headers, timeout=6)),
                        asyncio.create_task(session.get(stats_endpoint, headers=headers, timeout=6)),
                        asyncio.create_task(session.get(search_endpoint, headers=headers, timeout=6))
                    ]
                )

                if res_rank.status == 200:
                    dr = await res_rank.json()
                    profil["pp"] = int(dr.get("might_current", dr.get("might", 0)))
                    profil["gloire"] = int(dr.get("current_fame", dr.get("fame", 0)))
                    profil["butin"] = int(dr.get("loot_current", dr.get("loot", 0)))
                    profil["rang"] = int(dr.get("server_rank", 9999))

                if res_stats.status == 200:
                    ds = await res_stats.json()
                    pts_dict = ds.get("points", {})
                    
                    piliers_mapping = {
                        "nomades": "player_event_nomad_history",
                        "samourais": "player_event_samurai_history",
                        "corbeaux": "player_event_bloodcrow_history",
                        "etrangers": "player_event_war_realms_history"
                    }
                    
                    for pilier_nom, cle_api in piliers_mapping.items():
                        ev_list = pts_dict.get(cle_api, [])
                        if isinstance(ev_list, list) and ev_list:
                            
                            # 1. Nettoyage et tri chronologique exact des données
                            valid_entries = []
                            for e in ev_list:
                                d_str = e.get("date")
                                pt_str = str(e.get("point", ""))
                                if d_str and pt_str.replace("-", "").isdigit():
                                    try:
                                        dt = datetime.fromisoformat(d_str.replace('Z', '+00:00'))
                                        valid_entries.append((dt, int(pt_str)))
                                    except: pass
                            
                            if not valid_entries:
                                continue
                                
                            valid_entries.sort(key=lambda x: x[0])
                            
                            # 2. Regroupement par "édition" (Séparation des événements)
                            event_max_scores = []
                            current_max = valid_entries[0][1]
                            last_dt = valid_entries[0][0]

                            for dt, pt in valid_entries[1:]:
                                # Si un trou de plus de 4 jours (96h) sépare 2 points, c'est une NOUVELLE édition
                                if (dt - last_dt).total_seconds() > 96 * 3600:
                                    event_max_scores.append(current_max)
                                    current_max = pt
                                else:
                                    # On garde toujours le meilleur score de l'événement en cours
                                    if pt > current_max: current_max = pt
                                last_dt = dt
                            
                            event_max_scores.append(current_max) # On n'oublie pas d'ajouter le tout dernier évent

                            # 3. Calcul de la moyenne des 3 dernières éditions réelles
                            derniers_events = event_max_scores[-3:]
                            if derniers_events:
                                profil["event_averages"][pilier_nom] = sum(derniers_events) // len(derniers_events)

                if res_castles.status == 200:
                    dc = await res_castles.json()
                    if isinstance(dc, dict): dc = [dc]
                    if isinstance(dc, list):
                        c_id = None
                        for c in dc:
                            if not isinstance(c, dict): continue
                            if str(c.get('kingdomId', '0')) == "0" and str(c.get('type', '1')) == "1":
                                c_id = c.get('id')
                                break
                        
                        if c_id:
                            analysis_url = f"https://api.gge-tracker.com/api/v1/castle/analysis/{c_id}"
                            async with session.get(analysis_url, headers=headers, timeout=5) as a_resp:
                                if a_resp.status == 200:
                                    castle_data = (await a_resp.json()).get("data", {})
                                    all_elements = []
                                    for category_list in castle_data.values():
                                        if isinstance(category_list, list):
                                            all_elements.extend(category_list)
                                            
                                    f_count = sum(1 for b in all_elements if b.get("damageFactor") == 1 or str(b.get("damageFactor")).startswith("1"))
                                    profil["feux_count"] = f_count
                                    profil["feux_txt"] = f"{f_count} feux"
                                    if f_count > 0: profil["malus_feu"] = -1.0

                if all_name_raw and all_name_raw != "Sans alliance":
                    try:
                        alli_url = f"https://api.gge-tracker.com/api/v1/alliances/name/{urllib.parse.quote(all_name_raw)}"
                        async with session.get(alli_url, headers=headers, timeout=5) as ar:
                            if ar.status == 200:
                                da = await ar.json()
                                if isinstance(da, list) and da: da = da[0]
                                profil["alli_might"] = int(da.get("might_current", da.get("total_might", 0)))
                    except: pass

            except Exception as e:
                logger.error(f"Erreur sur {nom_joueur} : {e}")
            return profil

        p1, p2 = await asyncio.gather(scruter_profil_tactique(joueur1), scruter_profil_tactique(joueur2))

        if not p1["valide"] or not p2["valide"]: 
            return await interaction.followup.send("<:error:1512505075220611172> Impossible de charger l'un des profils.")

        def duel(v1, v2, inverse=False):
            if v1 == v2: return "", ""
            cond = (v1 < v2) if inverse else (v1 > v2)
            return ("▲", "") if cond else ("", "▲")

        lvl_1, lvl_2 = duel((p1["lvl"], p1["leg"]), (p2["lvl"], p2["leg"]))
        rnk_1, rnk_2 = duel(p1["rang"], p2["rang"], inverse=True)
        pp_1, pp_2 = duel(p1["pp"], p2["pp"])
        glr_1, glr_2 = duel(p1["gloire"], p2["gloire"])
        btn_1, btn_2 = duel(p1["butin"], p2["butin"])
        am_1, am_2 = duel(p1["alli_might"], p2["alli_might"])

        nom_1, nom_2 = duel(p1["event_averages"]["nomades"], p2["event_averages"]["nomades"])
        sam_1, sam_2 = duel(p1["event_averages"]["samourais"], p2["event_averages"]["samourais"])
        cor_1, cor_2 = duel(p1["event_averages"]["corbeaux"], p2["event_averages"]["corbeaux"])
        et_1, et_2 = duel(p1["event_averages"]["etrangers"], p2["event_averages"]["etrangers"])

        score1 = sum([1.0 for x in [lvl_1, am_1, rnk_1, pp_1, glr_1, btn_1] if x]) + sum([0.25 for x in [nom_1, sam_1, cor_1, et_1] if x]) + p1["malus_colombe"] + p1["malus_feu"]
        score2 = sum([1.0 for x in [lvl_2, am_2, rnk_2, pp_2, glr_2, btn_2] if x]) + sum([0.25 for x in [nom_2, sam_2, cor_2, et_2] if x]) + p2["malus_colombe"] + p2["malus_feu"]
        score1, score2 = max(0.0, score1), max(0.0, score2)

        desc_calcul = (
            f"📊 **Comparaison sur plusieurs points**\n\n"
            f"⚙️ **Méthode de calcul de l'Indice :**\n"
            f"• Métriques Militaires/Solo : `+1.0 pt` par supériorité brute.\n"
            f"• Piliers Événementiels : `+0.25 pt` par moyenne glissante (3 éd.) supérieure.\n"
            f"• États de Robustesse : `-1.0 pt` fixe par handicap actif (Feux ou Colombe).\n\n"
            f"<:ranking:1512438311132729525> **Résultat :**\n"
            f"🔵 **{p1['nom']}** (`{score1}🏆`) 🆚 🔴 **{p2['nom']}** (`{score2}🏆`)"
        )

        embed = discord.Embed(
            title="<:icon_analyze:1512573874150314005> Analyse : Grille de Confrontation",
            description=desc_calcul,
            color=self.clr_compare_j,
            timestamp=discord.utils.utcnow()
        )

        def build_row(label, v1, w1, v2, w2):
            str1 = f"{v1} {w1}".strip()
            str2 = f"{v2} {w2}".strip()
            return f"{label:<12} │ {str1:<12} │ {str2}"

        p1_all, p2_all = p1['alliance'][:10], p2['alliance'][:10]
        p1_lvl, p2_lvl = f"{p1['lvl']}(L.{p1['leg']})", f"{p2['lvl']}(L.{p2['leg']})"
        p1_rnk, p2_rnk = f"#{p1['rang']}", f"#{p2['rang']}"

        embed.add_field(
            name="<:players:1512504277392953426> Fiche d'Identité Générale",
            value=f"```\n{build_row('Alli.', p1_all, am_1, p2_all, am_2)}\n{build_row('Puiss. Alli', format_num(p1['alli_might']), '', format_num(p2['alli_might']), '')}\n{build_row('Niv.', p1_lvl, lvl_1, p2_lvl, lvl_2)}\n{build_row('Rang', p1_rnk, rnk_1, p2_rnk, rnk_2)}\n```",
            inline=False
        )

        embed.add_field(
            name="<:2_:1512574740915818527> Axe Militaire & Robustesse",
            value=f"```\n{build_row('Puiss.', format_num(p1['pp']), pp_1, format_num(p2['pp']), pp_2)}\n{build_row('Incendies', p1['feux_txt'], '', p2['feux_txt'], '')}\n```",
            inline=False
        )

        embed.add_field(
            name="<:events:1512574699555782666> Suivi des Événements (Moy. 3 éd.)",
            value=f"```\n{build_row('Nomad.', format_num(p1['event_averages']['nomades']), nom_1, format_num(p2['event_averages']['nomades']), nom_2)}\n{build_row('Samou.', format_num(p1['event_averages']['samourais']), sam_1, format_num(p2['event_averages']['samourais']), sam_2)}\n{build_row('Corbeaux', format_num(p1['event_averages']['corbeaux']), cor_1, format_num(p2['event_averages']['corbeaux']), cor_2)}\n{build_row('Étrangers', format_num(p1['event_averages']['etrangers']), et_1, format_num(p2['event_averages']['etrangers']), et_2)}\n```",
            inline=False
        )

        embed.add_field(
            name="<:icon_points:1512502439339888820> Activité & Vigilance",
            value=f"```\n{build_row('Gloire', format_num(p1['gloire']), glr_1, format_num(p2['gloire']), glr_2)}\n{build_row('Pillage/J', format_num(p1['butin']), btn_1, format_num(p2['butin']), btn_2)}\n{build_row('Colombe', p1['colombe_txt'], '', p2['colombe_txt'], '')}\n```",
            inline=False
        )

        verdict = f"**{p1['nom']}** ({score1}🏆) vs **{p2['nom']}** ({score2}🏆)."
        if p1['malus_colombe'] < 0 or p1['malus_feu'] < 0 or p2['malus_colombe'] < 0 or p2['malus_feu'] < 0:
            verdict += " Les handicaps structurels (incendies de châteaux ou colombes d'esquive) ont lourdement grevé l'indice opérationnel."
        
        embed.add_field(name="🎤 Rapport de comparaison", value=f"> *{verdict}*", inline=False)
        setup_embed_footer(embed, interaction)

        await interaction.followup.send(embed=embed)

    # ==========================================
    # 🤝 GROUPE DE COMMANDES : DIPLOMATIE
    # ==========================================
    diplo_group = app_commands.Group(
        name="diplomatie", 
        description="Gestion des relations diplomatiques (Admin uniquement)",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True
    )

    @diplo_group.command(name="add", description="Définit le statut diplomatique entre deux alliances")
    @app_commands.autocomplete(mon_alliance=alliance_autocomplete)
    @app_commands.autocomplete(cible=alliance_autocomplete)
    @app_commands.choices(statut=[
        app_commands.Choice(name="🟢 Allié", value="allies"),
        app_commands.Choice(name="🟡 PNA (Pacte de Non-Agression)", value="pna"),
        app_commands.Choice(name="🔴 En Guerre", value="guerre")
    ])
    async def d_add(self, interaction: discord.Interaction, mon_alliance: str, cible: str, statut: app_commands.Choice[str]):
        logger.info(f"🤝 [Diplo Add] Utilisé par {interaction.user.name} ({cible} -> {statut.value} pour {mon_alliance})")
        
        # 🔐 Sécurisé : Lecture asynchrone sécurisée par le verrou
        data = await load_diplo_async()
        source_key = get_alliance_diplo_key(data, mon_alliance)
        
        if source_key not in data:
            data[source_key] = {"allies": [], "pna": [], "guerre": [], "guild_id": interaction.guild_id}
        else:
            owner_id = data[source_key].get("guild_id")
            if owner_id and owner_id != interaction.guild_id:
                return await interaction.response.send_message("<:error:1512505075220611172> **Accès refusé** : La diplomatie de cette alliance est gérée par un autre serveur Discord.", ephemeral=True)
            elif not owner_id:
                data[source_key]["guild_id"] = interaction.guild_id
                
        cible_lower = cible.lower()
        for key in ["allies", "pna", "guerre"]:
            data[source_key][key] = [a for a in data[source_key][key] if a.lower() != cible_lower]
            
        data[source_key][statut.value].append(cible)
        # 🔐 Sécurisé : Sauvegarde asynchrone isolée
        await save_diplo_async(data)
        
        emojis = {"allies": "🟢", "pna": "🟡", "guerre": "🔴"}
        await interaction.response.send_message(f"<:4_:1512574743369224303> {emojis[statut.value]} Pour **{mon_alliance}**, l'alliance **{cible}** a été enregistrée en tant que **{statut.name}**.", ephemeral=True)

    @diplo_group.command(name="remove", description="Retire une alliance de votre diplomatie (devient neutre)")
    @app_commands.autocomplete(mon_alliance=alliance_autocomplete)
    @app_commands.autocomplete(cible=alliance_autocomplete)
    async def d_remove(self, interaction: discord.Interaction, mon_alliance: str, cible: str):
        logger.info(f"🤝 [Diplo Remove] Utilisé par {interaction.user.name} ({mon_alliance} retire {cible})")
        
        # 🔐 Sécurisé : Lecture asynchrone sécurisée par le verrou
        data = await load_diplo_async()
        source_key = get_alliance_diplo_key(data, mon_alliance)
        
        if source_key not in data:
            return await interaction.response.send_message(f"<:4_:1512574743369224303> **{mon_alliance}** n'a aucune diplomatie enregistrée.", ephemeral=True)

        owner_id = data[source_key].get("guild_id")
        if owner_id and owner_id != interaction.guild_id:
            return await interaction.response.send_message("<:error:1512505075220611172> **Accès refusé**.", ephemeral=True)

        cible_lower = cible.lower()
        trouve = False
        
        for key in ["allies", "pna", "guerre"]:
            if any(a.lower() == cible_lower for a in data[source_key][key]):
                data[source_key][key] = [a for a in data[source_key][key] if a.lower() != cible_lower]
                trouve = True
                
        if trouve:
            # 🔐 Sécurisé : Sauvegarde asynchrone isolée
            await save_diplo_async(data)
            await interaction.response.send_message(f"<:4_:1512574743369224303> L'alliance **{cible}** a été retirée. Elle est redevenue Neutre.", ephemeral=True)
        else:
            await interaction.response.send_message(f"<:error:1512505075220611172> **{cible}** n'est dans aucune liste diplomatique de **{mon_alliance}**.", ephemeral=True)

    @diplo_group.command(name="list", description="Affiche le tableau diplomatique d'une alliance (Privé)")
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    async def d_list(self, interaction: discord.Interaction, alliance: str):
        logger.info(f"📜 [Diplo List] Utilisé par {interaction.user.name} pour l'alliance {alliance}")
        
        # 🔐 Sécurisé : Lecture asynchrone sécurisée par le verrou
        data = await load_diplo_async()
        source_key = get_alliance_diplo_key(data, alliance)
        
        if source_key not in data:
            return await interaction.response.send_message(f"<:4_:1512574743369224303> Le registre diplomatique de **{alliance}** est vide.", ephemeral=True)
            
        diplo_alli = data[source_key]
        owner_id = diplo_alli.get("guild_id")
        
        if owner_id and owner_id != interaction.guild_id:
            return await interaction.response.send_message(f"<:error:1512505075220611172> **Accès classifié** : Vous n'avez pas l'autorisation de consulter la diplomatie de **{alliance}** depuis ce serveur.", ephemeral=True)
        
        embed = discord.Embed(title=f"📜 Registre Diplomatique : {alliance}", color=discord.Color.gold())
        embed.add_field(name="🟢 ALLIÉS", value="\n".join([f"🛡️ {a}" for a in diplo_alli.get("allies", [])]) or "*Aucun*", inline=True)
        embed.add_field(name="🟡 PNA", value="\n".join([f"🤝 {a}" for a in diplo_alli.get("pna", [])]) or "*Aucun*", inline=True)
        embed.add_field(name="🔴 GUERRE", value="\n".join([f"⚔️ {a}" for a in diplo_alli.get("guerre", [])]) or "*Aucune*", inline=False)
        setup_embed_footer(embed, interaction)

        await interaction.response.send_message(embed=embed, ephemeral=True)

# 🔌 Branchement du Cog
async def setup(bot: commands.Bot):
    await bot.add_cog(GuerreCog(bot))