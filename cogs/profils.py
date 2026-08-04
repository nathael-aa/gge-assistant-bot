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

    # Remplacement des émojis personnalisés par des émojis Unicode
    @discord.ui.button(emoji="🏷️", row=0)
    async def btn_pseudo(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "pseudo")

    @discord.ui.button(emoji="🛡️", row=0)
    async def btn_alliance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "alliance")

    @discord.ui.button(emoji="🧭", row=0)
    async def btn_position(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "position")

    # Correction : utilisation de "emoji=" au lieu de "label=" pour les flèches
    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.blurple, row=1)
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        await interaction.response.edit_message(embed=self.embeds_dict[self.current_cat][self.current_page], view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.blurple, row=1)
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.embeds_dict[self.current_cat]) - 1, self.current_page + 1)
        await interaction.response.edit_message(embed=self.embeds_dict[self.current_cat][self.current_page], view=self)


class ProfilsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bdd_chemin = BASE_DATA_PATH / "bdd_items_gge.json"
        
        self.clr_joueur      = discord.Color.from_rgb(0, 163, 204)  
        self.clr_alliance    = discord.Color.from_rgb(0, 115, 153)  
        self.clr_historique  = discord.Color.from_rgb(0, 77, 102)   
        self.clr_alliance_pp = discord.Color.from_rgb(0, 140, 186)  
        self.clr_alliance_property = discord.Color.from_rgb(0, 181, 206)  
        self.clr_colombe     = discord.Color.from_rgb(0, 180, 216)  

    # ========================================================
    # 👑 COMMANDE : PLAYER
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

            main_pts = int(data.get('main_points', 0))
            honor_pts = int(data.get('honor', 0))
            might_all_time = int(data.get('might_all_time', 0))
            max_honor = int(data.get('max_honor', 0))
            loot_current = int(data.get('loot_current', 0))
            loot_all_time = int(data.get('loot_all_time', 0))
            peace = data.get('peace_disabled_at')

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
            type_emojis = {1: "<:castle1:1512573817892110647>", 3: "<:castle3:1512573819313979544>", 4: "<:castle4:1512573820752498839>", 10: "<:date:1512573832375042340>", 12: "<:castle12:1521949211850182686>", 22: "<:castle22:1512573821520183347>", 23: "<:castle23:1512573823118086174>", 24: "<:aquamarine_16:1512573724786950346>", 26: "<:castle26:1512573824086835280>"}
            sort_priority = {1: 0, 4: 1, 12: 2, 3: 3, 22: 4, 23: 5, 24: 7, 26: 6}
            
            if outposts:
                outposts.sort(key=lambda x: (sort_priority.get(int(x.get('type', 99)), 99), x.get('world_id', 0)))

            collected = full_json.get('collected_at', discord.utils.utcnow())
            if not isinstance(collected, datetime):
                collected = discord.utils.utcnow()
            ts = int(collected.timestamp())

            embed_title = t(langue, "prof_joueur_title", n=data.get('name', player), defaut=f"<:players:1512504277392953426> Profil de {data.get('name', player)}")
            embed = discord.Embed(title=embed_title, color=self.clr_joueur)
            
            embed.description = f"{lbl_date} <t:{ts}:F> (<t:{ts}:R>)"
            
            info_title = t(langue, "prof_info_title", defaut="<:Information:1533430015264555099> Informations Générales")
            unk_id = t(langue, "prof_unknown", defaut="Inconnu")
            
            status_txt = t(langue, "prof_status_combat", defaut="⚔️ **Statut :** Attaque possible")
            if peace and peace != "null":
                try:
                    dt_peace = datetime.fromisoformat(peace.replace('Z', '+00:00'))
                    if dt_peace > discord.utils.utcnow():
                        ts_peace = int(dt_peace.timestamp())
                        status_txt = t(langue, "prof_status_peace", tp=ts_peace, defaut=f"<:peace:1512503935892586566> **Colombe :** Jusqu'au <t:{ts_peace}:R> (<t:{ts_peace}:t>)")
                except: pass

            info_val = (
                f"<:lvl:1512571152524906596> **Niveau :** {data.get('level', 0)} (Lég. {data.get('legendary_level', 0)})\n"
                f"<:listitem:1512573892596858960> **ID Joueur :** `{data.get('player_id', unk_id)}`\n"
                f"{status_txt}"
            )
            embed.add_field(name=info_title, value=info_val, inline=True)
            
            rank_title = t(langue, "prof_rank_title", defaut="<:empirerankings:1512574698301423847> Statistiques")
            rank_desc = (
                f"<:might:1512574615422107818> **Puissance :** {main_pts:,} (Max: `{might_all_time:,}`)\n"
                f"<:honor2:1512573861521260544> **Honneur :** {honor_pts:,} (Max: `{max_honor:,}`)\n"
                f"<:loot:1512439015570276553> **Pillage :** {loot_current:,} (Max: `{loot_all_time:,}`)"
            ).replace(",", " ") 
            
            embed.add_field(name=rank_title, value=rank_desc, inline=True)
            
            alli_title = t(langue, "prof_alli_title", defaut="<:alliance_icon:1512574688415580242> Alliance")
            embed.add_field(name=alli_title, value=f"{alliance_display}", inline=False)
            
            if outposts:
                coords_txt = ""
                for op in outposts[:10]:
                    emoji = type_emojis.get(int(op.get('type', 99)), "?")
                    w_emoji = op.get('world_emoji', "🗺️") 
                    
                    c_name = op.get('custom_name')
                    display_name = f"**{c_name}**" if c_name else f"*{op['type_label']}*"
                    
                    coords_txt += f"{w_emoji} [{emoji}] {display_name} ➔ `{op['coords_x']}:{op['coords_y']}`\n"
                    
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
            0: "<:dungeon0:1512573840704671775>",
            1: "<:dungeon1:1512573842277794062>",
            2: "<:dungeon2:1512573843267518546>",
            3: "<:dungeon3:1512573844538396692>",
            4: "<:dungeon4:1512573845737963722>"
        }

        
        txt_unk_world = t(langue, "prof_world_unknown", defaut="Monde inconnu")
        txt_unk_castle = t(langue, "prof_castle_unknown", defaut="Type inconnu")

        from urllib.parse import quote
        safe_name = quote(str(player_name))
        search_url = f"{api_url}/players/{safe_name}"
        
        session = self.bot.session
        if not session: return None
            
        try:
            async with session.get(search_url, headers=headers, timeout=15) as response:
                if response.status != 200: return None
                basic_info = await response.json()
                if isinstance(basic_info, list):
                    if not basic_info: return None
                    basic_info = basic_info[0]
                    
            player_id = basic_info.get('player_id')
            if not player_id: return None
            
            stats_url = f"{api_url}/statistics/ranking/player/{player_id}"
            async with session.get(stats_url, headers=headers, timeout=15) as stats_response:
                stats_data = {}
                if stats_response.status == 200: stats_data = await stats_response.json()

            castle_names_map = {}
            search_castles_url = f"{api_url}/castle/search/{safe_name}"
            
            try:
                async with session.get(search_castles_url, headers=headers, timeout=10) as c_response:
                    if c_response.status == 200:
                        c_data = await c_response.json()
                        if isinstance(c_data, list):
                            
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
                                    pass
                                return None, None

                            tasks = []
                            for c in c_data:
                                c_id = c.get('id')
                                k_id = c.get('kingdomId')
                                cx = c.get('positionX')
                                cy = c.get('positionY')
                                
                                if c_id is not None and k_id is not None and cx is not None and cy is not None:
                                    tasks.append(fetch_castle_name(c_id, k_id, int(cx), int(cy)))
                            
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
                        'world_emoji': world_emojis.get(0, "?"),
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
                        'world_id': w_id, 'coords_x': cx, 'coords_y': cy,
                        'type': t_id, 'world_label': worlds.get(w_id, f"{txt_unk_world} ({w_id})"),
                        'world_emoji': world_emojis.get(w_id, "?"),
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
                        'world_emoji': world_emojis.get(w_id, "?"),
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

            if isinstance(collected_time, str):
                try: collected_time = datetime.fromisoformat(collected_time.replace('Z', '+00:00'))
                except: collected_time = discord.utils.utcnow()
            elif not collected_time:
                collected_time = discord.utils.utcnow()
                
            ts = int(collected_time.timestamp())
            suffixe_cache = " *(Plan B activé)*" if not is_live else ""
            str_date_header = f"{lbl_date} <t:{ts}:F> (<t:{ts}:R>){suffixe_cache}\n\n"

            embeds = []
            rank_emojis = {0: "<:0_:1512574737677684818>", 1: "<:1_:1512574739208470640>", 2: "<:2_:1512574740915818527>", 3: "<:3_:1512574742245412874>", 4: "<:4_:1512574743369224303>", 5: "<:5_:1512574744501817515>", 6: "<:6_:1512574745617498172>", 7: "<:7_:1512574746989039839>", 8: "<:8_:1512574748356251691>", 9: "<:9_:1512574749430120519>"}
            chunk_size = 15
            nb_pages = max(1, (len(members) - 1) // chunk_size + 1)

            for i in range(0, len(members), chunk_size):
                chunk = members[i:i+chunk_size]
                page_actuelle = (i // chunk_size) + 1
                
                embed_title = t(langue, "prof_alli_embed_title", a=alliance_name, defaut=f"<:alliance_icon:1512574688415580242> Alliance : {alliance_name}")
                embed = discord.Embed(title=embed_title, color=self.clr_alliance)
                embed.description = str_date_header.strip()
                
                info_title = t(langue, "prof_info_title", defaut="<:Information:1533430015264555099> Informations")
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
    # 📜 COMMANDE : HISTORY
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
            {"key": "pseudo", "title": t(langue, "prof_hist_cat_pseudo", defaut="Historique des Pseudos"), "emoji": "<:listitem:1512573892596858960>", "json_key": "updates"},
            {"key": "alliance", "title": t(langue, "prof_hist_cat_alliance", defaut="Historique des Alliances"), "emoji": "<:alliance_icon:1512574688415580242>", "json_key": "updates"},
            {"key": "position", "title": t(langue, "prof_hist_cat_position", defaut="Historique des Positions"), "emoji": "<:compass:1512504625364729987>", "json_key": "movements"}
        ]

        has_any_data = False
        unk_val = t(langue, "prof_hist_unknown", defaut="*Inconnu*")
        no_alli_val = t(langue, "prof_hist_no_alli", defaut="*Sans alliance*")

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
                    lines.append(f"<:moove:1512574624112578580> {d} : `{x_old}:{y_old}` ➔ `{x_new}:{y_new}`")

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
    # 📈 COMMANDE : HISTORIQUE ALLIANCE MIGHT
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
    # COMMANDE : ALLIANCE PROPERTY
    # ========================================================
    @app_commands.command(name="alliance_property", description="Displays all properties (Cities, Towers, Monuments, Labs) of an alliance")
    @app_commands.describe(alliance_name="Alliance name")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def alliance_property(self, interaction: discord.Interaction, alliance_name: str):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

        # --- 1. RECHERCHE DE L'ID DE L'ALLIANCE ---
        safe_alliance = quote(alliance_name)
        url_search = f"https://api.gge-tracker.com/api/v1/alliances/name/{safe_alliance}"
        
        async with self.bot.session.get(url_search, headers=headers, timeout=10) as r:
            if r.status != 200:
                return await interaction.followup.send(t(langue, "cmd_prop_not_found", defaut=f"❌ Alliance **{alliance_name}** introuvable sur l'API."))
            data = await r.json()
            if not data:
                return await interaction.followup.send(t(langue, "cmd_prop_not_found", defaut=f"❌ Alliance **{alliance_name}** introuvable sur l'API."))
            
            target = data[0] if isinstance(data, list) else data
            alliance_id = target.get("alliance_id") or target.get("id")
            nom_officiel = target.get("alliance_name", alliance_name)

        if not alliance_id:
            return await interaction.followup.send(t(langue, "cmd_prop_not_found", defaut=f"❌ ID de l'alliance **{alliance_name}** introuvable."))

        # --- 2. RÉCUPÉRATION DE LA CARTOGRAPHIE ---
        url_carto = f"https://api.gge-tracker.com/api/v1/cartography/id/{alliance_id}"
        async with self.bot.session.get(url_carto, headers=headers, timeout=15) as r:
            if r.status != 200:
                return await interaction.followup.send(t(langue, "cmd_prop_api_err", defaut="❌ Erreur lors de la récupération des données cartographiques."))
            carto_data = await r.json()

        if not carto_data:
            return await interaction.followup.send(t(langue, "cmd_prop_empty", defaut=f"📭 L'alliance **{nom_officiel}** ne possède aucune propriété spéciale."))

        # --- 3. CONFIGURATION DES DONNÉES ---
        PROP_TYPES = {
            3: {"name": t(langue, "prop_capital", defaut="Capitale"), "emoji": "<:castle3:1512573819313979544>"},
            22: {"name": t(langue, "prop_city", defaut="Cité Marchande"), "emoji": "<:castle22:1512573821520183347>"},
            23: {"name": t(langue, "prop_tower", defaut="Tour Royale"), "emoji": "<:castle23:1512573823118086174>"},
            26: {"name": t(langue, "prop_monument", defaut="Monument"), "emoji": "<:castle26:1512573824086835280>"},
            28: {"name": t(langue, "prop_lab", defaut="Laboratoire"), "emoji": "<:castle28:1512573825299251351>"}
        }
        
        REALM_NAMES = {
            0: f"<:dungeon0:1512573840704671775> {t(langue, 'realm_empire', defaut='__Grand Empire__')}",
            1: f"<:dungeon1:1512573842277794062> {t(langue, 'realm_sands', defaut='__Sables Brûlants__')}",
            2: f"<:dungeon2:1512573843267518546> {t(langue, 'realm_glacier', defaut='__Glacier Éternel__')}",
            3: f"<:dungeon3:1512573844538396692> {t(langue, 'realm_peaks', defaut='__Pics du Feu__')}",
            4: f"<:dungeon4:1512573845737963722> {t(langue, 'realm_storms', defaut='__Îles Orageuses__')}"
        }

        # Initialisation du dictionnaire avec un ordre fixe
        proprietes_par_monde = {name: [] for name in REALM_NAMES.values()}

        # --- 4. PARCOURS ET TRI DES JOUEURS ---
        for player in carto_data:
            p_name = player.get("name", "Inconnu")
            
            # Traitement des châteaux dans le Grand Empire (index 0)
            for c in player.get("castles", []):
                if len(c) >= 3 and c[2] in PROP_TYPES:
                    proprietes_par_monde[REALM_NAMES[0]].append({
                        "type": c[2], "x": c[0], "y": c[1], "player": p_name
                    })
                    
            # Traitement des châteaux dans les autres mondes
            for cr in player.get("castles_realm", []):
                if len(cr) >= 4 and cr[3] in PROP_TYPES:
                    r_id = cr[0]
                    r_name = REALM_NAMES.get(r_id, f"Monde {r_id}")
                    if r_name not in proprietes_par_monde:
                        proprietes_par_monde[r_name] = []
                    
                    proprietes_par_monde[r_name].append({
                        "type": cr[3], "x": cr[1], "y": cr[2], "player": p_name
                    })

        # --- 5. CONSTRUCTION DE L'EMBED (PAGINATION AUTO SI TROP LONG) ---
        embeds = []
        titre_base = t(langue, "cmd_prop_embed_title", alliance=nom_officiel, defaut=f"🏰 Propriétés de {nom_officiel}")
        current_embed = discord.Embed(title=titre_base, color=self.clr_alliance_property)
        char_count = len(current_embed.title)
        total_props = 0

        for monde, props in proprietes_par_monde.items():
            if not props:
                continue
                
            # Tri par type de propriété puis par nom de joueur
            props.sort(key=lambda p: (p["type"], p["player"].lower()))
            
            chunk = ""
            field_count = 0
            
            for p in props:
                info = PROP_TYPES[p["type"]]
                ligne = f"{info['emoji']} **{info['name']}** | {p['player']} `({p['x']}:{p['y']})`\n"
                total_props += 1
                
                # Si le champ dépasse la limite Discord de 1024 caractères
                if len(chunk) + len(ligne) > 1024:
                    current_embed.add_field(name=monde if field_count == 0 else f"{monde} (suite)", value=chunk, inline=False)
                    char_count += len(monde) + len(chunk)
                    chunk = ligne
                    field_count += 1
                    
                    # Si l'embed atteint sa limite max, on crée une nouvelle page
                    if len(current_embed.fields) >= 25 or char_count > 5000:
                        embeds.append(current_embed)
                        current_embed = discord.Embed(title=f"{titre_base} (Suite)", color=discord.Color.from_rgb(255, 215, 0))
                        char_count = len(current_embed.title)
                else:
                    chunk += ligne
                    
            # Ajout du dernier morceau de texte restant
            if chunk:
                current_embed.add_field(name=monde if field_count == 0 else f"{monde} (suite)", value=chunk, inline=False)
                char_count += len(monde) + len(chunk)
                if len(current_embed.fields) >= 25 or char_count > 5000:
                    embeds.append(current_embed)
                    current_embed = discord.Embed(title=f"{titre_base} (Suite)", color=discord.Color.from_rgb(255, 215, 0))
                    char_count = len(current_embed.title)

        if len(current_embed.fields) > 0 and current_embed not in embeds:
            embeds.append(current_embed)

        if total_props == 0:
            msg_empty = t(langue, "cmd_prop_none", defaut=f"📭 L'alliance **{nom_officiel}** ne possède aucune propriété spéciale (Capitale, Tour du Roi, Monument, Labo).")
            return await interaction.followup.send(msg_empty)

        # Ajout du footer et envoi (avec la pagination si +1 page)
        for emb in embeds:
            await setup_embed_footer(emb, interaction, langue)

        if len(embeds) > 1:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)
        else:
            await interaction.followup.send(embed=embeds[0])

    # ========================================================
    # 🕊️ COMMANDE : DOVE
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
    # 📜 COMMANDE : DESCRIPTION ALLIANCE (API GGE Tracker)
    # ========================================================
    @app_commands.command(name="alliance_description", description="View the history of the last 7 wall changes for an alliance")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def alliance_description(self, interaction: discord.Interaction, alliance_name: str):
        try: 
            await interaction.response.defer(thinking=True)
        except: 
            return

        langue, serveur = await get_server_config(interaction)
        logger.info(f"📜 [Description Alliance] Recherche historique API pour '{alliance_name}' par {interaction.user.name}")

        headers = await get_api_headers(interaction)
        session = self.bot.session
        if not session: 
            return await interaction.followup.send(t(langue, "prof_desc_err_tech", defaut="❌ Erreur système (Session fermée)."))

        safe_alliance = quote(str(alliance_name))
        alliance_id = None

        # --- 1. RECHERCHE DE L'ID (VIA API NAME) ---
        try:
            search_url = f"https://api.gge-tracker.com/api/v1/alliances/name/{safe_alliance}"
            async with session.get(search_url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data1 = await resp.json()
                    target = data1[0] if isinstance(data1, list) and data1 else data1
                    if target:
                        alliance_id = target.get('alliance_id') or target.get('id') or target.get('allianceId')
        except Exception as e:
            logger.warning(f"[Description Alliance] API /name/ injoignable pour {alliance_name} : {e}")

        # --- 2. RECHERCHE DE L'ID (PLAN B : SCAN LOCAL) ---
        if not alliance_id:
            try:
                player_files = list((BASE_DATA_PATH / 'server_scans' / serveur).rglob('server_*.json'))
                if player_files:
                    latest = max(player_files, key=lambda p: p.stat().st_mtime)
                    
                    def _load_local_json():
                        with open(latest, 'r', encoding='utf-8') as f:
                            return json.load(f).get('players', {})
                            
                    local_data = await asyncio.to_thread(_load_local_json)
                    
                    for p_info in local_data.values():
                        a_obj = p_info.get('alliance')
                        a_name = a_obj.get('name') if isinstance(a_obj, dict) else (p_info.get('alliance_name') or a_obj)
                        if a_name and str(a_name).lower() == alliance_name.lower():
                            aid = p_info.get('allianceId') or p_info.get('alliance_id')
                            if not aid and isinstance(a_obj, dict): 
                                aid = a_obj.get('allianceId') or a_obj.get('alliance_id')
                            if aid:
                                alliance_id = str(aid)
                                break
            except Exception as e:
                logger.error(f"[Description Alliance] Erreur Plan B (Scan local) pour {alliance_name} : {e}")

        if not alliance_id:
            msg = t(langue, "prof_desc_not_found", a=alliance_name, defaut=f"❌ Impossible de trouver l'ID de l'alliance **{alliance_name}**.")
            return await interaction.followup.send(msg)

        # --- 3. RÉCUPÉRATION DES MURS VIA L'ID TROUVÉ ---
        api_url = f"https://api-beta.gge-tracker.com/api/v1/alliances/id/{alliance_id}"

        try:
            async with session.get(api_url, headers=headers, timeout=10) as response:
                if response.status != 200:
                    return await interaction.followup.send(t(langue, "prof_desc_not_found", a=alliance_name, defaut="❌ Impossible de trouver les données de cette alliance dans le Tracker."))
                data = await response.json()
                if isinstance(data, list) and data:
                    data = data[0]
        except Exception as e:
            logger.error(f"❌ Erreur API GGE Tracker (Murs) : {e}")
            return await interaction.followup.send(t(langue, "prof_desc_err_tech", defaut="❌ Erreur technique lors de la connexion à l'API."))

        # --- 4. TRAITEMENT DES DONNÉES ET GESTION DES MURS VIDES ---
        nom_alliance = data.get("alliance_name", alliance_name)
        desc_actuelle = data.get("description")
        historique = data.get("description_history") or []

        # 🛑 Si l'API renvoie des champs totalement vides, le serveur n'est probablement pas encore supporté pour les murs
        if not desc_actuelle and not historique:
            msg_unsupported = t(
                langue, 
                "prof_desc_unsupported", 
                srv=serveur, 
                defaut=f"⚠️ Cette commande ne fonctionne pas encore sur le serveur demandé (**{serveur}**), ou a rencontré un problème pour cette alliance."
            )
            return await interaction.followup.send(msg_unsupported)

        def clean_desc(text):
            if not text: return "*(Mur vide ou non renseigné)*"
            return text.replace('<br />', '\n').replace('<br/>', '\n').replace('<br>', '\n').strip()

        # --- 5. CRÉATION DE L'EMBED (Recherche de la date la plus récente) ---
        dates_trouvees = []
        if data.get("updated_at"): dates_trouvees.append(data.get("updated_at"))
        if data.get("updatedAt"): dates_trouvees.append(data.get("updatedAt"))
        for entry in historique:
            if entry.get("created_at"): dates_trouvees.append(entry.get("created_at"))

        # On extrait la date la plus récente parmi toutes celles trouvées
        if dates_trouvees:
            latest_str = max(dates_trouvees)
            if latest_str.endswith('Z'): 
                latest_str = latest_str[:-1] + '+00:00'
            try:
                actualisation_dt = datetime.fromisoformat(latest_str)
            except:
                actualisation_dt = discord.utils.utcnow()
        else:
            # Plan de secours : on utilise la fonction _get_api_timestamp de ton fichier
            actualisation_dt = _get_api_timestamp(data)

        ts_act = int(actualisation_dt.timestamp())
        
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
        str_date_header = f"{lbl_date} <t:{ts_act}:F> (<t:{ts_act}:R>)\n\n"
        desc_i18n = t(langue, "prof_desc_embed_desc", l=len(historique)+1, defaut="Analyse du mur d'alliance depuis le GGE Tracker.")

        embed = discord.Embed(
            title=t(langue, "prof_desc_embed_title", a=nom_alliance.upper(), defaut=f"Archives Alliance : {nom_alliance.upper()}"),
            description=str_date_header + desc_i18n,
            color=getattr(self, "clr_alliance", discord.Color.blue())
        )

        # A) Champ de la description Actuelle
        curr_txt = t(langue, "prof_desc_current", defaut="📝 Description Actuelle")
        valeur_actuelle = f">>> {clean_desc(desc_actuelle)}"
        if len(valeur_actuelle) > 1020: valeur_actuelle = valeur_actuelle[:1017] + "..."
        embed.add_field(name=curr_txt, value=valeur_actuelle, inline=False)

        # B) Champs de l'Historique (Anciennes descriptions)
        if historique:
            historique_trie = sorted(historique, key=lambda x: x.get("created_at", ""), reverse=True)

            for entry in historique_trie[:6]:
                try:
                    date_brute = entry.get('created_at', '')
                    if date_brute.endswith('Z'):
                        date_brute = date_brute[:-1] + '+00:00'
                        
                    dt_obj = datetime.fromisoformat(date_brute)
                    ts_change = int(dt_obj.timestamp())
                    
                    header_title = t(langue, "prof_desc_version_api", ts=ts_change, defaut=f"🕰️ Ancienne version (Remplacée le <t:{ts_change}:d> à <t:{ts_change}:t>)")
                    
                    texte_affiche = clean_desc(entry.get('old_description', ''))
                    valeur_champ = f">>> {texte_affiche}"
                    if len(valeur_champ) > 1020: valeur_champ = valeur_champ[:1017] + "..."
                    
                    embed.add_field(name=header_title, value=valeur_champ, inline=False)
                except Exception as e:
                    logger.warning(f"Erreur de parsing date historique pour {nom_alliance} : {e}")
                    continue
        else:
            embed.set_footer(text=t(langue, "prof_desc_no_history", defaut="Aucun historique d'ancien mur n'a été trouvé pour le moment."))

        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(ProfilsCog(bot))