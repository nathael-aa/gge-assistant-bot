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

# 🛠️ Import de la boîte à outils
from utils import (
    BASE_DATA_PATH, 
    CACHE,
    joueur_autocomplete, 
    alliance_autocomplete, 
    format_num, 
    BOT_VERSION,
    MON_ID_DISCORD
)

logger = logging.getLogger("GGE_Bot")

# ==========================================
# GESTION DU FICHIER DIPLOMATIE
# ==========================================
DIPLO_FILE = BASE_DATA_PATH / 'diplomatie.json'

def load_diplo():
    if os.path.exists(DIPLO_FILE):
        with open(DIPLO_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_diplo(data):
    with open(DIPLO_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_alliance_diplo_key(data, alliance_name):
    if not alliance_name: return None
    for key in data.keys():
        if key.lower() == alliance_name.lower():
            return key
    return alliance_name

class GuerreCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ==========================================
    # 🔍 COMMANDE : ALLIANCE SCANNER (Guerre & Paginé)
    # ==========================================
    @app_commands.command(name="alliance_scanner", description="🔍 Analyse le roster ennemi en temps réel (Colombes, PP, Cibles)")
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    async def alliance_scanner(self, interaction: discord.Interaction, alliance: str):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        logger.info(f"🔍 [Alliance Scanner] Utilisé par {interaction.user.name} pour l'alliance : {alliance}")

        # 1. On trouve l'ID de l'alliance via le NAS (Version Sécurisée)
        player_files = list((BASE_DATA_PATH / 'server_scans').rglob('server_*.json'))
        if not player_files: return await interaction.followup.send("❌ Aucun scan local trouvé.")
        latest = max(player_files, key=lambda p: p.stat().st_mtime)
        with open(latest, 'r', encoding='utf-8') as f:
            local_data = json.load(f)

        target_id = None
        for p_info in local_data.get('players', {}).values():
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
            return await interaction.followup.send(f"⚠️ Alliance **{alliance}** introuvable dans le cache local.")

        # 2. On interroge l'API en live
        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        url = f"https://api.gge-tracker.com/api/v1/alliances/id/{target_id}"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers, timeout=10) as r:
                    if r.status != 200:
                        logger.warning(f"[Alliance Scanner] Erreur API pour {alliance} (Statut: {r.status})")
                        return await interaction.followup.send("❌ Erreur de l'API GGE-Tracker (Impossible d'obtenir les données live).")
                    data = await r.json()
                    if isinstance(data, list) and data: data = data[0]
            except Exception as e:
                logger.error(f"[Alliance Scanner] Impossible de joindre l'API : {e}")
                return await interaction.followup.send(f"❌ Impossible de joindre l'API : {e}")

        members = data.get("players", data.get("members", data.get("playerList", [])))
        if not members:
            return await interaction.followup.send("⚠️ L'alliance semble vide ou l'API ne renvoie pas les membres.")

        maintenant = discord.utils.utcnow()
        colombes = []
        cibles_libres = []
        
        # 3. On trie le roster
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

        # 4. Création des pages interactives (Pagination)
        from utils import BOT_VERSION, PaginationView, format_num
        
        embeds = []
        chunk_size = 15
        
        # Préparation des lignes de texte
        lignes_colombes = [f"🕊️ **{c['name']}** ({format_num(c['pp'])} PP) ➔ Fin: <t:{c['fin']}:R>" for c in colombes]
        lignes_cibles = [f"🎯 **{c['name']}** ➔ **{format_num(c['pp'])} PP**" for c in cibles_libres]

        # Fonction locale pour générer la base de l'Embed
        def creer_base_embed(titre_page):
            embed = discord.Embed(title=f"🔍 Scanner de Guerre : {alliance}", color=discord.Color.dark_red(), timestamp=maintenant)
            embed.description = f"👥 **Membres Actifs :** {len(members)}\n🕊️ **Sous protection :** {len(colombes)}\n⚔️ **Cibles vulnérables :** {len(cibles_libres)}\n\n**{titre_page}**"
            embed.set_footer(text=BOT_VERSION)
            return embed

        # Pages des Colombes
        if lignes_colombes:
            nb_pages_col = max(1, (len(lignes_colombes) - 1) // chunk_size + 1)
            for i in range(0, len(lignes_colombes), chunk_size):
                chunk = lignes_colombes[i:i+chunk_size]
                num_page = (i // chunk_size) + 1
                embed = creer_base_embed(f"⏳ Colombes (Page {num_page}/{nb_pages_col})")
                embed.add_field(name="Prochaines à tomber", value="\n".join(chunk), inline=False)
                embeds.append(embed)

        # Pages des Cibles Libres
        if lignes_cibles:
            nb_pages_cib = max(1, (len(lignes_cibles) - 1) // chunk_size + 1)
            for i in range(0, len(lignes_cibles), chunk_size):
                chunk = lignes_cibles[i:i+chunk_size]
                num_page = (i // chunk_size) + 1
                embed = creer_base_embed(f"🔥 Cibles Libres (Page {num_page}/{nb_pages_cib})")
                embed.add_field(name="Cibles triées par Puissance", value="\n".join(chunk), inline=False)
                embeds.append(embed)

        # 5. Envoi final
        if not embeds:
            await interaction.followup.send("⚠️ L'alliance ne contient aucun membre exploitable.")
            return

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

    # ==========================================
    # 📍 COMMANDE : PROXIMITÉ (Guerre & Paginé)
    # ==========================================
    @app_commands.command(name="proximite", description="📍 Trouve les châteaux ennemis les plus proches de toi")
    @app_commands.autocomplete(mon_pseudo=joueur_autocomplete)
    @app_commands.autocomplete(alliance_ennemie=alliance_autocomplete)
    async def proximite(self, interaction: discord.Interaction, mon_pseudo: str, alliance_ennemie: str):
        import math
        import urllib.parse
        from datetime import datetime
        try: await interaction.response.defer(thinking=True)
        except: return

        logger.info(f"📍 [Proximité] Utilisé par {interaction.user.name} (Moi: {mon_pseudo}, Ennemie: {alliance_ennemie})")

        # 1. On trouve l'ID de l'alliance ennemie via le NAS
        player_files = list((BASE_DATA_PATH / 'server_scans').rglob('server_*.json'))
        if not player_files: return await interaction.followup.send("❌ Aucun scan local trouvé.")
        latest = max(player_files, key=lambda p: p.stat().st_mtime)
        with open(latest, 'r', encoding='utf-8') as f:
            local_data = json.load(f)

        target_id = None
        for p_info in local_data.get('players', {}).values():
            a_obj = p_info.get('alliance')
            a_name = a_obj.get('name') if isinstance(a_obj, dict) else (p_info.get('alliance_name') or a_obj)
            
            # 🎯 CORRECTION ICI : On utilise bien 'alliance_ennemie'
            if a_name and str(a_name).lower() == alliance_ennemie.lower():
                aid = p_info.get('allianceId') or p_info.get('alliance_id')
                if not aid and isinstance(a_obj, dict):
                    aid = a_obj.get('allianceId') or a_obj.get('alliance_id')
                
                if aid:
                    target_id = str(aid)
                    alliance_ennemie = str(a_name) # On récupère la belle orthographe
                    break

        if not target_id:
            return await interaction.followup.send(f"⚠️ Alliance **{alliance_ennemie}** introuvable dans le cache.")

        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        
        async with aiohttp.ClientSession() as session:
            # 2. On cherche TES coordonnées
            my_x, my_y = None, None
            url_me = f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(mon_pseudo)}"
            try:
                async with session.get(url_me, headers=headers, timeout=5) as r:
                    if r.status == 200:
                        for c in await r.json():
                            # Sécurisation en string pour éviter les bugs 0 vs "0"
                            if str(c.get('kingdomId', c.get('kingdom_id'))) == "0" and str(c.get('type', c.get('castle_type'))) == "1":
                                my_x = c.get('positionX') or c.get('position_x') or c.get('x')
                                my_y = c.get('positionY') or c.get('position_y') or c.get('y')
                                break
            except: pass
            
            if my_x is None or my_y is None:
                return await interaction.followup.send(f"❌ Impossible de trouver les coordonnées exactes de **{mon_pseudo}**.")

            my_x, my_y = int(my_x), int(my_y)

            # 3. On récupère le roster ennemi
            url_alli = f"https://api.gge-tracker.com/api/v1/alliances/id/{target_id}"
            try:
                async with session.get(url_alli, headers=headers, timeout=10) as r:
                    if r.status != 200: return await interaction.followup.send("❌ Erreur de l'API GGE-Tracker (Alliance).")
                    data = await r.json()
                    if isinstance(data, list) and data: data = data[0]
            except Exception as e:
                logger.error(f"[Proximité] Impossible de joindre l'API : {e}")
                return await interaction.followup.send(f"❌ Impossible de joindre l'API : {e}")

            # 🎯 CORRECTION : On cherche "players" en priorité comme on a appris
            members = data.get("players", data.get("members", data.get("playerList", [])))
            if not members: return await interaction.followup.send("⚠️ L'alliance ennemie semble vide.")

            # 4. Fonction asynchrone rapide pour localiser chaque membre
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
                    async with session.get(url_s, headers=headers, timeout=10) as res:
                        if res.status == 200:
                            for c in await res.json():
                                if str(c.get('kingdomId', c.get('kingdom_id'))) == "0" and str(c.get('type', c.get('castle_type'))) == "1":
                                    x = c.get('positionX') or c.get('position_x') or c.get('x')
                                    y = c.get('positionY') or c.get('position_y') or c.get('y')
                                    if x is not None and y is not None:
                                        dist = math.hypot(int(x) - my_x, int(y) - my_y)
                                        return {"name": p_name, "x": int(x), "y": int(y), "dist": dist, "pp": p_pp, "protected": is_protected}
                except: pass
                return None

            # On lance tous les espions en même temps !
            tasks = [get_enemy_coords(m) for m in members]
            results = await asyncio.gather(*tasks)

        # 5. On trie les résultats par distance
        valid_targets = [res for res in results if res is not None]
        valid_targets.sort(key=lambda t: t["dist"])

        if not valid_targets:
            return await interaction.followup.send("⚠️ Impossible de localiser les châteaux de cette alliance sur la carte Principale.")

        # --- 6. CRÉATION DES PAGES (Pagination) ---
        from utils import PaginationView, format_num, BOT_VERSION
        
        embeds = []
        chunk_size = 5 # 5 cibles par page pour garder un Embed propre et aéré
        nb_pages = max(1, (len(valid_targets) - 1) // chunk_size + 1)
        
        for i in range(0, len(valid_targets), chunk_size):
            chunk = valid_targets[i:i+chunk_size]
            page_num = (i // chunk_size) + 1
            
            embed = discord.Embed(
                title=f"📍 Cibles de Proximité : {alliance_ennemie}", 
                description=f"🛰️ Ton point de départ : **{mon_pseudo}** (`{my_x}:{my_y}`)\n🔍 **{len(valid_targets)}** cibles localisées au total.", 
                color=discord.Color.brand_green(), 
                timestamp=discord.utils.utcnow()
            )
            
            for j, t in enumerate(chunk):
                index_global = i + j + 1
                colombe_txt = "🕊️ **SOUS COLOMBE**" if t['protected'] else "🔥 **VULNÉRABLE**"
                embed.add_field(
                    name=f"#{index_global} - {t['name']}", 
                    value=f"📏 Distance : **{int(t['dist'])} lieues**\n📍 Coords : `{t['x']}:{t['y']}`\n💪 Puissance : {format_num(t['pp'])}\n{colombe_txt}", 
                    inline=False
                )

            embed.add_field(name=f"Page {page_num}/{nb_pages}", value="*Tri effectué du plus proche au plus éloigné.*", inline=False)
            embed.set_footer(text=BOT_VERSION)
            embeds.append(embed)

        # 7. Envoi Final
        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

    # ==========================================
    # 🥊 COMPARE_JOUEUR (V8 - Cumul des Records)
    # ==========================================
    @app_commands.command(name="compare_joueur", description="Organise un duel statistique épique (7 rounds) entre deux joueurs !")
    @app_commands.autocomplete(joueur1=joueur_autocomplete)
    @app_commands.autocomplete(joueur2=joueur_autocomplete)
    async def compare_joueur(self, interaction: discord.Interaction, joueur1: str, joueur2: str):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        logger.info(f"🥊 [Compare Joueur] Utilisé par {interaction.user.name} ({joueur1} vs {joueur2})")

        # 1. Chargement des données locales (NAS)
        async def fetch_player_data(player_name):
            try:
                process = await asyncio.create_subprocess_exec(
                    "python3", "scanners/player_scanner.py", player_name,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=45.0)
                res = stdout.decode('utf-8')
                if "JSON_FILE:" in res:
                    path = res.split("JSON_FILE:")[1].strip().replace('/volume1/gge-assistant', '/app')
                    if os.path.exists(path):
                        with open(path, 'r', encoding='utf-8') as f:
                            return json.load(f).get('parsed_data', {})
            except: pass
            return None

        p1_data, p2_data = await asyncio.gather(fetch_player_data(joueur1), fetch_player_data(joueur2))

        if not p1_data: return await interaction.followup.send(f"❌ Impossible de trouver **{joueur1}**.")
        if not p2_data: return await interaction.followup.send(f"❌ Impossible de trouver **{joueur2}**.")

        def clean_id(p_data):
            pid = str(p_data.get('player_id', p_data.get('id', '')))
            return pid + '164' if pid and not pid.endswith('164') else pid

        p1_id, p2_id = clean_id(p1_data), clean_id(p2_data)

        # 2. Chargement des données API en direct
        async def get_extended_stats(player_id, player_name, session):
            stats = {
                "might": 0, "fame": 0, "loot": 0, "rank_srv": "N/A", 
                "fire_count": 0, "fire_txt": "🛡️ Forteresse Impeccable", 
                "total_pb_events": 0, "alliance_might": 0
            }
            headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
            
            if player_id:
                # Classement (Gloire, Butin)
                try:
                    rank_url = f"https://api.gge-tracker.com/api/v1/statistics/ranking/player/{player_id}"
                    async with session.get(rank_url, headers=headers, timeout=5) as r:
                        if r.status == 200:
                            data = await r.json()
                            stats.update({
                                "might": int(data.get("might_current", data.get("might", 0))), 
                                "fame": int(data.get("current_fame", data.get("fame", 0))), 
                                "loot": int(data.get("loot_current", data.get("loot", 0))), 
                                "rank_srv": data.get("server_rank", "N/A")
                            })
                except: pass

                # 💡 NOUVEAU : Calcul du Cumul des Records Personnels (PB)
                try:
                    stats_url = f"https://api.gge-tracker.com/api/v1/statistics/player/{player_id}"
                    async with session.get(stats_url, headers=headers, timeout=5) as r:
                        if r.status == 200:
                            st_data = await r.json()
                            pts_dict = st_data.get("points", {})
                            cumul_pb = 0
                            
                            # On isole chaque événement, on trouve son max, et on l'ajoute au total
                            for ev_key, ev_list in pts_dict.items():
                                ev_max = 0
                                for entry in ev_list:
                                    val = int(entry.get("point", 0))
                                    if val > ev_max: ev_max = val
                                cumul_pb += ev_max
                                
                            stats["total_pb_events"] = cumul_pb
                except: pass

            # Alliance & Feux
            try:
                p_url = f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(player_name)}"
                async with session.get(p_url, headers=headers, timeout=5) as r:
                    if r.status == 200:
                        p_live = await r.json()
                        if isinstance(p_live, list) and p_live: p_live = p_live[0]
                        if p_live:
                            a_id = p_live.get("allianceId", p_live.get("alliance_id"))
                            # Calcul de la puissance d'alliance
                            if a_id and str(a_id) != "0":
                                a_url = f"https://api.gge-tracker.com/api/v1/alliances/id/{a_id}"
                                async with session.get(a_url, headers=headers, timeout=5) as ra:
                                    if ra.status == 200:
                                        a_data = await ra.json()
                                        if isinstance(a_data, list) and a_data: a_data = a_data[0]
                                        if a_data:
                                            members = a_data.get("players", a_data.get("members", a_data.get("playerList", [])))
                                            if members:
                                                stats["alliance_might"] = sum(int(m.get("might_current", m.get("might", m.get("main_points", 0)))) for m in members)
                                            else:
                                                stats["alliance_might"] = int(a_data.get("might", a_data.get("total_might", 0)))
            except: pass

            try:
                search_url = f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(player_name)}"
                async with session.get(search_url, headers=headers, timeout=5) as r:
                    if r.status == 200:
                        castles = await r.json()
                        c_id = next((c.get('id') for c in castles if isinstance(c, dict) and c.get('kingdomId') == 0 and c.get('type') == 1), None)
                        if c_id:
                            analysis_url = f"https://api.gge-tracker.com/api/v1/castle/analysis/{c_id}"
                            async with session.get(analysis_url, headers=headers, timeout=5) as a_resp:
                                if a_resp.status == 200:
                                    j = await a_resp.json()
                                    bldgs = j.get("data", {}).get("buildings", [])
                                    fire_count = sum(1 for b in bldgs if b.get("hitPoints", 100) < 100 or b.get("damageFactor", 0) > 0)
                                    stats["fire_count"] = fire_count
                                    if fire_count > 50: stats["fire_txt"] = f"🌋 **Cendres & Désolation** ({fire_count} feux)"
                                    elif fire_count > 0: stats["fire_txt"] = f"🔥 **Ça sent le roussi** ({fire_count} feux)"
                                else: stats["fire_txt"] = "❓ *Brouillard de guerre*"
            except: pass
            
            return stats

        async with aiohttp.ClientSession() as session:
            ext1, ext2 = await asyncio.gather(
                get_extended_stats(p1_id, p1_data.get('name', joueur1), session),
                get_extended_stats(p2_id, p2_data.get('name', joueur2), session)
            )

        n1, n2 = p1_data.get('name', joueur1), p2_data.get('name', joueur2)
        
        # 3. Répartition des statistiques finales
        m1 = ext1['might'] if ext1['might'] > 0 else int(p1_data.get('main_points', 0))
        m2 = ext2['might'] if ext2['might'] > 0 else int(p2_data.get('main_points', 0))
        
        b_lvl1, l_lvl1 = int(p1_data.get('level', 0)), int(p1_data.get('legendary_level', 0))
        b_lvl2, l_lvl2 = int(p2_data.get('level', 0)), int(p2_data.get('legendary_level', 0))
        
        hon1, hon2 = int(p1_data.get('honor', 0)), int(p2_data.get('honor', 0))
        
        alli_might1 = ext1['alliance_might'] if ext1['alliance_might'] > 0 else int(p1_data.get('alliance', {}).get('total_might', p1_data.get('alliance', {}).get('might', 0)))
        alli_might2 = ext2['alliance_might'] if ext2['alliance_might'] > 0 else int(p2_data.get('alliance', {}).get('total_might', p2_data.get('alliance', {}).get('might', 0)))

        score_p1, score_p2 = 0, 0

        # Arbitres
        def compare_stat(val1, val2):
            nonlocal score_p1, score_p2
            if val1 == val2: return "🤝 *Égalité parfaite*"
            if val1 > val2: 
                score_p1 += 1
                return f"👑 **{n1}** (+{format_num(abs(val1 - val2))})"
            else: 
                score_p2 += 1
                return f"👑 **{n2}** (+{format_num(abs(val1 - val2))})"

        def compare_levels(base1, leg1, base2, leg2):
            nonlocal score_p1, score_p2
            if (base1, leg1) == (base2, leg2): return "🤝 *Égalité parfaite*"
            if (base1, leg1) > (base2, leg2):
                score_p1 += 1
                return f"👑 **{n1}**"
            else:
                score_p2 += 1
                return f"👑 **{n2}**"

        # 4. Les 7 Rounds (Succès supprimés, Events ajustés)
        res_lvl = compare_levels(b_lvl1, l_lvl1, b_lvl2, l_lvl2)
        res_might = compare_stat(m1, m2)
        res_hon = compare_stat(hon1, hon2)
        res_fame = compare_stat(ext1['fame'], ext2['fame'])
        res_alli = compare_stat(alli_might1, alli_might2)
        res_loot = compare_stat(ext1['loot'], ext2['loot'])
        res_events = compare_stat(ext1['total_pb_events'], ext2['total_pb_events'])

        embed = discord.Embed(title=f"🥊 LE CHOC DES TITANS 🥊", description=f"Le public retient son souffle... **{n1}** affronte **{n2}** sur 7 rounds !", color=discord.Color.gold(), timestamp=discord.utils.utcnow())

        p1_desc = f"**Niveau :** {b_lvl1} (Lég. {l_lvl1})\n**Bannière :** {p1_data.get('alliance', {}).get('name', 'Loup solitaire')}\n**Prestige :** Top #{ext1['rank_srv']}\n**Donjon :** {ext1['fire_txt']}"
        p2_desc = f"**Niveau :** {b_lvl2} (Lég. {l_lvl2})\n**Bannière :** {p2_data.get('alliance', {}).get('name', 'Loup solitaire')}\n**Prestige :** Top #{ext2['rank_srv']}\n**Donjon :** {ext2['fire_txt']}"
        
        embed.add_field(name=f"🔵 Le Challenger", value=f"**{n1}**\n{p1_desc}", inline=True)
        embed.add_field(name=f"🔴 L'Adversaire", value=f"**{n2}**\n{p2_desc}", inline=True)

        arena_txt = (
            f"> 🏰 **Round 1 : Niveau Global**\n> {res_lvl}\n> `Niv {b_lvl1} (Lég {l_lvl1})` 🆚 `Niv {b_lvl2} (Lég {l_lvl2})`\n\n"
            f"> 💪 **Round 2 : Puissance Brute**\n> {res_might}\n> `{format_num(m1)}` 🆚 `{format_num(m2)}`\n\n"
            f"> 🏅 **Round 3 : Bravoure (Honneur)**\n> {res_hon}\n> `{format_num(hon1)}` 🆚 `{format_num(hon2)}`\n\n"
            f"> 🌟 **Round 4 : Gloire Accumulée**\n> {res_fame}\n> `{format_num(ext1['fame'])}` 🆚 `{format_num(ext2['fame'])}`\n\n"
            f"> ⚔️ **Round 5 : Soutien d'Alliance**\n> {res_alli}\n> `{format_num(alli_might1)} PP` 🆚 `{format_num(alli_might2)} PP`\n\n"
            f"> 💰 **Round 6 : Trésors Pillés**\n> {res_loot}\n> `{format_num(ext1['loot'])}` 🆚 `{format_num(ext2['loot'])}`\n\n"
            f"> 🎪 **Round 7 : Cumul des Records (Events)**\n> {res_events}\n> `{format_num(ext1['total_pb_events'])} pts` 🆚 `{format_num(ext2['total_pb_events'])} pts`"
        )
        embed.add_field(name="🏟️ L'Arène des Statistiques", value=arena_txt, inline=False)

        # Ajustement des scores de victoire (Sur 7 points max)
        if score_p1 >= 6: commentary, embed.color = f"🎙️ **Verdict ({score_p1}-{score_p2}) :** Un massacre absolu ! **{n1}** a humilié son adversaire.", discord.Color.blue()
        elif score_p2 >= 6: commentary, embed.color = f"🎙️ **Verdict ({score_p1}-{score_p2}) :** Un massacre absolu ! **{n2}** rentre chez lui en légende.", discord.Color.red()
        elif score_p1 > score_p2: commentary, embed.color = f"🎙️ **Verdict ({score_p1}-{score_p2}) :** Victoire indiscutable de **{n1}** !", discord.Color.blue()
        elif score_p2 > score_p1: commentary, embed.color = f"🎙️ **Verdict ({score_p1}-{score_p2}) :** Victoire indiscutable de **{n2}** !", discord.Color.red()
        else: commentary, embed.color = f"🎙️ **Verdict ({score_p1}-{score_p2}) :** ÉGALITÉ PARFAITE ! Quel combat d'anthologie !", discord.Color.light_grey()

        embed.add_field(name="🎤 Le Mot du Commentateur", value=commentary, inline=False)
        embed.set_footer(text=BOT_VERSION)
        
        await interaction.followup.send(embed=embed)

    # ==========================================
    # ⚔️ COMPARE_ALLIANCE
    # ==========================================
    @app_commands.command(name="compare_alliance", description="Organise une guerre statistique épique entre deux alliances !")
    @app_commands.autocomplete(alliance1=alliance_autocomplete)
    @app_commands.autocomplete(alliance2=alliance_autocomplete)
    async def compare_alliance(self, interaction: discord.Interaction, alliance1: str, alliance2: str):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        logger.info(f"⚔️ [Compare Alliance] Utilisé par {interaction.user.name} ({alliance1} vs {alliance2})")

        async def fetch_alliance_data(a_name):
            try:
                clean_name = str(a_name).replace(' ', '_').replace('/', '-').replace('\\', '-')
                details_dir = Path('/app/data/alliance_details')
                if details_dir.exists():
                    fichiers = list(details_dir.rglob(f"{clean_name}_*.json"))
                    if fichiers:
                        latest_file = max(fichiers, key=lambda p: p.stat().st_mtime)
                        if time.time() - latest_file.stat().st_mtime < 7200:
                            with open(latest_file, 'r', encoding='utf-8') as f:
                                return json.load(f).get('parsed_data', {})
            except: pass

            try:
                process = await asyncio.create_subprocess_exec("python3", "scanners/alliance_scanner.py", a_name, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=60.0)
                res = stdout.decode('utf-8')
                if "JSON_FILE:" in res:
                    path = res.split("JSON_FILE:")[1].strip().replace('/volume1/gge-assistant', '/app')
                    if os.path.exists(path):
                        with open(path, 'r', encoding='utf-8') as f:
                            return json.load(f).get('parsed_data', {})
            except: pass
            return None

        a1_data, a2_data = await asyncio.gather(fetch_alliance_data(alliance1), fetch_alliance_data(alliance2))

        if not a1_data: return await interaction.followup.send(f"❌ L'alliance **{alliance1}** est introuvable.")
        if not a2_data: return await interaction.followup.send(f"❌ L'alliance **{alliance2}** est introuvable.")

        n1, n2 = a1_data.get('name', alliance1), a2_data.get('name', alliance2)

        score_a1, score_a2 = 0, 0
        def compare_stat(val1, val2):
            nonlocal score_a1, score_a2
            if val1 > val2: 
                score_a1 += 1
                return f"🛡️ **{n1}** (+{format_num(val1 - val2)})"
            elif val2 > val1: 
                score_a2 += 1
                return f"🛡️ **{n2}** (+{format_num(val2 - val1)})"
            return "🤝 *Égalité parfaite*"

        m1, m2 = int(a1_data.get('total_might', 0)), int(a2_data.get('total_might', 0))
        mem1, mem2 = max(1, a1_data.get('members_count', 1)), max(1, a2_data.get('members_count', 1))
        avg_m1, avg_m2 = m1 // mem1, m2 // mem2

        def get_weekly_gain(data, key_type):
            history = data.get('stats_history', {}).get(key_type, [])
            if not history or len(history) < 2: return 0
            history.sort(key=lambda x: x.get("date", ""))
            latest_pt = int(history[-1].get("point", 0))
            target_date_str = (discord.utils.utcnow() - timedelta(days=7)).isoformat().replace('+00:00', 'Z')
            past_pt = int(history[0].get("point", 0))
            for r in history:
                if r.get("date", "") >= target_date_str:
                    past_pt = int(r.get("point", 0))
                    break
            diff = latest_pt - past_pt
            return max(0, diff) if key_type == 'loot' else diff

        loot1, loot2 = get_weekly_gain(a1_data, 'loot') // 7, get_weekly_gain(a2_data, 'loot') // 7
        growth1, growth2 = get_weekly_gain(a1_data, 'might') // 7, get_weekly_gain(a2_data, 'might') // 7

        res_might, res_avg = compare_stat(m1, m2), compare_stat(avg_m1, avg_m2)
        res_loot, res_growth = compare_stat(loot1, loot2), compare_stat(growth1, growth2)

        embed = discord.Embed(title=f"⚔️ LA GUERRE DES ALLIANCES ⚔️", description=f"Les cors de guerre résonnent... **{n1}** marche contre **{n2}** !", color=discord.Color.dark_magenta(), timestamp=discord.utils.utcnow())
        embed.add_field(name=f"🔵 Campagne de {n1}", value=f"👑 **Chef :** {a1_data.get('leader', 'Inconnu')}\n👥 **Effectif :** {mem1}/65\n🏆 **Gloire :** {format_num(a1_data.get('total_fame', 0))}", inline=True)
        embed.add_field(name=f"🔴 Campagne de {n2}", value=f"👑 **Chef :** {a2_data.get('leader', 'Inconnu')}\n👥 **Effectif :** {mem2}/65\n🏆 **Gloire :** {format_num(a2_data.get('total_fame', 0))}", inline=True)

        arena_txt = f"> **💪 Puissance Globale**\n> {res_might}\n> `{format_num(m1)}` 🆚 `{format_num(m2)}`\n\n> **⚔️ Puissance Moyenne**\n> {res_avg}\n> `{format_num(avg_m1)}` 🆚 `{format_num(avg_m2)}`\n\n> **💰 Pillage Moyen/Jour**\n> {res_loot}\n> `{format_num(loot1)}` 🆚 `{format_num(loot2)}`\n\n> **📈 Croissance Moyenne/Jour**\n> {res_growth}\n> `{format_num(growth1)}` 🆚 `{format_num(growth2)}`"
        embed.add_field(name="🗺️ Le Champ de Bataille", value=arena_txt, inline=False)

        if score_a1 == 4: commentary, embed.color = f"🎙️ **Verdict (4-0) :** Un massacre absolu. **{n1}** a rasé la carte.", discord.Color.blue()
        elif score_a2 == 4: commentary, embed.color = f"🎙️ **Verdict (4-0) :** Un massacre absolu. **{n2}** n'a laissé que des cendres.", discord.Color.red()
        elif score_a1 == 3: commentary, embed.color = f"🎙️ **Verdict (3-1) :** Large victoire de **{n1}** !", discord.Color.blue()
        elif score_a2 == 3: commentary, embed.color = f"🎙️ **Verdict (3-1) :** Large victoire de **{n2}** !", discord.Color.red()
        elif score_a1 == 2 and score_a2 == 2: commentary, embed.color = f"🎙️ **Verdict (2-2) :** ÉGALITÉ ! Une véritable guerre de tranchées.", discord.Color.light_grey()
        else:
            winner = n1 if score_a1 > score_a2 else n2
            commentary = f"🎙️ **Verdict ({max(score_a1, score_a2)}-{min(score_a1, score_a2)}) :** Victoire de **{winner}** dans la douleur."

        embed.add_field(name="🎤 Rapport de l'Éclaireur", value=commentary, inline=False)
        embed.set_footer(text=BOT_VERSION)
        await interaction.followup.send(embed=embed)

    # ==========================================
    # 🎯 COMMANDE : CIBLE (V4.5 - Limite +10M PP max)
    # ==========================================
    @app_commands.command(name="cible", description="Trouve des cibles légales (Règles CDR strictes + Surclassement)")
    @app_commands.autocomplete(attaquant=joueur_autocomplete)
    @app_commands.autocomplete(alliance_cible=alliance_autocomplete)
    @app_commands.choices(tri=[
        app_commands.Choice(name="📏 Plus proches", value="distance"),
        app_commands.Choice(name="💪 Par Puissance", value="puissance"),
        app_commands.Choice(name="🎲 Aléatoire", value="aleatoire")
    ])
    @app_commands.describe(
        tri="Sélectionne obligatoirement la méthode de tri des cibles",
        alliance_cible="Optionnel : Restreindre la recherche à une alliance ennemie spécifique"
    )
    async def cible(self, interaction: discord.Interaction, attaquant: str, tri: str, alliance_cible: str = None):
        import math
        import random
        try: await interaction.response.defer(thinking=True)
        except: return
        
        logger.info(f"🎯 [Cible] Utilisé par {interaction.user.name} (Attaquant: {attaquant}, Tri: {tri}, Alliance Cible: {alliance_cible})")

        from utils import CACHE, BASE_DATA_PATH
        
        def get_tier(lvl, leg):
            if lvl < 70: return 0
            if leg <= 120: return 1
            if leg <= 360: return 2
            if leg <= 650: return 3
            if leg <= 949: return 4
            return 5

        # --- 🛡️ Moteur de Règles RoE Strictes ---
        def is_legal_target(a_pp, a_tier, a_lvl, t_pp, t_tier, t_lvl):
            if a_pp >= 50_000_000:
                return t_pp >= 50_000_000 
            else:
                if t_pp >= 50_000_000: return False
                if t_tier not in [a_tier, a_tier + 1]: return False 
                if t_pp < (a_pp - 3_000_000): return False 
                if t_pp > (a_pp + 10_000_000): return False 
                if a_tier == 0 and t_tier == 0 and abs(a_lvl - t_lvl) > 10: return False 
                return True

        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        local_data = CACHE.get('players_data', {})
        
        # --- 1. Identifier l'attaquant en DIRECT ---
        a_info, a_name_real = None, attaquant
        a_coords = {"x": None, "y": None}
        
        async with aiohttp.ClientSession() as session:
            try:
                search_url = f"https://api.gge-tracker.com/api/v1/players/{attaquant}"
                async with session.get(search_url, headers=headers, timeout=5) as r:
                    if r.status == 200:
                        p_data = await r.json()
                        if p_data and isinstance(p_data, list) and len(p_data) > 0:
                            a_info = p_data[0]
                            a_name_real = a_info.get("playerName", a_info.get("name", attaquant))
                
                coords_url = f"https://api.gge-tracker.com/api/v1/castle/search/{a_name_real}"
                async with session.get(coords_url, headers=headers, timeout=5) as r:
                    if r.status == 200:
                        c_data = await r.json()
                        if isinstance(c_data, list):
                            for c in c_data:
                                k_id = str(c.get('kingdomId', c.get('kingdom_id', 'X')))
                                c_type = str(c.get('type', c.get('castle_type', 'X')))
                                if k_id == "0" and c_type == "1":
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

        if not a_info: return await interaction.followup.send(f"⚠️ Attaquant **{attaquant}** introuvable.")

        a_lvl = int(a_info.get('level', 0))
        a_leg = int(a_info.get('legendary_level', a_info.get('legendaryLevel', 0)))
        a_pp = int(a_info.get('might_current', a_info.get('main_points', a_info.get('might', 0))))
        a_tier = get_tier(a_lvl, a_leg)
        a_alliance = a_info.get('allianceName', a_info.get('alliance', ''))
        if isinstance(a_alliance, dict): a_alliance = a_alliance.get('name')

        # --- 2. Récupération Diplomatie & Murs ---
        allies_et_pna_clean = []
        try:
            if a_alliance:
                with open(BASE_DATA_PATH / 'diplomatie.json', 'r', encoding='utf-8') as f:
                    for key, diplo in json.load(f).items():
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

        # --- 3. Construction du Pool (Filtre NAS Massif) ---
        pool_candidats = []
        for t_name, t_info in local_data.items():
            if t_name.lower() == a_name_real.lower(): continue
            
            t_alliance = t_info.get('alliance') or t_info.get('alliance_name')
            if isinstance(t_alliance, dict): t_alliance = t_alliance.get('name')
            if not t_alliance: continue
            
            if alliance_cible and t_alliance.lower() != alliance_cible.lower(): continue
            if not alliance_cible and a_alliance and t_alliance.lower() == a_alliance.lower(): continue

            alli_clean = "".join(c for c in str(t_alliance).lower() if c.isalnum())
            if alli_clean in allies_et_pna_clean: continue

            t_lvl = int(t_info.get('level', 0))
            t_leg = int(t_info.get('legendary_level', 0))
            t_pp = int(t_info.get('main_points', 0))
            t_tier = get_tier(t_lvl, t_leg)

            if not is_legal_target(a_pp, a_tier, a_lvl, t_pp, t_tier, t_lvl): continue

            has_wall_warning = (alli_clean in alliances_mur_alerte or any(mot in str(t_alliance).lower() for mot in mots_interdits_mur))
            
            pool_candidats.append({
                "name": t_name, "alliance": str(t_alliance), "lvl": t_lvl, "leg": t_leg,
                "tier": t_tier, "pp": t_pp, "dist": 9999, "x": "???", "y": "???",
                "is_upper_tier": (t_tier > a_tier), "wall_warning": has_wall_warning, "is_ghost": False
            })

        if not pool_candidats:
            return await interaction.followup.send("⚠️ Aucune cible trouvée respectant les règles dans les données du serveur.")

        # --- 4. Pré-Tri ---
        if tri == "puissance": pool_candidats.sort(key=lambda x: x["pp"], reverse=True)
        else: random.shuffle(pool_candidats)
        
        candidats_a_verifier = pool_candidats[:60] 

        # --- 5. Vérification API Live avec Régulateur ---
        sem = asyncio.Semaphore(5)

        async def fetch_live_target(session, t):
            async with sem:
                try:
                    async with session.get(f"https://api.gge-tracker.com/api/v1/players/{t['name']}", headers=headers, timeout=5) as r:
                        if r.status == 200:
                            d = await r.json()
                            if isinstance(d, list) and len(d) > 0:
                                info = d[0]
                                t['lvl'] = int(info.get('level', t['lvl']))
                                t['leg'] = int(info.get('legendary_level', info.get('legendaryLevel', t['leg'])))
                                t['pp'] = int(info.get('might_current', info.get('might', t['pp'])))
                                t['tier'] = get_tier(t['lvl'], t['leg'])
                                t['is_upper_tier'] = (t['tier'] > a_tier)
                            elif isinstance(d, list) and len(d) == 0:
                                t['is_ghost'] = True
                except: pass
                
                if not t['is_ghost']:
                    try:
                        async with session.get(f"https://api.gge-tracker.com/api/v1/castle/search/{t['name']}", headers=headers, timeout=5) as r:
                            if r.status == 200:
                                c_data = await r.json()
                                if isinstance(c_data, list) and len(c_data) == 0:
                                    t['is_ghost'] = True
                                elif isinstance(c_data, list):
                                    for c in c_data:
                                        k_id = str(c.get('kingdomId', c.get('kingdom_id', 'X')))
                                        c_type = str(c.get('type', c.get('castle_type', 'X')))
                                        if k_id == "0" and c_type == "1":
                                            tx = c.get('positionX') or c.get('position_x') or c.get('x')
                                            ty = c.get('positionY') or c.get('position_y') or c.get('y')
                                            if tx is not None and ty is not None: 
                                                t['x'], t['y'] = str(tx), str(ty)
                                                if a_coords['x'] is not None and a_coords['y'] is not None:
                                                    t['dist'] = math.sqrt((float(tx) - a_coords['x'])**2 + (float(ty) - a_coords['y'])**2)
                                            break
                    except: pass

        async with aiohttp.ClientSession() as session:
            await asyncio.gather(*(fetch_live_target(session, t) for t in candidats_a_verifier))

        # --- 6. Tri Final et Vérification Ultime ---
        final_targets = []
        for t in candidats_a_verifier:
            if t['is_ghost']: continue

            if is_legal_target(a_pp, a_tier, a_lvl, t['pp'], t['tier'], t['lvl']):
                if not alliance_cible and t['dist'] < 30: continue
                final_targets.append(t)

        if not final_targets:
            return await interaction.followup.send("⚠️ Les cibles potentielles ne respectent plus les règles avec leurs puissances actuelles ou sont en ruines.")

        if tri == "distance": final_targets.sort(key=lambda x: x["dist"])
        elif tri == "puissance": final_targets.sort(key=lambda x: x["pp"], reverse=True)
        
        best_targets = final_targets[:25]

        # --- 7. Création des pages interactives ---
        from utils import PaginationView, format_num, BOT_VERSION
        
        embeds = []
        chunk_size = 5 
        nb_pages = max(1, (len(best_targets) - 1) // chunk_size + 1)
        titre_alliance = f" (Alliance : {alliance_cible})" if alliance_cible else ""
        
        for i in range(0, len(best_targets), chunk_size):
            chunk = best_targets[i:i+chunk_size]
            page_num = (i // chunk_size) + 1
            
            page_color = discord.Color.orange() if any(t['is_upper_tier'] or t['wall_warning'] for t in chunk) else discord.Color.brand_green()
            if a_pp >= 50_000_000: page_color = discord.Color.from_rgb(138, 43, 226)
            
            embed = discord.Embed(
                title=f"🎯 Cibles RoE pour {a_name_real}{titre_alliance}", 
                description=f"💪 Ta Puissance : **{format_num(a_pp)}** | Ton Palier : **{a_tier}**\n📜 *Règles : Tolérance -3M à +10M PP | Palier Supérieur autorisé*\n✅ **{len(best_targets)} cibles légales validées** en temps réel.",
                color=page_color,
                timestamp=discord.utils.utcnow()
            )

            for j, t in enumerate(chunk):
                index_global = i + j + 1
                ligue_txt, min_troops, avp_txt = ("⚔️ Ligue > 50M (No Limit PP)", "20 000", "✅ AP/AVP Autorisés") if a_pp >= 50_000_000 else ("⚖️ Ligue < 50M", "5 000", "✅ AP/AVP Autorisés" if a_pp >= 30_000_000 and t['pp'] >= 30_000_000 else "❌ AP/AVP Interdits (<30M PP)")
                dist_str = "Inconnue" if t['dist'] == 9999 else f"{int(t['dist'])} lieues"
                
                diff_pp = t['pp'] - a_pp
                diff_txt = f"(+{format_num(diff_pp)} PP)" if diff_pp > 0 else f"({format_num(diff_pp)} PP)"
                
                if t['wall_warning']: target_icon = "🔴"
                elif t['is_upper_tier']: target_icon = "🟠"
                else: target_icon = "🟢"

                warnings = []
                if t['wall_warning']: warnings.append("🛟 **VÉRIFIEZ LE MUR :** Mots sensibles ('repos', etc.) détectés dans la description de l'alliance !")
                if t['is_upper_tier']: warnings.append("⚠️ **ATTENTION : Joueur du palier supérieur !** Risque de représailles justifiées.")
                warning_txt = "\n" + "\n".join(warnings) if warnings else ""
                
                separateur = "\n\n━━━━━━━━━━━━━━━━━━━━━━━" if j < len(chunk) - 1 else ""
                
                embed.add_field(
                    name=f"{target_icon} Cible #{index_global} : {t['name']}", 
                    value=f"🛡️ Alliance : **{t['alliance']}**\n⭐ Niveau : {t['lvl']}/{t['leg']} (Palier {t['tier']})\n💪 Puissance : {format_num(t['pp'])} {diff_txt}\n📏 Distance : **{dist_str}**\n📍 Cordonnées : `{t['x']}:{t['y']}`\n{ligue_txt}\n🔥 {avp_txt} | Min. Soldats : **{min_troops}**{warning_txt}{separateur}", 
                    inline=False
                )

            embed.add_field(name=f"Page {page_num}/{nb_pages}", value="🚨 **SPY OBLIGATOIRE** avant impact. Vérifiez la diplomatie et les consignes du jour.", inline=False)
            embed.set_footer(text=BOT_VERSION)
            embeds.append(embed)

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

    # ==========================================
    # ⚖️ COMMANDE : HR
    # ==========================================
    @app_commands.command(name="hr", description="Vérifie si une attaque entre deux joueurs respecte les règles (CDR 2026)")
    @app_commands.autocomplete(attaquant=joueur_autocomplete)
    @app_commands.autocomplete(defenseur=joueur_autocomplete)
    async def hr(self, interaction: discord.Interaction, attaquant: str, defenseur: str):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        logger.info(f"⚖️ [HR] Arbitrage utilisé par {interaction.user.name} ({attaquant} vs {defenseur})")

        if attaquant.lower() == defenseur.lower(): return await interaction.followup.send("⚠️ Tu ne peux pas t'attaquer toi-même, voyons ! 😂")

        try:
            player_files = list((BASE_DATA_PATH / 'server_scans').rglob('server_*.json'))
            if not player_files: return await interaction.followup.send("⚠️ Aucune donnée locale trouvée sur le NAS.")
            with open(max(player_files, key=lambda p: p.stat().st_mtime), 'r', encoding='utf-8') as f: local_data = json.load(f).get('players', {})
        except Exception as e: return await interaction.followup.send(f"⚠️ Erreur de lecture du NAS : {e}")

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

        if not a_info: return await interaction.followup.send(f"❌ L'attaquant **{attaquant}** est introuvable.")
        if not d_info: return await interaction.followup.send(f"❌ Le défenseur **{defenseur}** est introuvable.")

        a_lvl, a_leg, a_pp = int(a_info.get('level', 0)), int(a_info.get('legendary_level', 0)), int(a_info.get('main_points', 0))
        d_lvl, d_leg, d_pp = int(d_info.get('level', 0)), int(d_info.get('legendary_level', 0)), int(d_info.get('main_points', 0))
        a_tier, d_tier = get_tier(a_lvl, a_leg), get_tier(d_lvl, d_leg)
        
        a_alli = a_info.get('alliance') or a_info.get('alliance_name')
        if isinstance(a_alli, dict): a_alli = a_alli.get('name')
        d_alli = d_info.get('alliance') or d_info.get('alliance_name')
        if isinstance(d_alli, dict): d_alli = d_alli.get('name')

        a_coords, d_coords, headers = None, None, {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        async with aiohttp.ClientSession() as session:
            for p_name, is_atk in [(a_name, True), (d_name, False)]:
                try:
                    async with session.get(f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(p_name)}", headers=headers, timeout=5) as r:
                        if r.status == 200:
                            for c in await r.json():
                                if c.get('kingdomId') == 0 and c.get('type') == 1:
                                    x, y = c.get('positionX') or c.get('position_x') or c.get('x'), c.get('positionY') or c.get('position_y') or c.get('y')
                                    if is_atk: a_coords = (int(x), int(y))
                                    else: d_coords = (int(x), int(y))
                                    break
                except: pass

        distance = math.hypot(d_coords[0] - a_coords[0], d_coords[1] - a_coords[1]) if a_coords and d_coords else None
        infractions, allies_propres, pna_propres, diplo_privee = [], [], [], False
        
        try:
            if a_alli:
                with open(BASE_DATA_PATH / 'diplomatie.json', 'r', encoding='utf-8') as f:
                    for key, diplo in json.load(f).items():
                        if key.lower() == a_alli.lower():
                            if diplo.get("guild_id") == interaction.guild_id:
                                allies_propres = ["".join(c for c in str(a).lower() if c.isalnum()) for a in diplo.get("allies", [])]
                                pna_propres = ["".join(c for c in str(a).lower() if c.isalnum()) for a in diplo.get("pna", [])]
                            else: diplo_privee = True
                            break
        except: pass

        if d_alli:
            d_alli_clean = "".join(c for c in str(d_alli).lower() if c.isalnum())
            if d_alli_clean in allies_propres: infractions.append(f"🤝 **Diplomatie** : Le défenseur est dans une alliance ALLIÉE (*{d_alli}*).")
            elif d_alli_clean in pna_propres: infractions.append(f"🤝 **Diplomatie** : Le défenseur est dans une alliance PNA (*{d_alli}*).")
            
            try:
                fichier_murs = BASE_DATA_PATH / 'murs_scans' / 'murs_alliances.json'
                if fichier_murs.exists():
                    with open(fichier_murs, 'r', encoding='utf-8') as f:
                        for nom_json, desc in json.load(f).items():
                            if "".join(c for c in str(nom_json).lower() if c.isalnum()) == d_alli_clean:
                                desc_mur = str(desc).lower()
                                mot_trouve = next((mot for mot in ["repos", "deuil", "hospitalisé"] if mot in desc_mur), None)
                                if mot_trouve: infractions.append(f"🧱 **Mur d'alliance** : Mot-clé interdit détecté (**{mot_trouve.capitalize()}**).\n> 📜 *Mur : \"{str(desc).replace(chr(10), ' ').strip()[:150]}...\"*")
                                break
            except: pass

        if distance is not None and distance < 30: infractions.append(f"📏 **Distance** : Cible trop proche ! Distance : **{int(distance)} lieues** (Min: 30).")
        if a_pp >= 50_000_000:
            if d_pp < 50_000_000: infractions.append("⚖️ **Ligue croisée** : Un Titan (>50M) a l'interdiction de taper un joueur <50M.")
        else:
            if d_pp >= 50_000_000: infractions.append("⚖️ **Ligue croisée** : Un joueur <50M a l'interdiction de taper un Titan (>50M).")
            else:
                if abs(a_pp - d_pp) > 10_000_000: infractions.append(f"💪 **Écart de Puissance** : Différence de {format_num(abs(a_pp - d_pp))} PP (Max : 10M).")
                if a_tier != d_tier: infractions.append(f"⭐ **Paliers** : L'attaquant est Palier {a_tier}, le défenseur est Palier {d_tier}.")
                elif a_tier == 0 and abs(a_lvl - d_lvl) > 10: infractions.append(f"⭐ **Niveaux <70** : Écart de {abs(a_lvl - d_lvl)} niveaux (Max : 10).")

        avp_txt, min_troops = ("✅ AP/AVP Autorisés", "20 000") if a_pp >= 50_000_000 and d_pp >= 50_000_000 else ("✅ AP/AVP Autorisés" if a_pp >= 30_000_000 and d_pp >= 30_000_000 else "❌ AP/AVP Interdits (<30M PP)", "5 000")
        dist_txt = f"{int(distance)} lieues" if distance else "Inconnue"

        embed = discord.Embed(title=f"⚖️ Arbitrage : {a_name} 🆚 {d_name}", timestamp=discord.utils.utcnow())
        embed.add_field(name=f"⚔️ Attaquant : {a_name}", value=f"🛡️ {a_alli or 'Sans alliance'}\n⭐ Lvl {a_lvl}/{a_leg} (Palier {a_tier})\n💪 {format_num(a_pp)} PP", inline=True)
        embed.add_field(name=f"🛡️ Défenseur : {d_name}", value=f"🛡️ {d_alli or 'Sans alliance'}\n⭐ Lvl {d_lvl}/{d_leg} (Palier {d_tier})\n💪 {format_num(d_pp)} PP", inline=True)
        embed.add_field(name="🎯 Détails du Combat", value=f"📏 Distance : **{dist_txt}**\n🔥 {avp_txt}\n🛡️ Min. Soldats : **{min_troops}**", inline=False)
        if diplo_privee: embed.add_field(name="🔒 Sécurité Diplomatique", value="La diplomatie de l'attaquant est gérée par un autre serveur. Liens d'alliance ignorés.", inline=False)
        embed.set_footer(text=BOT_VERSION)

        if not infractions:
            embed.color = discord.Color.green()
            embed.add_field(name="✅ ATTAQUE LÉGALE", value="Aucune infraction détectée.", inline=False)
            await interaction.followup.send(embed=embed)
            await interaction.followup.send(content="🟢 **C'est une attaque en règle !** Il n'y a pas lieu d'envoyer un message diplomatique.")
        else:
            embed.color = discord.Color.red()
            embed.add_field(name="❌ HORS RÈGLES (HR)", value="**Violations :**\n" + "\n".join([f"🔸 {i}" for i in infractions]), inline=False)
            liste_infractions_propres = "\n".join([f"- {i.replace('**', '').replace('🔸 ', '')}" for i in infractions])

            await interaction.followup.send(embed=embed)

    # ==========================================
    # 🤝 GROUPE DE COMMANDES : DIPLOMATIE
    # ==========================================
    diplo_group = app_commands.Group(
        name="diplomatie", 
        description="🤝 Gestion des relations diplomatiques (Admin uniquement)",
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
        data = load_diplo()
        source_key = get_alliance_diplo_key(data, mon_alliance)
        
        if source_key not in data:
            data[source_key] = {"allies": [], "pna": [], "guerre": [], "guild_id": interaction.guild_id}
        else:
            owner_id = data[source_key].get("guild_id")
            if owner_id and owner_id != interaction.guild_id:
                return await interaction.response.send_message("⛔ **Accès refusé** : La diplomatie de cette alliance est gérée par un autre serveur Discord.", ephemeral=True)
            elif not owner_id:
                data[source_key]["guild_id"] = interaction.guild_id
                
        cible_lower = cible.lower()
        for key in ["allies", "pna", "guerre"]:
            data[source_key][key] = [a for a in data[source_key][key] if a.lower() != cible_lower]
            
        data[source_key][statut.value].append(cible)
        save_diplo(data)
        
        emojis = {"allies": "🟢", "pna": "🟡", "guerre": "🔴"}
        await interaction.response.send_message(f"✅ {emojis[statut.value]} Pour **{mon_alliance}**, l'alliance **{cible}** a été enregistrée en tant que **{statut.name}**.", ephemeral=True)

    @diplo_group.command(name="remove", description="Retire une alliance de votre diplomatie (devient neutre)")
    @app_commands.autocomplete(mon_alliance=alliance_autocomplete)
    @app_commands.autocomplete(cible=alliance_autocomplete)
    async def d_remove(self, interaction: discord.Interaction, mon_alliance: str, cible: str):
        logger.info(f"🤝 [Diplo Remove] Utilisé par {interaction.user.name} ({mon_alliance} retire {cible})")
        data = load_diplo()
        source_key = get_alliance_diplo_key(data, mon_alliance)
        
        if source_key not in data:
            return await interaction.response.send_message(f"⚠️ **{mon_alliance}** n'a aucune diplomatie enregistrée.", ephemeral=True)

        owner_id = data[source_key].get("guild_id")
        if owner_id and owner_id != interaction.guild_id:
            return await interaction.response.send_message("⛔ **Accès refusé**.", ephemeral=True)

        cible_lower = cible.lower()
        trouve = False
        
        for key in ["allies", "pna", "guerre"]:
            if any(a.lower() == cible_lower for a in data[source_key][key]):
                data[source_key][key] = [a for a in data[source_key][key] if a.lower() != cible_lower]
                trouve = True
                
        if trouve:
            save_diplo(data)
            await interaction.response.send_message(f"✅ L'alliance **{cible}** a été retirée. Elle est redevenue Neutre.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ **{cible}** n'est dans aucune liste diplomatique de **{mon_alliance}**.", ephemeral=True)

    @diplo_group.command(name="list", description="Affiche le tableau diplomatique d'une alliance (Privé)")
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    async def d_list(self, interaction: discord.Interaction, alliance: str):
        logger.info(f"📜 [Diplo List] Utilisé par {interaction.user.name} pour l'alliance {alliance}")
        data = load_diplo()
        source_key = get_alliance_diplo_key(data, alliance)
        
        if source_key not in data:
            return await interaction.response.send_message(f"📭 Le registre diplomatique de **{alliance}** est vide.", ephemeral=True)
            
        diplo_alli = data[source_key]
        owner_id = diplo_alli.get("guild_id")
        
        if owner_id and owner_id != interaction.guild_id:
            return await interaction.response.send_message(f"⛔ **Accès classifié** : Vous n'avez pas l'autorisation de consulter la diplomatie de **{alliance}** depuis ce serveur.", ephemeral=True)
        
        embed = discord.Embed(title=f"📜 Registre Diplomatique : {alliance}", color=discord.Color.gold())
        embed.add_field(name="🟢 ALLIÉS", value="\n".join([f"🛡️ {a}" for a in diplo_alli.get("allies", [])]) or "*Aucun*", inline=True)
        embed.add_field(name="🟡 PNA", value="\n".join([f"🤝 {a}" for a in diplo_alli.get("pna", [])]) or "*Aucun*", inline=True)
        embed.add_field(name="🔴 GUERRE", value="\n".join([f"⚔️ {a}" for a in diplo_alli.get("guerre", [])]) or "*Aucune*", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

# 🔌 Branchement du Cog
async def setup(bot: commands.Bot):
    await bot.add_cog(GuerreCog(bot))