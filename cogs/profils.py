# -*- coding: utf-8 -*-
import os
import json
import asyncio
import traceback
import logging
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import discord
from discord import app_commands
from discord.ext import commands
import aiohttp

# 🛠️ On importe nos outils depuis utils.py
from utils import (
    BASE_DATA_PATH, 
    CONFIG_DIR,
    t,
    joueur_autocomplete, 
    alliance_autocomplete, 
    format_num, 
    get_discord_timestamp, 
    BOT_VERSION,
    setup_embed_footer,
    PaginationView,
    get_server_config, 
    get_api_headers
)

logger = logging.getLogger("GGE_Bot")

def _get_api_timestamp(*sources):
    """
    Explore de manière récursive et profonde les structures de données renvoyées par l'API.
    Traque TOUTES les dates trouvées pour s'assurer d'extraire la plus récente.
    """
    dates_trouvees = []

    def search_ts(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ["updated_at", "updatedAt", "last_update", "date", "collected_at", "last_collected_at", "attacked_at"] and isinstance(v, str):
                    if len(v) >= 10 and v[4] == '-':
                        dates_trouvees.append(v)
            
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    search_ts(v)
                    
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    search_ts(item)

    for src in sources:
        if src:
            search_ts(src)

    if dates_trouvees:
        try:
            latest_str = max(dates_trouvees)
            return datetime.fromisoformat(latest_str.replace('Z', '+00:00'))
        except:
            pass
            
    return discord.utils.utcnow()


class HistoriqueView(discord.ui.View):
    def __init__(self, embeds_dict, interaction: discord.Interaction, langue: str = "fr"):
        super().__init__(timeout=300)
        self.embeds_dict = embeds_dict
        self.interaction = interaction
        self.langue = langue
        
        self.btn_pseudo.label = t(langue, "prof_hist_btn_pseudos", defaut="Pseudos")
        self.btn_alliance.label = t(langue, "prof_hist_btn_alliances", defaut="Alliances")
        self.btn_position.label = t(langue, "prof_hist_btn_mouvements", defaut="Mouvements")
        
        self.current_cat = "pseudo"
        self.current_page = 0
        self._update_navigation()

    def _update_navigation(self):
        self.clear_items()
        
        self.btn_pseudo.style = discord.ButtonStyle.primary if self.current_cat == "pseudo" else discord.ButtonStyle.secondary
        self.btn_alliance.style = discord.ButtonStyle.primary if self.current_cat == "alliance" else discord.ButtonStyle.secondary
        self.btn_position.style = discord.ButtonStyle.primary if self.current_cat == "position" else discord.ButtonStyle.secondary
        
        self.add_item(self.btn_pseudo)
        self.add_item(self.btn_alliance)
        self.add_item(self.btn_position)
        
        if len(self.embeds_dict[self.current_cat]) > 1:
            self.add_item(self.btn_prev)
            self.add_item(self.btn_next)

    async def _switch_category(self, interaction: discord.Interaction, cat: str):
        self.current_cat = cat
        self.current_page = 0
        self._update_navigation()
        await interaction.response.edit_message(embed=self.embeds_dict[self.current_cat][0], view=self)

    @discord.ui.button(emoji="<:renames:1512574708913143858>", row=0)
    async def btn_pseudo(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "pseudo")

    @discord.ui.button(emoji="<:alliances:1512574688415580242>", row=0)
    async def btn_alliance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "alliance")

    @discord.ui.button(emoji="<:compass:1512504625364729987>", row=0)
    async def btn_position(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "position")

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.blurple, row=1)
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        await interaction.response.edit_message(embed=self.embeds_dict[self.current_cat][self.current_page], view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.blurple, row=1)
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.embeds_dict[self.current_cat]) - 1, self.current_page + 1)
        await interaction.response.edit_message(embed=self.embeds_dict[self.current_cat][self.current_page], view=self)


class ProfilsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bdd_chemin = BASE_DATA_PATH / "bdd_items_gge.json"
        
        # 🎨 PALETTE CYAN & BLEU PÉTROLE
        self.clr_joueur      = discord.Color.from_rgb(0, 163, 204)  
        self.clr_alliance    = discord.Color.from_rgb(0, 115, 153)  
        self.clr_historique  = discord.Color.from_rgb(0, 77, 102)   
        self.clr_alliance_pp = discord.Color.from_rgb(0, 140, 186)  
        self.clr_colombe     = discord.Color.from_rgb(0, 180, 216)  

    # ========================================================
    # 👑 COMMANDE : JOUEUR
    # ========================================================
    @app_commands.command(name="player", description="Detailed player profile")
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def player(self, interaction: discord.Interaction, player: str):
        try: 
            await interaction.response.defer(thinking=True)
        except: 
            return
        
        langue, serveur = await get_server_config(interaction)
        logger.info(f"👤 [Joueur] Consultation par {interaction.user.name} pour le joueur : {player}")
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
        
        try:
            full_json = await self._get_player_full_data(player, interaction, langue=langue)
            
            if not full_json:
                msg = t(langue, "error_player_not_found", joueur=player, defaut=f"<:error:1512505075220611172> Joueur **{player}** introuvable.")
                await interaction.followup.send(msg)
                return

            data = full_json.get('parsed_data', {})
            if not data:
                await interaction.followup.send(t(langue, "prof_err_empty_data", defaut="<:error:1512505075220611172> Données vides."))
                return

            # --- RÉCUPÉRATION DES DONNÉES CLASSIQUES ET NOUVELLES ---
            main_pts = int(data.get('main_points', 0))
            honor_pts = int(data.get('honor', 0))
            might_all_time = int(data.get('might_all_time', 0))
            max_honor = int(data.get('max_honor', 0))
            loot_current = int(data.get('loot_current', 0))
            loot_all_time = int(data.get('loot_all_time', 0))
            peace = data.get('peace_disabled_at')

            # --- GESTION DE LA COLOMBE ---
            txt_colombe = ""
            if peace and peace != "null":
                try:
                    dt_peace = datetime.fromisoformat(peace.replace('Z', '+00:00'))
                    if dt_peace > discord.utils.utcnow():
                        txt_colombe = f"\n<:peace:1512503935892586566> **Colombe** : <t:{int(dt_peace.timestamp())}:R>"
                except: pass

            alliance_info = data.get('alliance', {})
            rank_id = alliance_info.get('rank')
            
            ranks_map = {
                0: t(langue, "prof_role_0", defaut="Chef"), 1: t(langue, "prof_role_1", defaut="Représentant"),
                2: t(langue, "prof_role_2", defaut="Maréchal"), 3: t(langue, "prof_role_3", defaut="Trésorier"),
                4: t(langue, "prof_role_4", defaut="Diplomate"), 5: t(langue, "prof_role_5", defaut="Recruteur"),
                6: t(langue, "prof_role_6", defaut="Général"), 7: t(langue, "prof_role_7", defaut="Sergent"),
                8: t(langue, "prof_role_8", defaut="Membre"), 9: t(langue, "prof_role_9", defaut="Novice")
            }
            
            fallback_role = t(langue, "prof_role_fallback", r=rank_id, defaut=f" (Grade {rank_id})") if rank_id is not None else ""
            role_txt = f" ({ranks_map[rank_id]})" if rank_id in ranks_map else fallback_role
            
            txt_sans_alliance = t(langue, "prof_no_alliance", defaut="Sans alliance")
            alliance_name = alliance_info.get('name', txt_sans_alliance)
            alliance_display = f"**{alliance_name}**{role_txt}" if alliance_name != txt_sans_alliance else f"**{txt_sans_alliance}**"

            outposts = data.get('outposts', [])
            type_emojis = {1: "<:squarecastle:1512573757426892911>", 3: "<:squarecapital:1512573756243972237>", 4: "<:squareoutpost:1512573761583579228>", 10: "<:date:1512573832375042340>", 12: "<:dungeons:1512574697223753798>", 22: "<:castle22:1512573821520183347>", 23: "<:castle23:1512573823118086174>", 24: "<:aquamarine_16:1512573724786950346>", 26: "<:castle26:1512573824086835280>"}
            sort_priority = {1: 0, 4: 1, 12: 2, 3: 3, 22: 4, 23: 5, 24: 7, 26: 6}
            
            if outposts:
                outposts.sort(key=lambda x: (sort_priority.get(int(x.get('type', 99)), 99), x.get('world_id', 0)))

            # Extraction de la date
            collected = full_json.get('collected_at', discord.utils.utcnow())
            if not isinstance(collected, datetime):
                collected = discord.utils.utcnow()
            ts = int(collected.timestamp())

            embed_title = t(langue, "prof_joueur_title", n=data.get('name', player), defaut=f"<:players:1512504277392953426> Profil de {data.get('name', player)}")
            embed = discord.Embed(title=embed_title, color=self.clr_joueur)
            
            # Injection de la date dans la description
            embed.description = f"{lbl_date} <t:{ts}:F> (<t:{ts}:R>)"
            
            # --- AFFICHAGE DES INFORMATIONS (Design Dashboard) ---
            info_title = t(langue, "prof_info_title", defaut="<:info:1512502828193808537> Informations Générales")
            unk_id = t(langue, "prof_unknown", defaut="Inconnu")
            
            # Formatage de la colombe
            status_txt = t(langue, "prof_status_combat", defaut="⚔️ **Statut :** Prêt au combat")
            if peace and peace != "null":
                try:
                    dt_peace = datetime.fromisoformat(peace.replace('Z', '+00:00'))
                    if dt_peace > discord.utils.utcnow():
                        ts_peace = int(dt_peace.timestamp())
                        status_txt = t(langue, "prof_status_peace", tp=ts_peace, defaut=f"<:peace:1512503935892586566> **Colombe :** Jusqu'à <t:{ts_peace}:R> (<t:{ts_peace}:t>)")
                except: pass

            info_val = (
                f"<:lvl:1512571152524906596> **Niveau :** {data.get('level', 0)} (Lég. {data.get('legendary_level', 0)})\n"
                f"<:renames:1512574708913143858> **ID Joueur :** `{data.get('player_id', unk_id)}`\n"
                f"{status_txt}"
            )
            embed.add_field(name=info_title, value=info_val, inline=True)
            
            # --- AFFICHAGE DES STATISTIQUES ---
            rank_title = t(langue, "prof_rank_title", defaut="<:empirerankings:1512574698301423847> Statistiques")
            rank_desc = (
                f"<:might:1512574615422107818> **Puissance :** {main_pts:,} *(Max: {might_all_time:,})*\n"
                f"<:honor2:1512573861521260544> **Honneur :** {honor_pts:,} *(Max: {max_honor:,})*\n"
                f"<:loot:1512439015570276553> **Pillage/J. :** {loot_current:,} *(Max: {loot_all_time:,})*"
            ).replace(",", " ") 
            
            embed.add_field(name=rank_title, value=rank_desc, inline=True)
            
            # --- ALLIANCE ---
            alli_title = t(langue, "prof_alli_title", defaut="<:alliances:1512574688415580242> Alliance")
            embed.add_field(name=alli_title, value=f"{alliance_display}\n\u200b", inline=False) # \u200b pour aérer un peu
            
            # --- POSITIONS (Avec Noms Personnalisés !) ---
            if outposts:
                coords_txt = ""
                for op in outposts[:10]:
                    emoji = type_emojis.get(int(op.get('type', 99)), "🗺️")
                    w_emoji = op.get('world_emoji', "🗺️") # <--- L'émoji du monde
                    
                    c_name = op.get('custom_name')
                    display_name = f"**{c_name}**" if c_name else f"*{op['type_label']}*"
                        
                    coords_txt += f"{emoji} {display_name} {w_emoji} `{op['coords_x']}:{op['coords_y']}`\n"
                    
                if len(outposts) > 10: 
                    coords_txt += t(langue, "prof_pos_others", count=(len(outposts) - 10), defaut=f"*... et {len(outposts) - 10} autres positions.*\n") + "\n"
                    
                pos_title = t(langue, "prof_pos_title", count=len(outposts), defaut=f"<:compass:1512504625364729987> Positions ({len(outposts)})")
                embed.add_field(name=pos_title, value=coords_txt[:1024], inline=False)

            current_vassals = data.get('vassal_villages', [])
            if current_vassals:
                v_glace = len([v for v in current_vassals if v.get('world_id') == 2])
                v_sable = len([v for v in current_vassals if v.get('world_id') == 1])
                v_pic = len([v for v in current_vassals if v.get('world_id') == 3])
                v_orage = len([v for v in current_vassals if v.get('world_id') == 4])
                v_emp = len([v for v in current_vassals if v.get('world_id') == 0])
                
                v_txt = t(langue, "prof_vr_details", vg=v_glace, vs=v_sable, vp=v_pic, ve=v_emp, vo=v_orage, defaut=f"<:dungeon2:1512573843267518546> Glace: **{v_glace}** | <:dungeon1:1512573842277794062> Sables: **{v_sable}** | <:dungeon3:1512573844538396692> Pics: **{v_pic}**")
                v_coords = "\n".join([f"<:date:1512573832375042340> VR ({v['world_label']}) ➔ `{v['coords_x']}:{v['coords_y']}`" for v in current_vassals[:5]])
                
                vr_title = t(langue, "prof_vr_title", count=len(current_vassals), defaut=f"<:date:1512573832375042340> Villages à Ressources ({len(current_vassals)})")
                embed.add_field(name=vr_title, value=f"{v_txt}\n\n{v_coords}"[:1024], inline=False)

            await setup_embed_footer(embed, interaction, langue)
            await interaction.followup.send(embed=embed)

        except Exception as e:
            import traceback
            logger.error(f"[Profils - Joueur] Erreur fatale : {traceback.format_exc()}")
            try: 
                await interaction.followup.send(t(langue, "prof_err_internal", defaut="<:error:1512505075220611172> Erreur système interne."))
            except: 
                pass

    # ========================================================
    # 🛰️ MÉTHODE INTERNE : COLLECTEUR DE DONNÉES JOUEUR
    # ========================================================
    async def _get_player_full_data(self, player_name: str, interaction: discord.Interaction = None, langue: str = "fr"):
        headers = await get_api_headers(interaction)
        serveur = headers.get('gge-server', 'E4K_FR1')
                
        api_url = "https://api.gge-tracker.com/api/v1"
        
        castle_types = {
            1: t(langue, "prof_castle_1", defaut="Château Principal"), 3: t(langue, "prof_castle_3", defaut="Capitale"), 
            4: t(langue, "prof_castle_4", defaut="Avant-Poste"), 10: t(langue, "prof_castle_10", defaut="Village à Ressource"), 
            12: t(langue, "prof_castle_12", defaut="Château Secondaire"), 22: t(langue, "prof_castle_22", defaut="Cité Marchande"), 
            23: t(langue, "prof_castle_23", defaut="Tour Royale"), 24: t(langue, "prof_castle_24", defaut="Ile aux Ressources"), 
            26: t(langue, "prof_castle_26", defaut="Monument")
        }
        worlds = {
            0: t(langue, "prof_world_0", defaut="Le Grand Empire"), 1: t(langue, "prof_world_1", defaut="Les Sables Brûlants"),
            2: t(langue, "prof_world_2", defaut="Glacier Éternel"), 3: t(langue, "prof_world_3", defaut="Pics du Feu"), 
            4: t(langue, "prof_world_4", defaut="Les Îles Orageuses")
        }

        world_emojis = {
            0: "<:dungeon0:1512573840704671775>", # Le Grand Empire (Utilise ton émoji pour le grand empire)
            1: "<:dungeon1:1512573842277794062>",   # Sables Brûlants
            2: "<:dungeon2:1512573843267518546>",   # Glacier Éternel
            3: "<:dungeon3:1512573844538396692>",    # Pics du Feu
            4: "<:dungeon4:1512573845737963722>"     # Îles Orageuses
        }

        
        txt_unk_world = t(langue, "prof_world_unknown", defaut="Monde inconnu")
        txt_unk_castle = t(langue, "prof_castle_unknown", defaut="Type inconnu")

        from urllib.parse import quote
        safe_name = quote(str(player_name))
        search_url = f"{api_url}/players/{safe_name}"
        
        session = self.bot.session
        if not session: return None
            
        try:
            # 1. Base Info
            async with session.get(search_url, headers=headers, timeout=15) as response:
                if response.status != 200: return None
                basic_info = await response.json()
                if isinstance(basic_info, list):
                    if not basic_info: return None
                    basic_info = basic_info[0]
                    
            player_id = basic_info.get('player_id')
            if not player_id: return None
            
            # 2. Stats Info
            stats_url = f"{api_url}/statistics/ranking/player/{player_id}"
            async with session.get(stats_url, headers=headers, timeout=15) as stats_response:
                stats_data = {}
                if stats_response.status == 200: stats_data = await stats_response.json()

            # 3. NOUVEAU : Récupération des IDs de châteaux et de leurs noms (En parallèle !)
            castle_names_map = {}
            search_castles_url = f"{api_url}/castle/search/{safe_name}"
            
            try:
                async with session.get(search_castles_url, headers=headers, timeout=10) as c_response:
                    if c_response.status == 200:
                        c_data = await c_response.json()
                        if isinstance(c_data, list):
                            
                            # Fonction interne pour fetch un seul château avec le KINGDOM ID
                            async def fetch_castle_name(castle_id, kingdom_id, cx, cy):
                                analysis_url = f"{api_url}/castle/analysis/{castle_id}?kingdomId={kingdom_id}"
                                try:
                                    async with session.get(analysis_url, headers=headers, timeout=5) as a_response:
                                        if a_response.status == 200:
                                            a_data = await a_response.json()
                                            c_name = None
                                            
                                            if isinstance(a_data, list) and len(a_data) > 0:
                                                c_name = a_data[0].get('castleName')
                                            elif isinstance(a_data, dict):
                                                c_name = a_data.get('castleName')
                                                
                                            if c_name:
                                                return (int(cx), int(cy)), c_name
                                except Exception as e:
                                    pass # Échec silencieux, on gardera le nom par défaut
                                return None, None

                            # Préparation des tâches asynchrones
                            tasks = []
                            for c in c_data:
                                c_id = c.get('id')
                                k_id = c.get('kingdomId') # <-- L'information cruciale !
                                cx = c.get('positionX')
                                cy = c.get('positionY')
                                
                                if c_id is not None and k_id is not None and cx is not None and cy is not None:
                                    tasks.append(fetch_castle_name(c_id, k_id, int(cx), int(cy)))
                            
                            # Lancement simultané pour une vitesse maximale
                            if tasks:
                                results = await asyncio.gather(*tasks)
                                for coords, name in results:
                                    if coords and name:
                                        castle_names_map[coords] = name

            except Exception as e:
                logger.warning(f"[API] Impossible de lier les noms de châteaux pour {player_name}: {e}")

            outposts = []
            vassal_villages = []
            
            for c in stats_data.get('castles', []):
                if len(c) >= 3:
                    t_id = c[2]
                    cx, cy = int(c[0]), int(c[1]) 
                    struct = {
                        'world_id': 0, 'coords_x': cx, 'coords_y': cy,
                        'type': t_id, 'world_label': worlds.get(0, worlds[0]),
                        'type_label': castle_types.get(t_id, f"{txt_unk_castle} ({t_id})"),
                        'custom_name': castle_names_map.get((cx, cy)) 
                    }
                    if t_id == 10: vassal_villages.append(struct)
                    else: outposts.append(struct)
                        
            for c in stats_data.get('castles_realm', []):
                if len(c) >= 4:
                    w_id, t_id = c[0], c[3]
                    cx, cy = int(c[1]), int(c[2])
                    struct = {
                        'world_id': w_id, 
                        'coords_x': cx, 'coords_y': cy,
                        'type': t_id, 
                        'world_label': worlds.get(w_id, f"{txt_unk_world} ({w_id})"),
                        'world_emoji': world_emojis.get(w_id, "🗺️"),
                        'type_label': castle_types.get(t_id, f"{txt_unk_castle} ({t_id})"),
                        'custom_name': castle_names_map.get((cx, cy))
                    }
                    if t_id == 10: vassal_villages.append(struct)
                    else: outposts.append(struct)

            for v in stats_data.get('villages', []):
                if len(v) >= 3:
                    w_id = v[0]
                    struct = {
                        'world_id': w_id, 'coords_x': v[1], 'coords_y': v[2],
                        'type': 10, 'world_label': worlds.get(w_id, f"{txt_unk_world} ({w_id})"),
                        'type_label': castle_types.get(10, t(langue, "prof_castle_10", defaut="Village à Ressource"))
                    }
                    vassal_villages.append(struct)

            parsed_data = {
                'player_id': basic_info.get('player_id'),
                'name': basic_info.get('player_name'),
                'level': basic_info.get('level', 0) or stats_data.get('level', 0),
                'legendary_level': basic_info.get('legendary_level', 0) or stats_data.get('legendary_level', 0),
                'honor': basic_info.get('honor', 0),
                'main_points': stats_data.get('might_current', 0) or stats_data.get('might', 0),
                'might_all_time': stats_data.get('might_all_time', 0),
                'max_honor': stats_data.get('max_honor', 0) or basic_info.get('max_honor', 0),
                'loot_current': stats_data.get('loot_current', 0) or basic_info.get('loot_current', 0),
                'loot_all_time': stats_data.get('loot_all_time', 0) or basic_info.get('loot_all_time', 0),
                'peace_disabled_at': basic_info.get('peace_disabled_at'),
                'alliance': {
                    'id': basic_info.get('alliance_id'),
                    'name': basic_info.get('alliance_name') or stats_data.get('alliance_name') or t(langue, "prof_no_alliance", defaut='Sans alliance'),
                    'rank': basic_info.get('alliance_rank') or stats_data.get('alliance_rank')
                },
                'outposts': outposts,
                'vassal_villages': vassal_villages 
            }
            
            api_timestamp = _get_api_timestamp(stats_data, basic_info)
            
            return {
                'collected_at': api_timestamp,
                'player_name': basic_info.get('player_name'),
                'server': serveur,
                'parsed_data': parsed_data
            }
            
        except Exception as e:
            logger.error(f"Erreur API Joueur pour {player_name}: {e}")
            return None

    # ========================================================
    # 🛡️ COMMANDE : ALLIANCE
    # ========================================================
    @app_commands.command(name="alliance", description="Detailed profile of an alliance (Quick and paginated)")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def alliance(self, interaction: discord.Interaction, alliance_name: str):
        try: 
            await interaction.response.defer(thinking=True)
        except: 
            return
        
        logger.info(f"🛡️ [Alliance] Consultation par {interaction.user.name} pour : {alliance_name}")
        
        langue, serveur = await get_server_config(interaction)
        txt_unknown = t(langue, "prof_unknown", defaut="Inconnu")
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
        
        try:
            is_live = False
            target_alliance_id = None
            total_might = total_fame = total_honor = 0
            leader_name = txt_unknown
            members = []
            collected_time = None
            
            # --- PLAN A : L'API EN DIRECT ---
            try:
                api_data = await self._get_alliance_full_data(alliance_name, interaction, langue=langue)
                
                if api_data and 'parsed_data' in api_data:
                    is_live = True
                    collected_time = api_data.get('collected_at')
                    parsed = api_data['parsed_data']
                    target_alliance_id = parsed.get('alliance_id')
                    alliance_name = parsed.get('name', alliance_name)
                    leader_name = parsed.get('leader', txt_unknown)
                    total_might = parsed.get('total_might', 0)
                    total_honor = parsed.get('total_honor', 0)
                    total_fame = parsed.get('total_fame', 0)
                    members = parsed.get('members', [])
            except Exception as e:
                logger.warning(f"[Profils - Alliance] API inaccessible, passage au Plan B... ({e})")

            # --- PLAN B : LE CACHE LOCAL ---
            if not is_live:
                player_files = list((BASE_DATA_PATH / 'server_scans' / serveur).rglob('server_*.json'))
                local_data = {}
                
                if player_files:
                    latest = max(player_files, key=lambda p: p.stat().st_mtime)
                    
                    def _load_local_json():
                        with open(latest, 'r', encoding='utf-8') as f:
                            return json.load(f)
                    
                    full_json = await asyncio.to_thread(_load_local_json)
                    local_data = full_json.get('players', {})
                    collected_time = full_json.get('collected_at')

                for p_info in local_data.values():
                    a_obj = p_info.get('alliance')
                    a_name = a_obj.get('name') if isinstance(a_obj, dict) else (p_info.get('alliance_name') or a_obj)
                    if a_name and str(a_name).lower() == alliance_name.lower():
                        aid = p_info.get('allianceId') or p_info.get('alliance_id')
                        if not aid and isinstance(a_obj, dict): aid = a_obj.get('allianceId') or a_obj.get('alliance_id')
                        if aid:
                            target_alliance_id = str(aid)
                            alliance_name = str(a_name)
                            break
                            
                if not target_alliance_id:
                    return await interaction.followup.send(t(langue, "prof_alli_not_found", nom=alliance_name, defaut=f"<:error:1512505075220611172> Alliance **{alliance_name}** introuvable."))

                for p_info in local_data.values():
                    a_obj = p_info.get('alliance')
                    aid = p_info.get('allianceId') or p_info.get('alliance_id')
                    if not aid and isinstance(a_obj, dict): aid = a_obj.get('allianceId') or a_obj.get('alliance_id')
                        
                    if str(aid) == target_alliance_id:
                        m_rank = int(p_info.get('alliance_rank', 9))
                        m_might = int(p_info.get('main_points') or p_info.get('might_current') or 0)
                        m_fame = int(p_info.get('fame') or 0)
                        m_honor = int(p_info.get('honor') or 0)
                        
                        total_might += m_might
                        total_fame += m_fame
                        total_honor += m_honor
                        
                        m_name = p_info.get('name', txt_unknown)
                        if str(m_rank) in ["0", "1"] and (leader_name == txt_unknown or str(m_rank) == "0"):
                            leader_name = m_name
                            
                        members.append({
                            "name": m_name, "level": p_info.get('level', 0), "leg_level": p_info.get('legendary_level', 0),
                            "might": m_might, "fame": m_fame, "honor": m_honor, "rank": m_rank
                        })

                members.sort(key=lambda x: (int(x.get('rank', 9)), -x.get('might', 0)))

            if not members:
                return await interaction.followup.send(t(langue, "prof_alli_ghost", alli=alliance_name, defaut=f"<:error:1512505075220611172> L'alliance **{alliance_name}** semble vide."))

            # Timestamp extraction
            if isinstance(collected_time, str):
                try: collected_time = datetime.fromisoformat(collected_time.replace('Z', '+00:00'))
                except: collected_time = discord.utils.utcnow()
            elif not collected_time:
                collected_time = discord.utils.utcnow()
                
            ts = int(collected_time.timestamp())
            suffixe_cache = " *(Plan B activé)*" if not is_live else ""
            str_date_header = f"{lbl_date} <t:{ts}:F> (<t:{ts}:R>){suffixe_cache}\n\n"

            # --- AFFICHAGE PAGINÉ ---
            embeds = []
            rank_emojis = {0: "<:0_:1512574737677684818>", 1: "<:1_:1512574739208470640>", 2: "<:2_:1512574740915818527>", 3: "<:3_:1512574742245412874>", 4: "<:4_:1512574743369224303>", 5: "<:5_:1512574744501817515>", 6: "<:6_:1512574745617498172>", 7: "<:7_:1512574746989039839>", 8: "<:8_:1512574748356251691>", 9: "<:9_:1512574749430120519>"}
            chunk_size = 15
            nb_pages = max(1, (len(members) - 1) // chunk_size + 1)

            for i in range(0, len(members), chunk_size):
                chunk = members[i:i+chunk_size]
                page_actuelle = (i // chunk_size) + 1
                
                embed_title = t(langue, "prof_alli_embed_title", a=alliance_name, defaut=f"<:alliances:1512574688415580242> Alliance : {alliance_name}")
                embed = discord.Embed(title=embed_title, color=self.clr_alliance)
                embed.description = str_date_header.strip()
                
                info_title = t(langue, "prof_info_title", defaut="<:info:1512502828193808537> Informations")
                info_desc = t(langue, "prof_alli_info_desc", l=leader_name, c=len(members), id=target_alliance_id, defaut=f"**Chef** : {leader_name}\n**Membres** : {len(members)} / 65")
                embed.add_field(name=info_title, value=info_desc, inline=True)
                
                stats_title = t(langue, "prof_alli_stats_title", defaut="<:stats:1512517930490003726> Statistiques Globales")
                stats_desc = t(langue, "prof_alli_stats_desc", m=format_num(total_might), f=format_num(total_fame), h=format_num(total_honor), defaut=f"**Puiss.** : {format_num(total_might)}")
                embed.add_field(name=stats_title, value=stats_desc, inline=True)
                
                memb_txt = ""
                for m in chunk:
                    lvl = m.get('level', 0)
                    leg = m.get('leg_level', m.get('leg', 0))
                    emoji = rank_emojis.get(int(m.get('rank', 9)), "<:players:1512504277392953426>")
                    memb_txt += f"{emoji} **{m.get('name', txt_unknown)}** ({lvl}/{leg}) ➔ {format_num(m.get('might', 0))} | {format_num(m.get('fame', 0))}\n"
                
                memb_title = t(langue, "prof_alli_members_title", cur=page_actuelle, tot=nb_pages, defaut=f"Membres (Page {page_actuelle}/{nb_pages})")
                embed.add_field(name=memb_title, value=memb_txt, inline=False)

                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

            if len(embeds) == 1:
                await interaction.followup.send(embed=embeds[0])
            else:
                view = PaginationView(embeds)
                await interaction.followup.send(embed=embeds[0], view=view)

        except Exception as e:
            logger.error(f"[Profils - Alliance] Erreur fatale : {traceback.format_exc()}")
            try: 
                await interaction.followup.send(t(langue, "prof_alli_err_internal", defaut="<:error:1512505075220611172> Erreur système interne."))
            except: 
                pass

    # ========================================================
    # 🛰️ MÉTHODE INTERNE : COLLECTEUR DE DONNÉES ALLIANCE
    # ========================================================
    async def _get_alliance_full_data(self, alliance_name: str, interaction: discord.Interaction = None, langue: str = "fr"):
        headers = await get_api_headers(interaction)
        serveur = headers.get('gge-server', 'E4K_FR1')
        api_url = "https://api.gge-tracker.com/api/v1"

        safe_name = quote(str(alliance_name))
        search_url = f"{api_url}/alliances/name/{safe_name}"
        
        session = self.bot.session
        if not session: return None

        try:
            async with session.get(search_url, headers=headers, timeout=10) as resp:
                if resp.status != 200: return None
                data1 = await resp.json()
        except Exception:
            return None

        target_alliance = data1[0] if isinstance(data1, list) and data1 else data1
        if not target_alliance: return None
            
        alliance_id = target_alliance.get('alliance_id') or target_alliance.get('id') or target_alliance.get('allianceId')
        if not alliance_id: return None

        detail_url = f"{api_url}/alliances/id/{alliance_id}"
        stats_url = f"{api_url}/statistics/alliance/{alliance_id}"
        pulse_url = f"{api_url}/statistics/alliance/{alliance_id}/pulse"

        async def fetch_json_local(url, timeout_val):
            try:
                async with session.get(url, headers=headers, timeout=timeout_val) as r:
                    if r.status == 200: return await r.json()
            except: pass
            return None

        members_data, stats_data, pulse_data = await asyncio.gather(
            fetch_json_local(detail_url, 30),
            fetch_json_local(stats_url, 40),
            fetch_json_local(pulse_url, 30)
        )

        if isinstance(members_data, list) and len(members_data) > 0: members_data = members_data[0]
        elif not members_data: members_data = {}
        
        stats_data = stats_data or {}
        pulse_data = pulse_data or {}
            
        members = members_data.get('players', members_data.get('members', members_data.get('playerList', [])))
        
        parsed_members = []
        tot_might = tot_honor = tot_fame = 0
        txt_unknown = t(langue, "prof_unknown", defaut="Inconnu")
        leader_name = txt_unknown

        for m in members:
            rank = m.get('allianceRank', m.get('alliance_rank', m.get('rank', 9)))
            might = int(m.get('might_current', m.get('might', m.get('main_points', 0))))
            honor = int(m.get('honor', 0))
            fame = int(m.get('current_fame', m.get('fame', 0)))
            
            tot_might += might
            tot_honor += honor
            tot_fame += fame
            
            if str(rank) in ["0", "1"]:
                if leader_name == txt_unknown or str(rank) == "0":
                    leader_name = m.get('player_name', m.get('playerName', m.get('name', txt_unknown)))

            parsed_members.append({
                'name': m.get('player_name', m.get('playerName', m.get('name', txt_unknown))),
                'might': might, 'honor': honor, 'fame': fame, 'level': m.get('level', 0),
                'leg_level': m.get('legendary_level', m.get('legendaryLevel', 0)), 'rank': rank
            })

        parsed_members.sort(key=lambda x: (int(x['rank']), -x['might']))

        txt_unk_alli = t(langue, "prof_unknown_alli", defaut="Inconnue")
        parsed_data = {
            'alliance_id': target_alliance.get('alliance_id') or target_alliance.get('allianceId'),
            'name': target_alliance.get('alliance_name') or target_alliance.get('name', txt_unk_alli),
            'members_count': len(parsed_members), 'leader': leader_name, 'total_might': tot_might,
            'total_honor': tot_honor, 'total_fame': tot_fame, 'members': parsed_members,
            'stats_diffs': stats_data.get('diffs', {}),
            'stats_history': {
                'loot': stats_data.get('points', {}).get('player_loot_history', []),
                'might': stats_data.get('points', {}).get('player_might_history', [])
            },
            'pulse': pulse_data
        }

        api_timestamp = _get_api_timestamp(members_data, stats_data, target_alliance)

        return {
            'collected_at': api_timestamp, 'alliance_name': parsed_data['name'],
            'server': serveur, 'parsed_data': parsed_data
        }

    # ========================================================
    # 📜 COMMANDE : HISTORIQUE
    # ========================================================
    @app_commands.command(name="history", description="Displays a player's complete history")
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def history(self, interaction: discord.Interaction, player: str):
        try: 
            await interaction.response.defer(thinking=True)
        except: 
            return
        
        langue, serveur = await get_server_config(interaction)
        logger.info(f"📜 [Historique] Consultation globale par {interaction.user.name} pour le joueur : {player}")

        p_id = None
        actualisation_dt = discord.utils.utcnow()
        try:
            full_json = await self._get_player_full_data(player, interaction, langue=langue)
            if full_json:
                data = full_json.get('parsed_data', {})
                p_id = str(data.get('player_id', ''))
                actualisation_dt = full_json.get('collected_at', actualisation_dt)
        except: pass

        if not p_id:
            await interaction.followup.send(t(langue, "prof_hist_err_id", j=player, defaut=f"<:error:1512505075220611172> ID introuvable."))
            return

        headers = await get_api_headers(interaction)

        urls_to_fetch = {
            "pseudo": f"https://api.gge-tracker.com/api/v1/updates/players/{p_id}/names",
            "alliance": f"https://api.gge-tracker.com/api/v1/updates/players/{p_id}/alliances",
            "position": f"https://api.gge-tracker.com/api/v1/server/movements?page=1&castleType=1&movementType=3&search={quote(player)}&searchType=player"
        }

        results = {}
        session = self.bot.session
        if session:
            async def fetch_url(name, url):
                try:
                    async with session.get(url, headers=headers, timeout=10) as resp:
                        if resp.status == 200: results[name] = await resp.json()
                except: pass

            tasks = [fetch_url(name, url) for name, url in urls_to_fetch.items()]
            await asyncio.gather(*tasks)

        def parse_date(iso_str):
            if not iso_str: return t(langue, "prof_hist_unknown_date", defaut="Date inconnue")
            return get_discord_timestamp(iso_str, 'd', langue)

        embeds_dict = {"pseudo": [], "alliance": [], "position": []}
        lines_per_page = 12

        categories = [
            {"key": "pseudo", "title": t(langue, "prof_hist_cat_pseudo", defaut="Historique des Pseudos"), "emoji": "<:renames:1512574708913143858>", "json_key": "updates"},
            {"key": "alliance", "title": t(langue, "prof_hist_cat_alliance", defaut="Historique des Alliances"), "emoji": "<:alliances:1512574688415580242>", "json_key": "updates"},
            {"key": "position", "title": t(langue, "prof_hist_cat_position", defaut="Historique des Positions"), "emoji": "<:compass:1512504625364729987>", "json_key": "movements"}
        ]

        has_any_data = False
        unk_val = t(langue, "prof_hist_unknown", defaut="*Inconnu*")
        no_alli_val = t(langue, "prof_hist_no_alli", defaut="*Sans alliance*")

        # 💥 NOUVEAU : En-tête de date pour la description
        if isinstance(actualisation_dt, str):
            try: actualisation_dt = datetime.fromisoformat(actualisation_dt.replace('Z', '+00:00'))
            except: actualisation_dt = discord.utils.utcnow()
            
        ts_act = int(actualisation_dt.timestamp())
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
        str_date_header = f"{lbl_date} <t:{ts_act}:F> (<t:{ts_act}:R>)\n\n"

        for cat in categories:
            lines = []
            raw_data = results.get(cat["key"], {}).get(cat["json_key"], [])

            for item in raw_data:
                d = parse_date(item.get('date', item.get('created_at')))
                if cat["key"] == "pseudo":
                    old = item.get("old_player_name") or unk_val
                    new = item.get("new_player_name") or unk_val
                    lines.append(f"<:members:1512573912305766652> {d} : ~~{old}~~ ➔ **{new}**")
                elif cat["key"] == "alliance":
                    old = item.get("old_alliance_name") or no_alli_val
                    new = item.get("new_alliance_name") or no_alli_val
                    lines.append(f"<:icon_alliance:1512573872774451210> {d} : *{old}* ➔ **{new}**")
                elif cat["key"] == "position":
                    x_old, y_old = item.get('position_x_old'), item.get('position_y_old')
                    x_new, y_new = item.get('position_x_new'), item.get('position_y_new')
                    lines.append(f"<:UyuPdm57K4WjWIFbWCTzgOUhP0hiydbK:1512574624112578580> {d} : `{x_old}:{y_old}` ➔ `{x_new}:{y_new}`")

            if lines:
                has_any_data = True
                for i in range(0, len(lines), lines_per_page):
                    chunk = lines[i:i+lines_per_page]
                    page_num = (i // lines_per_page) + 1
                    total_pages_cat = (len(lines) + lines_per_page - 1) // lines_per_page
                    suffixe_titre = f" ({page_num}/{total_pages_cat})" if total_pages_cat > 1 else ""
                    
                    emb = discord.Embed(
                        title=f"{cat['emoji']} {cat['title']} - {player}{suffixe_titre}",
                        description=str_date_header + "\n".join(chunk),
                        color=self.clr_historique
                    )
                    await setup_embed_footer(emb, interaction, langue)
                    embeds_dict[cat["key"]].append(emb)
            else:
                desc_empty = t(langue, "prof_hist_no_data_cat", defaut=" Aucun historique disponible.")
                emb = discord.Embed(
                    title=f"{cat['emoji']} {cat['title']} - {player}",
                    description=str_date_header + desc_empty,
                    color=self.clr_historique
                )
                await setup_embed_footer(emb, interaction, langue)
                embeds_dict[cat["key"]].append(emb)

        if not has_any_data:
            desc_empty_global = t(langue, "prof_hist_empty_desc", defaut="Aucun historique trouvé.")
            embed_vide = discord.Embed(
                title=t(langue, "prof_hist_empty_title", j=player, defaut=f"Dossier - {player}"), 
                description=str_date_header + desc_empty_global,
                color=self.clr_historique
            )
            await setup_embed_footer(embed_vide, interaction, langue)
            await interaction.followup.send(embed=embed_vide)
            return

        view = HistoriqueView(embeds_dict, interaction, langue=langue)
        await interaction.followup.send(embed=embeds_dict[view.current_cat][0], view=view)

    # ========================================================
    # 📈 COMMANDE : HISTORIQUE ALLIANCE PP
    # ========================================================
    @app_commands.command(name="alliance_might", description="Historical Power (PP) of an alliance over X days")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    @app_commands.describe(days="Period to analyze in days (Default: 3, Maximum: 10)")
    async def alliance_might(self, interaction: discord.Interaction, alliance_name: str, days: int = 3):
        try: 
            await interaction.response.defer(thinking=True)
        except: 
            return
        
        langue, serveur = await get_server_config(interaction)
        logger.info(f"📈 [Alliance PP] Historique demandé par {interaction.user.name} (Alliance: {alliance_name}, Jours: {days})")

        days = max(1, min(10, days))
        date_limite = discord.utils.utcnow() - timedelta(days=days)

        headers = await get_api_headers(interaction)
        safe_alliance = quote(alliance_name)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
        
        session = self.bot.session
        if not session: return await interaction.followup.send(t(langue, "prof_pp_err_http", defaut="Erreur connexion."))

        try:
            search_url = f"https://api.gge-tracker.com/api/v1/alliances/name/{safe_alliance}"
            async with session.get(search_url, headers=headers, timeout=10) as resp:
                if resp.status != 200: return await interaction.followup.send(t(langue, "prof_pp_err_not_found", defaut="Alliance introuvable."))
                data1 = await resp.json()
                target = data1[0] if isinstance(data1, list) and data1 else data1
                alliance_id = target.get('alliance_id') or target.get('id')
        except Exception:
            return await interaction.followup.send(t(langue, "prof_pp_err_api", defaut="Erreur API."))

        if not alliance_id: return await interaction.followup.send(t(langue, "prof_pp_err_alli_nf", a=alliance_name, defaut="Alliance introuvable."))

        try:
            stats_url = f"https://api.gge-tracker.com/api/v1/statistics/alliance/{alliance_id}"
            detail_url = f"https://api.gge-tracker.com/api/v1/alliances/id/{alliance_id}"

            async def fetch_json(url):
                try:
                    async with session.get(url, headers=headers, timeout=15) as r:
                        if r.status == 200: return await r.json()
                except: pass
                return None

            res_stats, res_detail = await asyncio.gather(fetch_json(stats_url), fetch_json(detail_url))
            if not res_stats: return await interaction.followup.send(t(langue, "prof_pp_err_dl", defaut="Téléchargement impossible."))
            
            stats_data = res_stats
            detail_data = res_detail or {}
            
        except Exception:
            return await interaction.followup.send(t(langue, "prof_pp_err_dl_stats", defaut="Téléchargement impossible."))

        might_history = stats_data.get("points", {}).get("player_might_history", [])
        if not might_history:
            return await interaction.followup.send(t(langue, "prof_pp_no_hist", a=alliance_name, defaut="Aucun historique."))

        daily_data = {}
        for entry in might_history:
            d_str = entry.get("date")
            pid = str(entry.get("player_id"))
            pt = int(entry.get("point", 0))
            if not d_str: continue

            try:
                dt = datetime.fromisoformat(d_str.replace('Z', '+00:00'))
                if dt < date_limite: continue
                day_str = dt.strftime("%d/%m/%Y")
                if day_str not in daily_data: daily_data[day_str] = {}
                daily_data[day_str][pid] = max(daily_data[day_str].get(pid, 0), pt)
            except: pass

        if not daily_data:
            return await interaction.followup.send(t(langue, "prof_pp_no_data_days", a=alliance_name, j=days, defaut="Aucune donnée."))

        alliance_daily_might = {}
        for day, players in daily_data.items(): alliance_daily_might[day] = sum(players.values())

        sorted_days = sorted(alliance_daily_might.keys(), key=lambda d: datetime.strptime(d, "%d/%m/%Y"))
        
        premier_jour = sorted_days[0]
        dernier_jour = sorted_days[-1]
        
        pp_debut = alliance_daily_might[premier_jour]
        pp_fin = alliance_daily_might[dernier_jour]
        variation_totale = pp_fin - pp_debut
        
        pic_pp = max(alliance_daily_might.values())
        pire_pp = min(alliance_daily_might.values())

        def format_diff(val):
            if val > 0: return f"+{format_num(val)}"
            elif val < 0: return f"{format_num(val)}"
            return "0"

        stats_txt = t(langue, "prof_pp_bilan_desc", 
            p=premier_jour, d=dernier_jour, p_d=format_num(pp_debut), p_f=format_num(pp_fin), 
            v=format_diff(variation_totale), pic=format_num(pic_pp), pire=format_num(pire_pp),
            defaut=f"\n\n**Période** : du {premier_jour} au {dernier_jour}"
        )

        lignes_historique = []
        for day in reversed(sorted_days):
            pp_jour = alliance_daily_might[day]
            index_jour = sorted_days.index(day)
            if index_jour > 0:
                pp_hier = alliance_daily_might[sorted_days[index_jour - 1]]
                diff_jour = pp_jour - pp_hier
                diff_txt = f"({format_diff(diff_jour)})"
            else:
                diff_txt = t(langue, "prof_pp_start_point", defaut="(Point de départ)")

            lignes_historique.append(f"• **{day}** ➔ **{format_num(pp_jour)} PP** {diff_txt}")

        embeds = []
        chunk_size = 15
        nb_pages = max(1, (len(lignes_historique) - 1) // chunk_size + 1)
        alliance_name_real = target.get('alliance_name') or target.get('name', alliance_name)

        api_timestamp = _get_api_timestamp(detail_data, target, stats_data)
        ts_act = int(api_timestamp.timestamp())
        str_date_header = f"{lbl_date} <t:{ts_act}:F> (<t:{ts_act}:R>)\n\n"

        for i in range(0, len(lignes_historique), chunk_size):
            chunk = lignes_historique[i:i+chunk_size]
            page_actuelle = (i // chunk_size) + 1
            
            embed = discord.Embed(title=t(langue, "prof_pp_embed_title", a=alliance_name_real, defaut=f"Évolution de la Puissance pour : {alliance_name_real}"), color=self.clr_alliance_pp)
            embed_desc_i18n = t(langue, "prof_pp_embed_desc", j=days, defaut=f"Analyse sur les **{days} derniers jours**.")
            embed.description = str_date_header + embed_desc_i18n
            
            embed.add_field(name=t(langue, "prof_pp_bilan_title", defaut="Bilan Global"), value=stats_txt, inline=False)
            embed.add_field(name=t(langue, "prof_pp_daily_title", cur=page_actuelle, tot=nb_pages, defaut="Historique Quotidien"), value="\n".join(chunk), inline=False)
            
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

    # ========================================================
    # 🕊️ COMMANDE : VÉRIFIER LA COLOMBE
    # ========================================================
    @app_commands.command(name="dove", description="Check the date and time a player's protection ended")
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def dove(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer()
        langue, _ = await get_server_config(interaction)
        logger.info(f"🕊️ [Colombe] Vérification par {interaction.user.name} pour : {player}")
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
        
        headers = await get_api_headers(interaction)
        url = f"https://api.gge-tracker.com/api/v1/players/{quote(player)}"
        
        session = self.bot.session
        if not session: return await interaction.followup.send(t(langue, "prof_pp_err_http", defaut="Erreur."))

        try:
            async with session.get(url, headers=headers, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    p_data = data[0] if isinstance(data, list) and data else data
                    if not p_data: return await interaction.followup.send(t(langue, "prof_col_not_found", j=player, defaut="Joueur introuvable."))
                        
                    peace_str = p_data.get("peace_disabled_at")
                    if not peace_str or peace_str == "null":
                        return await interaction.followup.send(t(langue, "prof_col_none", j=player, defaut="Aucune colombe."))
                        
                    dt_peace = datetime.fromisoformat(peace_str.replace('Z', '+00:00'))
                    maintenant = discord.utils.utcnow()
                    ts = int(dt_peace.timestamp())
                    
                    if dt_peace > maintenant:
                        embed = discord.Embed(title=t(langue, "prof_col_embed_title", defaut="Statut de la Colombe"), color=self.clr_colombe)
                        
                        api_timestamp = _get_api_timestamp(p_data)
                        ts_act = int(api_timestamp.timestamp())
                        embed.description = f"{lbl_date} <t:{ts_act}:F> (<t:{ts_act}:R>)"
                        
                        embed.add_field(name=t(langue, "prof_col_target", defaut="Cible"), value=f"**{player}**", inline=False)
                        embed.add_field(name=t(langue, "prof_col_end", defaut="Fin de protection"), value=t(langue, "prof_col_end_val", ts=ts, defaut=f"<t:{ts}:f>"), inline=False)

                        await setup_embed_footer(embed, interaction, langue)
                        await interaction.followup.send(embed=embed)
                    else:
                        await interaction.followup.send(t(langue, "prof_col_expired", j=player, ts=ts, defaut="La colombe a expiré."))
                else:
                    await interaction.followup.send(t(langue, "prof_col_err_api", j=player, s=r.status, defaut="Erreur API."))
        except Exception:
            await interaction.followup.send(t(langue, "prof_col_err_conn", defaut="Erreur."))

    # ========================================================
    # 📜 COMMANDE : DESCRIPTION ALLIANCE (Murs Historiques)
    # ========================================================
    @app_commands.command(name="alliance_description", description="View the history of the last 7 wall changes for an alliance")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def alliance_description(self, interaction: discord.Interaction, alliance_name: str):
        try: 
            await interaction.response.defer(thinking=True)
        except: 
            return

        langue, serveur = await get_server_config(interaction)
        logger.info(f"📜 [Description Alliance] Recherche historique pour '{alliance_name}' par {interaction.user.name}")
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
        alliance_lower = alliance_name.lower().strip()

        scans_dir = BASE_DATA_PATH / "murs_scans" / serveur
        history = []

        if not scans_dir.exists():
            return await interaction.followup.send(t(langue, "prof_desc_no_folder", defaut="Dossier introuvable."))

        try:
            files = list(scans_dir.rglob('murs_alliances_*.json'))
            files.sort(key=lambda p: (p.parent.name, p.name), reverse=True)

            if files:
                try:
                    dt_local = datetime.fromtimestamp(files[0].stat().st_mtime)
                    actualisation_dt = dt_local
                except:
                    actualisation_dt = discord.utils.utcnow()
            else:
                actualisation_dt = discord.utils.utcnow()

            def _parse_murs_history():
                last_text = None
                local_hist = []
                for p in files:
                    try:
                        date_raw = p.parent.name 
                        time_raw = p.stem.replace('murs_alliances_', '').replace('-', ':')
                        parts = date_raw.split('-')
                        date_formatee = f"{parts[2]}/{parts[1]}/{parts[0]}" if len(parts) == 3 else date_raw

                        with open(p, 'r', encoding='utf-8') as f:
                            data = json.load(f)

                        desc = data.get(alliance_lower)
                        if desc and desc != last_text:
                            local_hist.append({"date": date_formatee, "heure": time_raw, "texte": desc})
                            last_text = desc
                            if len(local_hist) >= 7: break
                    except: continue
                return local_hist

            history = await asyncio.to_thread(_parse_murs_history)
                    
        except Exception as e:
            logger.error(f"Erreur parcours archives de murs : {e}")
            return await interaction.followup.send(t(langue, "prof_desc_err_tech", defaut="Erreur technique."))

        if not history:
            return await interaction.followup.send(t(langue, "prof_desc_not_found", a=alliance_name, defaut="Aucune description trouvée."))

        ts_act = int(actualisation_dt.timestamp())
        str_date_header = f"{lbl_date} <t:{ts_act}:F> (<t:{ts_act}:R>)\n\n"
        desc_i18n = t(langue, "prof_desc_embed_desc", l=len(history), defaut=f"Analyse du mur d'alliance.")

        embed = discord.Embed(
            title=t(langue, "prof_desc_embed_title", a=alliance_name.upper(), defaut=f"Archives Alliance : {alliance_name.upper()}"),
            description=str_date_header + desc_i18n,
            color=self.clr_alliance
        )

        curr_txt = t(langue, "prof_desc_current", defaut="Description Actuelle")
        for i, entry in enumerate(history):
            header_title = curr_txt if i == 0 else t(langue, "prof_desc_version", d=entry['date'], h=entry['heure'], defaut=f"Version du {entry['date']}")
            
            texte_affiche = entry['texte'].replace('<br />', '\n').replace('<br/>', '\n').replace('<br>', '\n').strip()
            valeur_champ = f">>> {texte_affiche}"
            if len(valeur_champ) > 1020: valeur_champ = valeur_champ[:1017] + "..."

            embed.add_field(name=header_title, value=valeur_champ, inline=False)

        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(ProfilsCog(bot))