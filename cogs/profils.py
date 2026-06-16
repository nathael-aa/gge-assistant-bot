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
    joueur_autocomplete, 
    alliance_autocomplete, 
    format_num, 
    get_discord_timestamp, 
    BOT_VERSION,
    setup_embed_footer,
    PaginationView
)

class HistoriqueView(discord.ui.View):
    def __init__(self, embeds_dict, interaction: discord.Interaction):
        super().__init__(timeout=300)
        self.embeds_dict = embeds_dict
        self.interaction = interaction
        
        # On définit la catégorie par défaut (Pseudos)
        self.current_cat = "pseudo"
        self.current_page = 0
        
        # On ajuste dynamiquement les styles au démarrage
        self._update_navigation()

    def _update_navigation(self):
        # On vide les boutons pour reconstruire la vue proprement
        self.clear_items()
        
        # Ligne 0 : Mise en valeur du bouton de la catégorie active
        self.btn_pseudo.style = discord.ButtonStyle.primary if self.current_cat == "pseudo" else discord.ButtonStyle.secondary
        self.btn_alliance.style = discord.ButtonStyle.primary if self.current_cat == "alliance" else discord.ButtonStyle.secondary
        self.btn_position.style = discord.ButtonStyle.primary if self.current_cat == "position" else discord.ButtonStyle.secondary
        
        self.add_item(self.btn_pseudo)
        self.add_item(self.btn_alliance)
        self.add_item(self.btn_position)
        
        # Ligne 1 : On n'affiche les flèches QUE si la catégorie active a plusieurs pages
        if len(self.embeds_dict[self.current_cat]) > 1:
            self.add_item(self.btn_prev)
            self.add_item(self.btn_next)

    async def _switch_category(self, interaction: discord.Interaction, cat: str):
        self.current_cat = cat
        self.current_page = 0
        self._update_navigation()
        await interaction.response.edit_message(embed=self.embeds_dict[self.current_cat][0], view=self)

    @discord.ui.button(label="Pseudos", emoji="<:renames:1512574708913143858>", row=0)
    async def btn_pseudo(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "pseudo")

    @discord.ui.button(label="Alliances", emoji="<:alliances:1512574688415580242>", row=0)
    async def btn_alliance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "alliance")

    @discord.ui.button(label="Mouvements", emoji="<:compass:1512504625364729987>", row=0)
    async def btn_position(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "position")

    # Boutons de sous-pagination (Flèches secondaires sur la ligne 1)
    @discord.ui.button(label="◀️", style=discord.ButtonStyle.blurple, row=1)
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        await interaction.response.edit_message(embed=self.embeds_dict[self.current_cat][self.current_page], view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.blurple, row=1)
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.embeds_dict[self.current_cat]) - 1, self.current_page + 1)
        await interaction.response.edit_message(embed=self.embeds_dict[self.current_cat][self.current_page], view=self)

logger = logging.getLogger("GGE_Bot")

class ProfilsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bdd_chemin = "/app/data/bdd_items_gge.json"
        self.serveur_par_defaut = "E4K_FR1"
        
        # 🎨 PALETTE CYAN & BLEU PÉTROLE
        self.clr_joueur      = discord.Color.from_rgb(0, 163, 204)  # 👑 Cyan Éclatant
        self.clr_alliance    = discord.Color.from_rgb(0, 115, 153)  # 🛡️ Bleu Pétrole Médium
        self.clr_historique  = discord.Color.from_rgb(0, 77, 102)   # 📜 Bleu Pétrole Sombre
        self.clr_alliance_pp = discord.Color.from_rgb(0, 140, 186)  # 📈 Bleu Océan
        self.clr_colombe     = discord.Color.from_rgb(0, 180, 216)  # 🕊️ Cyan Givré / Colombe

    # ========================================================
    # 👑 COMMANDE : JOUEUR (100% ASYNCHRONE)
    # ========================================================
    @app_commands.command(name="joueur", description="Profil détaillé d'un joueur")
    @app_commands.autocomplete(nom=joueur_autocomplete)
    async def joueur(self, interaction: discord.Interaction, nom: str):
        try: 
            await interaction.response.defer(thinking=True)
        except: 
            return
        
        logger.info(f"👤 [Joueur] Consultation par {interaction.user.name} pour le joueur : {nom}")
        
        try:
            full_json = await self._get_player_full_data(nom)
            
            if not full_json:
                await interaction.followup.send(f"<:error:1512505075220611172> Joueur **{nom}** introuvable (API injoignable ou joueur inexistant).")
                return

            data = full_json.get('parsed_data', {})
            if not data:
                await interaction.followup.send("<:error:1512505075220611172> Données vides.")
                return

            main_pts = int(data.get('main_points', 0))
            honor_pts = int(data.get('honor', 0))

            alliance_info = data.get('alliance', {})
            rank_id = alliance_info.get('rank')
            ranks_map = {0: "Chef", 1: "Représentant", 2: "Maréchal", 3: "Trésorier", 4: "Diplomate", 5: "Recruteur", 6: "Général", 7: "Sergent", 8: "Membre", 9: "Novice"}
            
            role_txt = f" ({ranks_map[rank_id]})" if rank_id in ranks_map else (f" (Grade {rank_id})" if rank_id is not None else "")
            alliance_name = alliance_info.get('name', 'Sans alliance')
            alliance_display = f"**{alliance_name}**{role_txt}" if alliance_name != 'Sans alliance' else "**Sans alliance**"

            outposts = data.get('outposts', [])
            type_emojis = {1: "<:squarecastle:1512573757426892911>", 3: "<:squarecapital:1512573756243972237>", 4: "<:squareoutpost:1512573761583579228>", 10: "<:date:1512573832375042340>", 12: "<:dungeons:1512574697223753798>", 22: "<:castle22:1512573821520183347>", 23: "<:castle23:1512573823118086174>", 24: "<:aquamarine_16:1512573724786950346>", 26: "<:castle26:1512573824086835280>"}
            sort_priority = {1: 0, 4: 1, 12: 2, 3: 3, 22: 4, 23: 5, 24: 7, 26: 6}
            
            if outposts:
                outposts.sort(key=lambda x: (sort_priority.get(int(x.get('type', 99)), 99), x.get('world_id', 0)))

            embed = discord.Embed(title=f"<:players:1512504277392953426> Profil de {data.get('name', nom)}", color=self.clr_joueur, timestamp=discord.utils.utcnow())
            embed.add_field(name="<:info:1512502828193808537> Informations", value=f"<:lvl:1512571152524906596> {data.get('level', 0)} (Lég. {data.get('legendary_level', 0)})\n<:renames:1512574708913143858> `{data.get('player_id', 'Inconnu')}`", inline=True)
            embed.add_field(name="<:empirerankings:1512574698301423847> Classement", value=f"<:might:1512574615422107818> **Puiss.** : {main_pts:,}\n<:honor2:1512573861521260544> **Honneur** : {honor_pts:,}", inline=True)
            embed.add_field(name="<:alliances:1512574688415580242> Alliance", value=alliance_display, inline=False)
            
            if outposts:
                coords_txt = ""
                for op in outposts[:10]:
                    emoji = type_emojis.get(int(op.get('type', 99)), "📌")
                    coords_txt += f"{emoji} **{op['type_label']}** ({op['world_label']}) ➔ `{op['coords_x']}:{op['coords_y']}`\n"
                if len(outposts) > 10: 
                    coords_txt += f"*... et {len(outposts) - 10} autres positions.*\n"
                embed.add_field(name=f"<:compass:1512504625364729987> Positions ({len(outposts)})", value=coords_txt[:1024], inline=False)

            vassals = data.get('vassal_villages', [])
            if vassals:
                v_glace = len([v for v in vassals if v.get('world_id') == 2])
                v_sable = len([v for v in vassals if v.get('world_id') == 1])
                v_pic = len([v for v in vassals if v.get('world_id') == 3])
                v_orage = len([v for v in vassals if v.get('world_id') == 4])
                v_emp = len([v for v in vassals if v.get('world_id') == 0])
                v_txt = f"<:dungeon2:1512573843267518546> Glace: **{v_glace}** | <:dungeon1:1512573842277794062> Sables: **{v_sable}** | <:dungeon3:1512573844538396692> Pics: **{v_pic}** | <:dungeon0:1512573840704671775> Empires: **{v_emp}** | <:dungeon4:1512573845737963722> Iles: **{v_orage}**"
                v_coords = "\n".join([f"<:date:1512573832375042340> VR ({v['world_label']}) ➔ `{v['coords_x']}:{v['coords_y']}`" for v in vassals[:5]])
                embed.add_field(name=f"<:date:1512573832375042340> Villages à Ressources ({len(vassals)})", value=f"{v_txt}\n\n{v_coords}"[:1024], inline=False)
            
            collected = full_json.get('collected_at')
            if collected:
                ts_r = get_discord_timestamp(collected, 'R')
                ts_t = get_discord_timestamp(collected, 't')
                embed.add_field(name="⏱️ Source des données", value=f"Relevé API en direct {ts_r} (*{ts_t}*)", inline=False)

            setup_embed_footer(embed, interaction)
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"[Profils - Joueur] Erreur fatale : {traceback.format_exc()}")
            try: 
                await interaction.followup.send(f"<:error:1512505075220611172> Erreur système interne.")
            except: 
                pass

    # ========================================================
    # 🛰️ MÉTHODE INTERNE : COLLECTEUR DE DONNÉES JOUEUR
    # ========================================================
    async def _get_player_full_data(self, player_name: str):
        server = 'FR1'
        config_path = Path('config.json')
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    server = json.load(f).get('server', 'FR1')
            except: pass
                
        api_url = "https://api.gge-tracker.com/api/v1"
        headers = {
            "gge-server": server,
            "accept": "application/json",
            "User-Agent": "Mozilla/5.0 GGE-Assistant/5.0"
        }
        
        castle_types = {
            1: "Château Principal", 3: "Capitale", 4: "Avant-Poste",
            10: "Village à Ressource", 12: "Château Secondaire",
            22: "Cité Marchande", 23: "Tour Royale", 24: "Ile aux Ressources", 26: "Monument"
        }
        worlds = {
            0: "Le Grand Empire", 1: "Les Sables Brûlants",
            2: "Glacier Éternel", 3: "Pics du Feu", 4: "Les Îles Orageuses"
        }

        safe_name = quote(str(player_name))
        search_url = f"{api_url}/players/{safe_name}"
        
        # ⚡ Optimisation globale de session
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
            
            outposts = []
            vassal_villages = []
            
            for c in stats_data.get('castles', []):
                if len(c) >= 3:
                    t_id = c[2]
                    struct = {
                        'world_id': 0, 'coords_x': c[0], 'coords_y': c[1],
                        'type': t_id, 'world_label': worlds.get(0, "Le Grand Empire"),
                        'type_label': castle_types.get(t_id, f"Type inconnu ({t_id})")
                    }
                    if t_id == 10: vassal_villages.append(struct)
                    else: outposts.append(struct)
                        
            for c in stats_data.get('castles_realm', []):
                if len(c) >= 4:
                    w_id, t_id = c[0], c[3]
                    struct = {
                        'world_id': w_id, 'coords_x': c[1], 'coords_y': c[2],
                        'type': t_id, 'world_label': worlds.get(w_id, f"Monde inconnu ({w_id})"),
                        'type_label': castle_types.get(t_id, f"Type inconnu ({t_id})")
                    }
                    if t_id == 10: vassal_villages.append(struct)
                    else: outposts.append(struct)

            for v in stats_data.get('villages', []):
                if len(v) >= 3:
                    w_id = v[0]
                    struct = {
                        'world_id': w_id, 'coords_x': v[1], 'coords_y': v[2],
                        'type': 10, 'world_label': worlds.get(w_id, f"Monde inconnu ({w_id})"),
                        'type_label': "Village à Ressource"
                    }
                    vassal_villages.append(struct)

            parsed_data = {
                'player_id': basic_info.get('player_id'),
                'name': basic_info.get('player_name'),
                'level': basic_info.get('level', 0) or stats_data.get('level', 0),
                'legendary_level': basic_info.get('legendary_level', 0) or stats_data.get('legendary_level', 0),
                'honor': basic_info.get('honor', 0),
                'main_points': stats_data.get('might_current', 0),
                'alliance': {
                    'id': basic_info.get('alliance_id'),
                    'name': basic_info.get('alliance_name') or stats_data.get('alliance_name') or 'Sans alliance',
                    'rank': basic_info.get('alliance_rank') or stats_data.get('alliance_rank')
                },
                'outposts': outposts,
                'vassal_villages': vassal_villages 
            }
            
            return {
                'collected_at': datetime.now().isoformat(),
                'player_name': basic_info.get('player_name'),
                'server': server,
                'parsed_data': parsed_data
            }
            
        except Exception as e:
            logger.error(f"Erreur API Joueur pour {player_name}: {e}")
            return None

    # ========================================================
    # 🛡️ COMMANDE ALLIANCE (100% ASYNCHRONE)
    # ========================================================
    @app_commands.command(name="alliance", description="Profil détaillé d'une alliance (Rapide & Paginé)")
    @app_commands.autocomplete(nom=alliance_autocomplete)
    async def alliance(self, interaction: discord.Interaction, nom: str):
        try: 
            await interaction.response.defer(thinking=True)
        except: 
            return
        
        logger.info(f"🛡️ [Alliance] Consultation par {interaction.user.name} pour : {nom}")
        
        try:
            is_live = False
            target_alliance_id = None
            alliance_name = nom
            total_might = total_fame = total_honor = 0
            leader_name = "Inconnu"
            members = []
            
            # --- PLAN A : L'API EN DIRECT ---
            try:
                api_data = await self._get_alliance_full_data(nom)
                
                if api_data and 'parsed_data' in api_data:
                    is_live = True
                    parsed = api_data['parsed_data']
                    target_alliance_id = parsed.get('alliance_id')
                    alliance_name = parsed.get('name', nom)
                    leader_name = parsed.get('leader', 'Inconnu')
                    total_might = parsed.get('total_might', 0)
                    total_honor = parsed.get('total_honor', 0)
                    total_fame = parsed.get('total_fame', 0)
                    members = parsed.get('members', [])
            except Exception as e:
                logger.warning(f"[Profils - Alliance] API inaccessible, passage au Plan B... ({e})")

            # --- PLAN B : LE CACHE LOCAL (Sécurisé contre les lags) ---
            local_date = None
            if not is_live:
                player_files = list((BASE_DATA_PATH / 'server_scans').rglob('server_*.json'))
                local_data = {}
                
                if player_files:
                    latest = max(player_files, key=lambda p: p.stat().st_mtime)
                    
                    # 🛠️ Déportation du parsing lourd JSON hors de la boucle principale d'asyncio
                    def _load_local_json():
                        with open(latest, 'r', encoding='utf-8') as f:
                            return json.load(f)
                    
                    full_json = await asyncio.to_thread(_load_local_json)
                    local_data = full_json.get('players', {})
                    local_date = full_json.get('collected_at')

                for p_info in local_data.values():
                    a_obj = p_info.get('alliance')
                    a_name = a_obj.get('name') if isinstance(a_obj, dict) else (p_info.get('alliance_name') or a_obj)
                    if a_name and str(a_name).lower() == nom.lower():
                        aid = p_info.get('allianceId') or p_info.get('alliance_id')
                        if not aid and isinstance(a_obj, dict): aid = a_obj.get('allianceId') or a_obj.get('alliance_id')
                        if aid:
                            target_alliance_id = str(aid)
                            alliance_name = str(a_name)
                            break
                            
                if not target_alliance_id:
                    return await interaction.followup.send(f"<:error:1512505075220611172> Alliance **{nom}** introuvable (ni sur l'API en direct, ni dans le cache local).")

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
                        
                        m_name = p_info.get('name', 'Inconnu')
                        if str(m_rank) in ["0", "1"] and (leader_name == "Inconnu" or str(m_rank) == "0"):
                            leader_name = m_name
                            
                        members.append({
                            "name": m_name, "level": p_info.get('level', 0), "leg_level": p_info.get('legendary_level', 0),
                            "might": m_might, "fame": m_fame, "honor": m_honor, "rank": m_rank
                        })

                members.sort(key=lambda x: (int(x.get('rank', 9)), -x.get('might', 0)))

            if not members:
                return await interaction.followup.send(f"<:error:1512505075220611172> L'alliance **{alliance_name}** semble être une ville fantôme (0 membre).")

            # --- AFFICHAGE PAGINÉ ---
            embeds = []
            rank_emojis = {0: "<:0_:1512574737677684818>", 1: "<:1_:1512574739208470640>", 2: "<:2_:1512574740915818527>", 3: "<:3_:1512574742245412874>", 4: "<:4_:1512574743369224303>", 5: "<:5_:1512574744501817515>", 6: "<:6_:1512574745617498172>", 7: "<:7_:1512574746989039839>", 8: "<:8_:1512574748356251691>", 9: "<:9_:1512574749430120519>"}
            chunk_size = 15
            nb_pages = max(1, (len(members) - 1) // chunk_size + 1)

            for i in range(0, len(members), chunk_size):
                chunk = members[i:i+chunk_size]
                page_actuelle = (i // chunk_size) + 1
                
                embed = discord.Embed(title=f"<:alliances:1512574688415580242> Alliance : {alliance_name}", color=self.clr_alliance, timestamp=discord.utils.utcnow())
                embed.add_field(name="<:info:1512502828193808537> Informations", value=f"**Chef** : <:ggelogo:151257355779262695> {leader_name}\n**Membres** : <:players:1512504277392953426> {len(members)} / 65\n**ID** : `{target_alliance_id}`", inline=True)
                embed.add_field(name="<:stats:1512517930490003726> Statistiques Globales", value=f"<:pp3:1512573741065048236> **Puiss.** : {format_num(total_might)}\n<:glory:1512573856840421386> **Gloire** : {format_num(total_fame)}\n<:honor:1512573860204253214> **Honneur** : {format_num(total_honor)}", inline=True)
                
                memb_txt = ""
                for m in chunk:
                    lvl = m.get('level', 0)
                    leg = m.get('leg_level', m.get('leg', 0))
                    emoji = rank_emojis.get(int(m.get('rank', 9)), "<:players:1512504277392953426>")
                    memb_txt += f"{emoji} **{m.get('name', 'Inconnu')}** ({lvl}/{leg}) ➔ {format_num(m.get('might', 0))} | {format_num(m.get('fame', 0))}\n"
                
                embed.add_field(name=f"<:players:1512504277392953426> Membres (Page {page_actuelle}/{nb_pages})     *PP | Gloire*", value=memb_txt, inline=False)
                
                if not is_live and local_date:
                    ts_r = get_discord_timestamp(local_date, 'R')
                    embed.add_field(name="⏱️ Base de données (Plan B)", value=f"Cache local datant de {ts_r}", inline=False)

                setup_embed_footer(embed, interaction)
                embeds.append(embed)

            if len(embeds) == 1:
                await interaction.followup.send(embed=embeds[0])
            else:
                view = PaginationView(embeds)
                await interaction.followup.send(embed=embeds[0], view=view)

        except Exception as e:
            logger.error(f"[Profils - Alliance] Erreur fatale : {traceback.format_exc()}")
            try: await interaction.followup.send(f"<:error:1512505075220611172> Erreur système interne lors du chargement de l'alliance.")
            except: pass

    # ========================================================
    # 🛰️ MÉTHODE INTERNE : COLLECTEUR DE DONNÉES ALLIANCE
    # ========================================================
    async def _get_alliance_full_data(self, alliance_name: str):
        server = 'E4K_FR1'
        config_path = Path('config.json')
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    server = json.load(f).get('server', 'E4K_FR1')
            except: pass
                
        api_url = "https://api.gge-tracker.com/api/v1"
        headers = {
            "gge-server": server,
            "accept": "application/json",
            "User-Agent": "Mozilla/5.0 GGE-Assistant/5.0"
        }

        safe_name = quote(str(alliance_name))
        search_url = f"{api_url}/alliances/name/{safe_name}"
        
        session = self.bot.session
        if not session: return None

        try:
            async with session.get(search_url, headers=headers, timeout=15) as resp:
                if resp.status != 200: return None
                data1 = await resp.json()
        except Exception as e:
            logger.debug(f"Erreur recherche alliance API : {e}")
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
        leader_name = "Inconnu"

        for m in members:
            rank = m.get('allianceRank', m.get('alliance_rank', m.get('rank', 9)))
            might = int(m.get('might_current', m.get('might', m.get('main_points', 0))))
            honor = int(m.get('honor', 0))
            fame = int(m.get('current_fame', m.get('fame', 0)))
            
            tot_might += might
            tot_honor += honor
            tot_fame += fame
            
            if str(rank) in ["0", "1"]:
                if leader_name == "Inconnu" or str(rank) == "0":
                    leader_name = m.get('player_name', m.get('playerName', m.get('name', 'Inconnu')))

            parsed_members.append({
                'name': m.get('player_name', m.get('playerName', m.get('name', 'Inconnu'))),
                'might': might, 'honor': honor, 'fame': fame, 'level': m.get('level', 0),
                'leg_level': m.get('legendary_level', m.get('legendaryLevel', 0)), 'rank': rank
            })

        parsed_members.sort(key=lambda x: (int(x['rank']), -x['might']))

        parsed_data = {
            'alliance_id': target_alliance.get('alliance_id') or target_alliance.get('allianceId'),
            'name': target_alliance.get('alliance_name') or target_alliance.get('name', 'Inconnue'),
            'members_count': len(parsed_members), 'leader': leader_name, 'total_might': tot_might,
            'total_honor': tot_honor, 'total_fame': tot_fame, 'members': parsed_members,
            'stats_diffs': stats_data.get('diffs', {}),
            'stats_history': {
                'loot': stats_data.get('points', {}).get('player_loot_history', []),
                'might': stats_data.get('points', {}).get('player_might_history', [])
            },
            'pulse': pulse_data
        }

        return {
            'collected_at': datetime.now().isoformat(), 'alliance_name': parsed_data['name'],
            'server': server, 'parsed_data': parsed_data
        }

    # ========================================================
    # 📜 COMMANDE : HISTORIQUE (VERSION BOUTONS THÉMATIQUES)
    # ========================================================
    @app_commands.command(name="historique", description="Affiche le dossier historique complet d'un joueur (Pseudos, Alliances, Positions)")
    @app_commands.autocomplete(joueur=joueur_autocomplete)
    async def historique(self, interaction: discord.Interaction, joueur: str):
        try: 
            await interaction.response.defer(thinking=True)
        except: 
            return
        
        logger.info(f"📜 [Historique] Consultation globale par {interaction.user.name} pour le joueur : {joueur}")

        # 1. Récupération de l'ID interne du joueur
        p_id = None
        try:
            full_json = await self._get_player_full_data(joueur)
            if full_json:
                data = full_json.get('parsed_data', {})
                p_id = str(data.get('player_id', ''))
                if p_id and not p_id.endswith('164'): p_id += '164'
        except: pass

        if not p_id:
            await interaction.followup.send(f"<:error:1512505075220611172> Impossible de trouver l'ID interne de **{joueur}** via l'API.")
            return

        headers = {
            'User-Agent': 'Mozilla/5.0 GGE-Assistant/5.0',
            'accept': 'application/json', 
            'gge-server': 'E4K_FR1'
        }

        # 2. Préparation des requêtes parallèles
        urls_to_fetch = {
            "pseudo": f"https://api.gge-tracker.com/api/v1/updates/players/{p_id}/names",
            "alliance": f"https://api.gge-tracker.com/api/v1/updates/players/{p_id}/alliances",
            "position": f"https://api.gge-tracker.com/api/v1/server/movements?page=1&castleType=1&movementType=3&search={quote(joueur)}&searchType=player"
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

        # Helper pour parser proprement les dates Discord
        def parse_date(iso_str):
            if not iso_str: return "Date inconnue"
            return get_discord_timestamp(iso_str, 'd')

        # 3. Traitement et structuration par dictionnaire thématique
        embeds_dict = {"pseudo": [], "alliance": [], "position": []}
        LINES_PER_PAGE = 12

        categories = [
            {"key": "pseudo", "title": "Historique des Pseudos", "emoji": "<:renames:1512574708913143858>", "json_key": "updates"},
            {"key": "alliance", "title": "Historique des Alliances", "emoji": "<:alliances:1512574688415580242>", "json_key": "updates"},
            {"key": "position", "title": "Historique des Positions", "emoji": "<:compass:1512504625364729987>", "json_key": "movements"}
        ]

        has_any_data = False

        for cat in categories:
            lines = []
            raw_data = results.get(cat["key"], {}).get(cat["json_key"], [])

            for item in raw_data:
                d = parse_date(item.get('date', item.get('created_at')))
                if cat["key"] == "pseudo":
                    old = item.get("old_player_name") or "*Inconnu*"
                    new = item.get("new_player_name") or "*Inconnu*"
                    lines.append(f"<:members:1512573912305766652> {d} : ~~{old}~~ ➔ **{new}**")
                elif cat["key"] == "alliance":
                    old = item.get("old_alliance_name") or "*Sans alliance*"
                    new = item.get("new_alliance_name") or "*Sans alliance*"
                    lines.append(f"<:icon_alliance:1512573872774451210> {d} : *{old}* ➔ **{new}**")
                elif cat["key"] == "position":
                    x_old, y_old = item.get('position_x_old'), item.get('position_y_old')
                    x_new, y_new = item.get('position_x_new'), item.get('position_y_new')
                    lines.append(f"<:UyuPdm57K4WjWIFbWCTzgOUhP0hiydbK:1512574624112578580> {d} : `{x_old}:{y_old}` ➔ `{x_new}:{y_new}`")

            if lines:
                has_any_data = True
                for i in range(0, len(lines), LINES_PER_PAGE):
                    chunk = lines[i:i+LINES_PER_PAGE]
                    page_num = (i // LINES_PER_PAGE) + 1
                    total_pages_cat = (len(lines) + LINES_PER_PAGE - 1) // LINES_PER_PAGE
                    suffixe_titre = f" ({page_num}/{total_pages_cat})" if total_pages_cat > 1 else ""
                    
                    emb = discord.Embed(
                        title=f"{cat['emoji']} {cat['title']} - {joueur}{suffixe_titre}",
                        description="\n".join(chunk),
                        color=self.clr_historique,
                        timestamp=discord.utils.utcnow()
                    )
                    setup_embed_footer(emb, interaction)
                    embeds_dict[cat["key"]].append(emb)
            else:
                # Si aucune donnée, on prépare un embed vide d'affichage par défaut pour cette catégorie
                emb = discord.Embed(
                    title=f"{cat['emoji']} {cat['title']} - {joueur}",
                    description="<:info:1512502828193808537> Aucun historique disponible dans cette catégorie.",
                    color=self.clr_historique,
                    timestamp=discord.utils.utcnow()
                )
                setup_embed_footer(emb, interaction)
                embeds_dict[cat["key"]].append(emb)

        # 4. Finalisation de l'affichage
        if not has_any_data:
            embed_vide = discord.Embed(
                title=f"<:Le_Hraut_Lumbricus_2:1512573890298380388> Dossier Historique - {joueur}",
                description="<:info:1512502828193808537> Aucun historique trouvé pour ce joueur sur l'ensemble des modules.",
                color=self.clr_historique
            )
            setup_embed_footer(embed_vide, interaction)
            await interaction.followup.send(embed=embed_vide)
            return

        # On instancie la vue avec le dictionnaire d'embeds complet
        view = HistoriqueView(embeds_dict, interaction)
        # On affiche la première page de la catégorie sélectionnée par défaut au démarrage ("pseudo")
        await interaction.followup.send(embed=embeds_dict[view.current_cat][0], view=view)

    # ========================================================
    # 📈 COMMANDE : HISTORIQUE ALLIANCE PP
    # ========================================================
    @app_commands.command(name="alliance_pp", description="Historique de la Puissance (PP) d'une alliance sur X jours")
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.describe(jours="Période à analyser en jours (Par défaut: 3, Maximum: 10)")
    async def alliance_pp(self, interaction: discord.Interaction, alliance: str, jours: int = 3):
        try: 
            await interaction.response.defer(thinking=True)
        except: 
            return
        
        logger.info(f"📈 [Alliance PP] Historique demandé par {interaction.user.name} (Alliance: {alliance}, Jours: {jours})")

        jours = max(1, min(10, jours))
        date_limite = discord.utils.utcnow() - timedelta(days=jours)

        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        safe_alliance = quote(alliance)
        
        session = self.bot.session
        if not session: return await interaction.followup.send("<:error:1512505075220611172> Erreur interne : Connexion HTTP indisponible.")

        try:
            search_url = f"https://api.gge-tracker.com/api/v1/alliances/name/{safe_alliance}"
            async with session.get(search_url, headers=headers, timeout=10) as resp:
                if resp.status != 200: return await interaction.followup.send("<:error:1512505075220611172> Impossible de trouver cette alliance.")
                data1 = await resp.json()
                target = data1[0] if isinstance(data1, list) and data1 else data1
                alliance_id = target.get('alliance_id') or target.get('id')
        except Exception:
            return await interaction.followup.send("<:error:1512505075220611172> Erreur de connexion avec l'API GGE-Tracker.")

        if not alliance_id: return await interaction.followup.send(f"<:error:1512505075220611172> Alliance **{alliance}** introuvable.")

        try:
            stats_url = f"https://api.gge-tracker.com/api/v1/statistics/alliance/{alliance_id}"
            async with session.get(stats_url, headers=headers, timeout=15) as resp:
                if resp.status != 200: return await interaction.followup.send("<:error:1512505075220611172> Impossible de télécharger l'historique.")
                stats_data = await resp.json()
        except Exception:
            return await interaction.followup.send("<:error:1512505075220611172> Erreur lors du téléchargement des statistiques.")

        might_history = stats_data.get("points", {}).get("player_might_history", [])
        if not might_history:
            return await interaction.followup.send(f"<:error:1512505075220611172> Aucun historique de puissance disponible pour **{alliance}**.")

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
            return await interaction.followup.send(f"<:error:1512505075220611172> Aucune donnée enregistrée pour **{alliance}** sur les **{jours} derniers jours**.")

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

        stats_txt = (
            f"\n\n**Période** : du {premier_jour} au {dernier_jour}\n"
            f"**PP de Départ** : {format_num(pp_debut)}\n"
            f"**PP Actuels** : {format_num(pp_fin)}\n"
            f"**Variation Globale** : **{format_diff(variation_totale)}**\n"
            f"**Pic historique** : {format_num(pic_pp)} PP\n"
            f"**Pire journée** : {format_num(pire_pp)} PP"
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
                diff_txt = "(Point de départ)"

            lignes_historique.append(f"• **{day}** ➔ **{format_num(pp_jour)} PP** {diff_txt}")

        embeds = []
        chunk_size = 15
        nb_pages = max(1, (len(lignes_historique) - 1) // chunk_size + 1)

        alliance_name_real = target.get('alliance_name') or target.get('name', alliance)

        for i in range(0, len(lignes_historique), chunk_size):
            chunk = lignes_historique[i:i+chunk_size]
            page_actuelle = (i // chunk_size) + 1
            
            embed = discord.Embed(title=f"<:stats:1512517930490003726> Évolution de la Puissance pour : {alliance_name_real}", color=self.clr_alliance_pp, timestamp=discord.utils.utcnow())
            embed.description = f"Analyse sur les **{jours} derniers jours**."
            embed.add_field(name="<:podium:1512523218299392131> Bilan Global de la Période", value=stats_txt, inline=False)
            embed.add_field(name=f"<:pp2:1512571027119538335> Historique Quotidien (Page {page_actuelle}/{nb_pages})", value="\n".join(chunk), inline=False)
            
            # 🛠️ Correction : On applique le footer à chaque page de statistiques ici !
            setup_embed_footer(embed, interaction)
            embeds.append(embed)

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

    # ========================================================
    # 🕊️ COMMANDE : VÉRIFIER LA COLOMBE
    # ========================================================
    @app_commands.command(name="colombe", description="Vérifie la date et l'heure de fin de protection d'un joueur")
    @app_commands.autocomplete(joueur=joueur_autocomplete)
    async def colombe(self, interaction: discord.Interaction, joueur: str):
        await interaction.response.defer()
        logger.info(f"🕊️ [Colombe] Vérification par {interaction.user.name} pour : {joueur}")
        
        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        url = f"https://api.gge-tracker.com/api/v1/players/{quote(joueur)}"
        
        session = self.bot.session
        if not session: return await interaction.followup.send("<:error:1512505075220611172> Erreur interne : Connexion HTTP indisponible.")

        try:
            async with session.get(url, headers=headers, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    p_data = data[0] if isinstance(data, list) and data else data
                    if not p_data: return await interaction.followup.send(f"<:error:1512505075220611172> Joueur **{joueur}** introuvable sur l'API.")
                        
                    peace_str = p_data.get("peace_disabled_at")
                    if not peace_str or peace_str == "null":
                        return await interaction.followup.send(f"<:players:1512504277392953426> **{joueur}** n'a actuellement **aucune colombe** en cours. Cible libre !")
                        
                    dt_peace = datetime.fromisoformat(peace_str.replace('Z', '+00:00'))
                    maintenant = discord.utils.utcnow()
                    ts = int(dt_peace.timestamp())
                    
                    if dt_peace > maintenant:
                        embed = discord.Embed(title="<:peace:1512503935892586566> Statut de la Colombe", color=self.clr_colombe)
                        embed.add_field(name="Cible", value=f"**{joueur}**", inline=False)
                        embed.add_field(name="Fin de protection", value=f"<t:{ts}:f>\nSoit **<t:{ts}:R>**", inline=False)
                        setup_embed_footer(embed, interaction)
                        await interaction.followup.send(embed=embed)
                    else:
                        await interaction.followup.send(f"<:players:1512504277392953426> La colombe de **{joueur}** a expiré le <t:{ts}:f> (<t:{ts}:R>). Cible libre !")
                else:
                    await interaction.followup.send(f"<:error:1512505075220611172> Impossible de récupérer les infos de **{joueur}** (Erreur API : {r.status}).")
        except Exception:
            await interaction.followup.send(f"<:error:1512505075220611172> Erreur de connexion au serveur GGE-Tracker.")

    # ========================================================
    # 📜 COMMANDE : DESCRIPTION ALLIANCE (Murs Historiques)
    # ========================================================
    @app_commands.command(name="description_alliance", description="Consulte l'historique des 7 derniers changements de mur d'une alliance")
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.describe(alliance="Le nom de l'alliance à analyser")
    async def description_alliance(self, interaction: discord.Interaction, alliance: str):
        try: 
            await interaction.response.defer(thinking=True)
        except: 
            return

        logger.info(f"📜 [Description Alliance] Recherche historique pour '{alliance}' par {interaction.user.name}")
        alliance_lower = alliance.lower().strip()

        scans_dir = Path("/app/data/murs_scans")
        history = []

        if not scans_dir.exists():
            return await interaction.followup.send("<:error:1512505075220611172> Le dossier des archives de murs (`/app/data/murs_scans`) is introuvable.")

        try:
            files = list(scans_dir.rglob('murs_alliances_*.json'))
            files.sort(key=lambda p: (p.parent.name, p.name), reverse=True)

            # 🛠️ Déportation du parsing de fichiers multiples en Threading
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
            return await interaction.followup.send("<:error:1512505075220611172> Une erreur technique est survenue lors de l'analyse des fichiers historiques.")

        if not history:
            return await interaction.followup.send(f"<:error:1512505075220611172> Aucune description trouvée pour l'alliance **{alliance}** dans les archives locales.")

        embed = discord.Embed(
            title=f"<:search:1512504654183792690> Archives Alliance : {alliance.upper()}",
            description=f"Analyse des variations et modifications du mur de l'alliance.\n*Traçabilité sur les {len(history)} derniers changements uniques détectés.*",
            color=self.clr_alliance,
            timestamp=discord.utils.utcnow()
        )

        for i, entry in enumerate(history):
            header_title = "<:alliance:1512503083861540914> Description Actuelle" if i == 0 else f"<:memberlist:1512572899360378971> Version du {entry['date']} à {entry['heure']}"
            
            texte_affiche = entry['texte'].replace('<br />', '\n').replace('<br/>', '\n').replace('<br>', '\n').strip()
            valeur_champ = f">>> {texte_affiche}"
            if len(valeur_champ) > 1020: valeur_champ = valeur_champ[:1017] + "..."

            embed.add_field(name=header_title, value=valeur_champ, inline=False)

        setup_embed_footer(embed, interaction)
        await interaction.followup.send(embed=embed)


# 🔌 Branchement du Cog
async def setup(bot: commands.Bot):
    await bot.add_cog(ProfilsCog(bot))