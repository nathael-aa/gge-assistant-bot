# -*- coding: utf-8 -*-
import os
import json
import logging
import asyncio
import aiohttp
import urllib.parse
import traceback
from datetime import datetime, timedelta
from collections import Counter
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import (
    JOUEURS_DIR,      
    CONFIG_DIR,       
    t,                
    joueur_autocomplete,
    setup_embed_footer,
    load_dungeons_async, 
    save_dungeons_async,
    get_cached_data,
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

# ==========================================
# LE COMPOSANT UI : VÉRIFIER & RELANCER (2H MAX)
# ==========================================
class FortressActionView(discord.ui.View):
    def __init__(self, cog, user_id: str, cibles: list, joueur: str, serveur: str, langue: str = "fr"):
        super().__init__(timeout=7200)
        self.cog = cog
        self.user_id = user_id
        self.cibles = cibles
        self.joueur = joueur
        self.serveur = serveur
        self.langue = langue

        self.btn_verify = discord.ui.Button(
            style=discord.ButtonStyle.secondary, 
            label=t(langue, "fort_btn_verify", defaut="Vérifier ces cibles"),
            emoji="<:search:1512504654183792690>",
            custom_id="btn_fort_verify"
        )
        self.btn_verify.callback = self.callback_verify

        self.btn_relaunch = discord.ui.Button(
            style=discord.ButtonStyle.primary, 
            label=t(langue, "fort_btn_relaunch", defaut="Relancer (10 suivantes)"),
            emoji="<:refresh:1533433306610274425>",
            custom_id="fort_btn_relaunch"
        )
        self.btn_relaunch.callback = self.callback_relaunch

        self.add_item(self.btn_verify)
        self.add_item(self.btn_relaunch)

    async def callback_verify(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=False)
        await self.cog.verify_and_send(interaction, self.cibles, self.joueur, self.serveur, self.langue)

    async def callback_relaunch(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=False)
        await self.cog.relaunch_scan(interaction, self.user_id, self.langue)


# ==========================================
# 🏰 LE COG FORTERESSES
# ==========================================
@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
class ForteressesCog(commands.GroupCog, group_name="fortress", group_description="Fortress Radar (Sands, Ice, Peaks)"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()
        
        # 🎨 PALETTE VERT RADAR / EMERAUDE
        self.clr_activation = discord.Color.from_rgb(180,238,180)
        self.clr_attente = discord.Color.from_rgb(255,195,160)
        self.base_api = "https://api.gge-tracker.com/api/v1"

    async def cog_load(self):
        if not self.dungeon_spy_task.is_running():
            self.dungeon_spy_task.start()

    async def cog_unload(self):
        self.dungeon_spy_task.cancel()

    # ==========================================
    # 🧠 MÉTHODE : VÉRIFICATION EN DIRECT
    # ==========================================
    async def verify_and_send(self, interaction: discord.Interaction, cibles: list, joueur: str, serveur: str, langue: str):
        headers = await get_api_headers(custom_server=serveur)
        safe_joueur = urllib.parse.quote(joueur)
        kids = list(set([c["kid"] for c in cibles]))
        
        kids_str = "%5B" + ",".join(f"%22{k}%22" for k in kids) + "%5D"
        etats = {}
        
        # 🟢 UNE SEULE REQUÊTE MAGIQUE POUR TOUT RÉCUPÉRER D'UN COUP
        url = f"{self.base_api}/dungeons?page=1&size=4000&filterByKid={kids_str}&filterByPlayerName={safe_joueur}&nearPlayerName={safe_joueur}"
        
        try:
            async with self.bot.session.get(url, headers=headers, timeout=10) as r:
                if r.status == 200:
                    dungeons = (await r.json()).get("dungeons", [])
                    maintenant_ts = int(discord.utils.utcnow().timestamp())
                    
                    for d in dungeons:
                        status = "libre"
                        cd_until = d.get("effective_cooldown_until", "")
                        
                        # Si la forteresse a un cooldown dans le futur, elle est en feu
                        if cd_until:
                            try:
                                ts_cd = int(datetime.fromisoformat(cd_until.replace('Z', '+00:00')).timestamp())
                                if ts_cd > maintenant_ts:
                                    status = "feu"
                            except: pass
                            
                        etats[f"{d['kid']}_{d['position_x']}_{d['position_y']}"] = {
                            "status": status, 
                            "cd_until": cd_until,
                            "last_attack": d.get("last_attack", "")
                        }
        except Exception as e:
            logger.error(f"❌ [VERIFY] Erreur API lors de la vérification globale : {e}")

        # -----------------------------------------------
        # La suite du code reste exactement pareille !
        # -----------------------------------------------
        embed = discord.Embed(
            title=t(langue, "fort_verify_title", defaut="🔍 Résultat de la vérification"), 
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )

    # ==========================================
    # 🧠 MÉTHODE : RELANCE (LES 10 SUIVANTES)
    # ==========================================
    async def relaunch_scan(self, interaction: discord.Interaction, user_id: str, langue: str):
        data = await load_dungeons_async()
        if user_id not in data.get("sessions", {}):
            msg_err = t(langue, "fort_err_no_session", defaut="<:warning:1534907226689634404> Ta session de scan est terminée ou introuvable. Relance `/fortress scan` pour en démarrer une nouvelle.")
            return await interaction.followup.send(msg_err, ephemeral=False)
            
        info = data["sessions"][user_id]
        joueur = info["joueur"]
        kids = info["kids"]
        dist = info["distance_max"]
        notified = info.get("notified", [])
        serveur = info.get("serveur", "E4K_FR1")
        
        modifie, embed_cibles, cibles_list = await self.fetch_cibles(joueur, kids, dist, notified, self.bot.session, serveur=serveur, langue=langue)
        
        if modifie:
            info["notified"] = notified
            maintenant = discord.utils.utcnow()
            freq = info.get("frequence_minutes", 5)
            info["next_scan"] = (maintenant + timedelta(minutes=freq)).isoformat().replace('+00:00', 'Z')
            data["sessions"][user_id] = info
            await save_dungeons_async(data)
            
        if cibles_list:
            view = FortressActionView(self, user_id, cibles_list, joueur, serveur, langue)
            await setup_embed_footer(embed_cibles, interaction, langue)
            await interaction.followup.send(embed=embed_cibles, view=view, ephemeral=False)
        else:
            embed_attente = discord.Embed(
                title=t(langue, "fort_wait_title", defaut="📭 Calme plat sur les frontières..."),
                color=self.clr_attente,
                description=t(langue, "fort_wait_desc", defaut="😴 **Aucune forteresse n'est attaquable ou proche de s'ouvrir.**\nToutes les structures aux alentours sont verrouillées en phase de reconstruction.\n\n*Le guet reste en alerte invisible et te contactera par MP dès qu'un mur redevient vulnérable !* ☕")
            )
            await setup_embed_footer(embed_attente, interaction, langue)
            await interaction.followup.send(embed=embed_attente, ephemeral=False)

    # ==========================================
    # 🗺️ TRI INTELLIGENT (PLUS PROCHE VOISIN)
    # ==========================================
    def sort_targets_by_path(self, cibles: list) -> list:
        if not cibles: return []
        
        from collections import defaultdict
        by_kid = defaultdict(list)
        for c in cibles:
            by_kid[c['kid']].append(c)
            
        result = []
        for kid, items in by_kid.items():
            current = items.pop(0)
            result.append(current)
            
            while items:
                best_idx = 0
                best_dist = float('inf')
                for i, candidate in enumerate(items):
                    dist_sq = (current['x'] - candidate['x'])**2 + (current['y'] - candidate['y'])**2
                    if dist_sq < best_dist:
                        best_dist = dist_sq
                        best_idx = i
                
                current = items.pop(best_idx)
                result.append(current)
                
        return result

    # ==========================================
    # 🧠 LE MOTEUR DE RECHERCHE EN CASCADE
    # ==========================================
    async def fetch_cibles(self, joueur: str, kids_to_scan: list, dist_max: int, notified: list, session: aiohttp.ClientSession, serveur: str = "E4K_FR1", langue: str = "fr"):
        headers = await get_api_headers(custom_server=serveur)
        
        dict_royaumes = {
            1: t(langue, "fort_realm_sands", defaut="Sables <:dungeon1:1512573842277794062>"), 
            2: t(langue, "fort_realm_ice", defaut="Glaces <:dungeon2:1512573843267518546>"), 
            3: t(langue, "fort_realm_peaks", defaut="Pics <:dungeon3:1512573844538396692>")
        }
        safe_joueur = urllib.parse.quote(joueur)
        mode_secours_actif = False

        kids_str = "%5B" + ",".join(f"%22{k}%22" for k in kids_to_scan) + "%5D"

        # ------------------------------------------------------------------
        # 🟢 PASSE 1 : RECHERCHE DES CIBLES DISPONIBLES IMMÉDIATEMENT
        # ------------------------------------------------------------------
        cibles_dispo = []
        url = f"{self.base_api}/dungeons?page=1&size=2000&filterByKid={kids_str}&filterByPlayerName={safe_joueur}&nearPlayerName={safe_joueur}&filterByAttackCooldown=1"
        try:
            response_data = None
            async with session.get(url, headers=headers, timeout=10) as r:
                if r.status == 200:
                    response_data = await r.json()
                elif r.status == 400:
                    erreur_txt = await r.text()
                    if "invalid kid" in erreur_txt.lower() or "not found" in erreur_txt.lower():
                        url_secours = f"{self.base_api}/dungeons?page=1&size=2000&filterByKid={kids_str}&filterByPlayerName={safe_joueur}&filterByAttackCooldown=1"
                        async with session.get(url_secours, headers=headers, timeout=10) as r_secours:
                            if r_secours.status == 200:
                                response_data = await r_secours.json()
                                mode_secours_actif = True

            if response_data:
                dungeons = response_data.get("dungeons", [])
                for d in dungeons:
                    kid_dungeon = d.get("kid")
                    raw_dist = d.get("distance")
                    try: dist = float(raw_dist)
                    except: dist = -1.0

                    if dist != -1.0 and dist > dist_max: continue
                    
                    x, y = d.get("position_x"), d.get("position_y")
                    uid = f"{kid_dungeon}_{x}_{y}_1"
                    
                    unk_player = t(langue, "fort_unknown_player", defaut="Inconnu")
                    unk_realm = t(langue, "fort_unknown_realm", kid=kid_dungeon, defaut=f"Monde {kid_dungeon}")
                    
                    if uid not in notified:
                        cibles_dispo.append({
                            "uid": uid, "kid": kid_dungeon, "x": x, "y": y, "dist": dist, "cooldown": 0,
                            "ancien": d.get("player_name", unk_player),
                            "royaume": dict_royaumes.get(kid_dungeon, unk_realm)
                        })
        except Exception as e:
            logger.error(f"❌ [FORTERESSES] Erreur Passe 1: {e}")

        if cibles_dispo:
            cibles_dispo.sort(key=lambda c: c["dist"] if c["dist"] != -1.0 else 99999)
            cibles_a_envoyer = cibles_dispo[:10]
            
            cibles_a_envoyer = self.sort_targets_by_path(cibles_a_envoyer)
            
            sessions_modifiees = False
            for c in cibles_a_envoyer:
                notified.append(c["uid"])
                sessions_modifiees = True

            titre = t(langue, "fort_title_avail", defaut="\<:attaque:1512570903886692474> CIBLES DISPONIBLES IMMÉDIATEMENT")
            if mode_secours_actif: titre += t(langue, "fort_title_rescue", defaut=" 🚑 (Mode Secours)")
            
            embed = discord.Embed(title=titre, color=discord.Color.green())
            embed.description = t(langue, "fort_desc_avail", count=len(cibles_a_envoyer), defaut=f"Le guet a repéré **{len(cibles_a_envoyer)}** forteresse(s) prête(s) au pillage :")
            
            for idx, c in enumerate(cibles_a_envoyer, start=1):
                dist_str = t(langue, "fort_dist_val", dist=int(c['dist']), defaut=f"**{int(c['dist'])}** lieues") if c['dist'] != -1.0 else t(langue, "fort_dist_unk", defaut="<:search:1512504654183792690> Inconnue")
                name_f = t(langue, "fort_field_name", idx=idx, royaume=c['royaume'], dist_str=dist_str, defaut=f"{idx}. {c['royaume']} ({dist_str})")
                val_f = t(langue, "fort_field_val_avail", x=c['x'], y=c['y'], ancien=c['ancien'], defaut=f"<:compass:1512504625364729987> Position : `{c['x']}:{c['y']}` <:empirerankings:1512574698301423847> Dernier roi : *{c['ancien']}*\n**Statut : <:2_:1512574740915818527> Attaquable maintenant**")
                
                embed.add_field(name=name_f, value=val_f, inline=False)
            
            await setup_embed_footer(embed, None, langue)
            return sessions_modifiees, embed, cibles_a_envoyer

        # ------------------------------------------------------------------
        # ⏳ PASSE 2 : AUCUNE FORTERESSE PRÊTE ➔ ANALYSE DES COOLDOWNS
        # ------------------------------------------------------------------
        cibles_en_cooldown = []
        for cd_filter in [2, 3]:
            url = f"{self.base_api}/dungeons?page=1&size=2000&filterByKid={kids_str}&filterByPlayerName={safe_joueur}&nearPlayerName={safe_joueur}&filterByAttackCooldown={cd_filter}"
            try:
                response_data = None
                async with session.get(url, headers=headers, timeout=10) as r:
                    if r.status == 200: response_data = await r.json()

                if response_data:
                    dungeons = response_data.get("dungeons", [])
                    for d in dungeons:
                        kid_dungeon = d.get("kid")
                        raw_dist = d.get("distance")
                        try: dist = float(raw_dist)
                        except: dist = -1.0

                        if dist != -1.0 and dist > dist_max: continue
                        
                        raw_cd = d.get("available_duration_seconds", d.get("cooldown", 0))
                        try: cd_secondes = int(raw_cd)
                        except: cd_secondes = 99999

                        x, y = d.get("position_x"), d.get("position_y")
                        uid = f"{kid_dungeon}_{x}_{y}_{cd_secondes}"

                        unk_player = t(langue, "fort_unknown_player", defaut="Inconnu")
                        unk_realm = t(langue, "fort_unknown_realm", kid=kid_dungeon, defaut=f"Monde {kid_dungeon}")

                        if uid not in notified:
                            cibles_en_cooldown.append({
                                "uid": uid, "kid": kid_dungeon, "x": x, "y": y, "dist": dist, "cooldown": cd_secondes,
                                "ancien": d.get("player_name", unk_player),
                                "royaume": dict_royaumes.get(kid_dungeon, unk_realm)
                            })
            except Exception as e:
                logger.error(f"❌ [FORTERESSES] Erreur Passe 2 (Filtre {cd_filter}): {e}")

        if not cibles_en_cooldown:
            return False, None, []

        cibles_moins_5min = [c for c in cibles_en_cooldown if c["cooldown"] <= 300]
        cibles_moins_1h = [c for c in cibles_en_cooldown if 300 < c["cooldown"] <= 3600]

        cibles_finales = []
        embed_color = discord.Color.gold()
        embed_title = ""
        embed_desc = ""
        status_label = ""

        if cibles_moins_5min:
            cibles_moins_5min.sort(key=lambda c: c["cooldown"])
            cibles_finales = cibles_moins_5min[:10]
            embed_title = t(langue, "fort_title_imminent", defaut="<:time:1512573766096654458> SURVEILLANCE : Cibles imminentes (< 5 min)")
            embed_color = discord.Color.orange()
            embed_desc = t(langue, "fort_desc_imminent", defaut="Aucune cible libre de suite, mais le guet a identifié ces structures sur le point de s'ouvrir :")
            status_label = t(langue, "fort_status_imminent", defaut="<:deeporangebullet:1534908821925920818> Ouverture dans")
        elif cibles_moins_1h:
            cibles_moins_1h.sort(key=lambda c: c["cooldown"])
            cibles_finales = cibles_moins_1h[:10]
            embed_title = t(langue, "fort_title_anticip", defaut="<:time:1512573766096654458> ANTICIPATION : Cibles en recharge (< 1 heure)")
            embed_color = discord.Color.red()
            embed_desc = t(langue, "fort_desc_anticip", defaut="Zone calme pour l'instant. Voici les prochaines cibles disponibles dans l'heure pour préparer tes calages :")
            status_label = t(langue, "fort_status_anticip", defaut="<:tomatobulletpoint:1533440866063224933> Verrouillée pendant")

        if cibles_finales:

            sessions_modifiees = False
            for c in cibles_finales:
                notified.append(c["uid"])
                sessions_modifiees = True

            embed = discord.Embed(title=embed_title, color=embed_color)
            embed.description = embed_desc
            
            for idx, c in enumerate(cibles_finales, start=1):
                dist_str = t(langue, "fort_dist_val", dist=int(c['dist']), defaut=f"**{int(c['dist'])}** lieues") if c['dist'] != -1.0 else t(langue, "fort_dist_unk", defaut="<:search:1512504654183792690> Inconnue")
                minutes_restantes = max(1, int(c['cooldown'] // 60))
                
                name_f = t(langue, "fort_field_name", idx=idx, royaume=c['royaume'], dist_str=dist_str, defaut=f"{idx}. {c['royaume']} ({dist_str})")
                val_f = t(langue, "fort_field_val_cd", x=c['x'], y=c['y'], ancien=c['ancien'], status=status_label, mins=minutes_restantes, defaut=f"<:compass:1512504625364729987> Position : `{c['x']}:{c['y']}` <:empirerankings:1512574698301423847> Dernier roi : *{c['ancien']}*\n**Statut : {status_label} {minutes_restantes} min**")
                
                embed.add_field(name=name_f, value=val_f, inline=False)
            
            await setup_embed_footer(embed, None, langue)
            return sessions_modifiees, embed, cibles_finales

        return False, None, []

    # ==========================================
    # 🟢 COMMANDE : SCAN
    # ==========================================
    @app_commands.command(name="scan", description="Activates the automatic radar of the free fortresses")
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.describe(
        player="The player's username to center the search on",
        ice="Monitor the Everwinter Glacier? (Default: False)",
        sands="Monitor the Burning Sands? (Default: False)",
        peaks="Monitor the Fire Peaks? (Default: False)"
    )
    async def f_scan(
        self, 
        interaction: discord.Interaction, 
        player: str, 
        ice: bool = False, 
        sands: bool = False, 
        peaks: bool = False
    ):
        await interaction.response.defer(ephemeral=False, thinking=True)
        
        langue, serveur = await get_server_config(interaction)

        DIST_MAX = 999 
        nom_joueur = player.strip()
        
        kids = []
        if sands: kids.append(1)
        if ice: kids.append(2)
        if peaks: kids.append(3)
        
        if not kids:
            err_msg = t(langue, "fort_err_no_realms", defaut="<:error:1512505075220611172> Tu as laissé tous les mondes sur `False`. Active au moins un royaume !")
            return await interaction.followup.send(err_msg, ephemeral=False)

        data = await load_dungeons_async()
        user_id = str(interaction.user.id)

        # 💡 AJOUT : Avertissement si une session existe déjà
        if user_id in data.get("sessions", {}):
            msg_warn = t(langue, "fort_warn_overwrite", defaut="<:warning:1534907226689634404> **Attention :** Tu avais déjà un radar en cours ! Il vient d'être réinitialisé. Pense à utiliser `/fortress stop` la prochaine fois pour éviter de recevoir des alertes en double.")
            try:
                await interaction.followup.send(msg_warn, ephemeral=True)
            except: pass

        nb_sessions_actives = len(data.get("sessions", {}))
        freq_val = min(20, 5 + (nb_sessions_actives // 3))
        duree_val = max(40, 120 - ((nb_sessions_actives // 3) * 10))

        maintenant = discord.utils.utcnow()
        end_time = maintenant + timedelta(minutes=duree_val)
        
        info_session = {
            "joueur": nom_joueur, "kids": kids, "distance_max": DIST_MAX,
            "frequence_minutes": freq_val,
            "end_time": end_time.isoformat().replace('+00:00', 'Z'),
            "next_scan": (maintenant + timedelta(minutes=freq_val)).isoformat().replace('+00:00', 'Z'),
            "notified": [],
            "serveur": serveur 
        }

        modifie, embed_cibles, cibles_list = await self.fetch_cibles(nom_joueur, kids, DIST_MAX, info_session["notified"], self.bot.session, serveur=serveur, langue=langue)

        data["sessions"][user_id] = info_session
        await save_dungeons_async(data)

        ts_fin = int(end_time.timestamp())
        noms_royaumes = []
        if 1 in kids: noms_royaumes.append(t(langue, "fort_realm_sands", defaut="Sables <:dungeon1:1512573842277794062>"))
        if 2 in kids: noms_royaumes.append(t(langue, "fort_realm_ice", defaut="Glaces <:dungeon2:1512573843267518546>"))
        if 3 in kids: noms_royaumes.append(t(langue, "fort_realm_peaks", defaut="Pics <:dungeon3:1512573844538396692>"))
        
        st_fluide = t(langue, "fort_status_fluid", defaut="<:greencirclebullet:1533440867598340186> Fluide")
        st_mod = t(langue, "fort_status_mod", defaut="<:deeporangebullet:1534908821925920818> Modérée")
        st_elev = t(langue, "fort_status_high", defaut="<:tomatobulletpoint:1533440866063224933> Élevée")
        status_charge = st_fluide if nb_sessions_actives < 4 else st_mod if nb_sessions_actives < 8 else st_elev

        embed_conf = discord.Embed(title=t(langue, "fort_setup_title", defaut="<:fortresses:1512574700839239892> Dispositif de Guet Activé"), color=self.clr_activation)
        embed_conf.add_field(name=t(langue, "fort_field_player", defaut="<:players:1512504277392953426> Joueur"), value=f"`{nom_joueur}`", inline=False)
        embed_conf.add_field(name=t(langue, "fort_field_realms", defaut="<:icon_world:1512517516012814537> Mondes"), value=" • ".join(noms_royaumes), inline=False)
        embed_conf.add_field(name=t(langue, "fort_field_freq", defaut="⏱️ Fréquence"), value=f"`{freq_val} min`", inline=True)
        embed_conf.add_field(name=t(langue, "fort_field_end", defaut="⏳ Fin du guet"), value=f"<t:{ts_fin}:R>", inline=True)
        embed_conf.add_field(name=t(langue, "fort_field_load", defaut="📊 Charge Bot"), value=f"`{status_charge}`", inline=True)
        await setup_embed_footer(embed_conf, interaction, langue)
        await interaction.followup.send(embed=embed_conf, ephemeral=False)
        
        if cibles_list:
            view = FortressActionView(self, user_id, cibles_list, nom_joueur, serveur, langue)
            await setup_embed_footer(embed_cibles, interaction, langue)
            await interaction.followup.send(embed=embed_cibles, view=view, ephemeral=False)
        else:
            embed_attente = discord.Embed(
                title=t(langue, "fort_wait_title", defaut="📭 Calme plat sur les frontières..."),
                color=self.clr_attente,
                description=t(langue, "fort_wait_desc", defaut="😴 **Aucune forteresse n'est attaquable ou proche de s'ouvrir (rien à moins d'une heure).**\nToutes les structures aux alentours sont verrouillées en phase de reconstruction.\n\n*Profites-en pour former de nouvelles troupes, gérer ton alliance ou t'accorder une pause café. Le guet reste en alerte invisible et te contactera par MP dès qu'un mur redevient vulnérable !* ☕")
            )
            await setup_embed_footer(embed_attente, interaction, langue)
            await interaction.followup.send(embed=embed_attente, ephemeral=False)

    # ==========================================
    # 🔴 COMMANDE : STOP
    # ==========================================
    @app_commands.command(name="stop", description="Stop your fortress scanning session")
    async def f_stop(self, interaction: discord.Interaction):
        try: await interaction.response.defer(ephemeral=False, thinking=True)
        except: return

        langue, _ = await get_server_config(interaction)
        data = await load_dungeons_async()
        user_id = str(interaction.user.id)

        if user_id in data["sessions"]:
            del data["sessions"][user_id]
            await save_dungeons_async(data)
            await interaction.followup.send(t(langue, "fort_stop_success", defaut="<:tomatobulletpoint:1533440866063224933> **Session arrêtée.** Fin des alertes."), ephemeral=False)
        else:
            await interaction.followup.send(t(langue, "fort_stop_fail", defaut="<:error:1512505075220611172> Tu n'as aucune session active."), ephemeral=False)

    # ==========================================
    # 🛰️ LA TÂCHE DE FOND
    # ==========================================
    @tasks.loop(minutes=1)
    async def dungeon_spy_task(self):
        try:
            data = await load_dungeons_async()
            sessions = data.get("sessions", {})
            if not sessions: return
            
            users_data = {}
            path_users = CONFIG_DIR / 'users.json'
            if os.path.exists(path_users):
                try:
                    with open(path_users, 'r', encoding='utf-8') as f:
                        users_data = json.load(f)
                except: pass

            maintenant = discord.utils.utcnow()
            sessions_modifiees = False
            
            for user_id, info in list(sessions.items()):
                joueur = info.get("joueur", "Inconnu")
                serveur = info.get("serveur", "E4K_FR1") 
                langue = users_data.get(user_id, {}).get("langue", "fr")
                
                try:
                    end_dt = datetime.fromisoformat(info["end_time"].replace('Z', '+00:00'))
                    if maintenant > end_dt:
                        del data["sessions"][user_id]
                        sessions_modifiees = True
                        try:
                            user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                            msg = t(langue, "fort_session_ended", joueur=joueur, defaut=f"<:Information:1533430015264555099> **Fin de session !** Ton radar pour **{joueur}** s'est éteint.")
                            await user.send(msg)
                        except: pass
                        continue
                except: pass

                try:
                    next_scan_dt = datetime.fromisoformat(info["next_scan"].replace('Z', '+00:00'))
                    if maintenant < next_scan_dt: continue 
                except: pass

                kids = info.get("kids", [2])
                dist = info.get("distance_max", 999)
                if "notified" not in info: info["notified"] = []
                
                modifie, embed_cibles, cibles_list = await self.fetch_cibles(joueur, kids, dist, info["notified"], self.bot.session, serveur=serveur, langue=langue)
                
                freq = info.get("frequence_minutes", 5)
                info["next_scan"] = (maintenant + timedelta(minutes=freq)).isoformat().replace('+00:00', 'Z')
                sessions_modifiees = True
                
                if cibles_list:
                    try:
                        user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                        view = FortressActionView(self, user_id, cibles_list, joueur, serveur, langue)
                        await user.send(embed=embed_cibles, view=view)
                    except Exception as e: 
                        logger.error(f"❌ [FORTERESSES] Erreur d'envoi MP à {user_id}: {e}")

            if sessions_modifiees:
                await save_dungeons_async(data)

        except Exception as e:
            logger.error(f"❌ [FORTERESSES CRASH] : {traceback.format_exc()}")

    # ==========================================
    # 📜 Forteresses : HISTORY
    # ==========================================
    @app_commands.command(name="history", description="View a player's fortress attack history (up to 365 days)")
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def f_history(self, interaction: discord.Interaction, player: str):
        try: await interaction.response.defer(ephemeral=False, thinking=True)
        except: return

        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(custom_server=serveur)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="<:time:1512573766096654458> **Données datées de :**")

        player_id = None
        vrai_nom = player
        cache = await get_cached_data(serveur)
        local_data = cache.get('players_data', {})

        for p_name, p_info in local_data.items():
            if p_name.lower() == player.lower():
                player_id = str(p_info.get('player_id', p_info.get('id', '')))
                vrai_nom = p_name 
                break

        if not player_id:
            try:
                async with self.bot.session.get(f"{self.base_api}/players/{urllib.parse.quote(player)}", headers=headers, timeout=8) as r:
                    if r.status == 200:
                        p_data = await r.json()
                        if isinstance(p_data, list) and p_data:
                            p_data = p_data[0]
                            player_id = str(p_data.get("player_id", p_data.get("id", "")))
                            vrai_nom = p_data.get("player_name", player)
            except: pass

        if not player_id:
            return await interaction.followup.send(t(langue, "ev_woa_player_not_found", p=player, defaut=f"<:error:1512505075220611172> Joueur **{player}** introuvable sur le serveur {serveur}."))

        url_history = f"{self.base_api}/dungeons/player/{player_id}?lastDays=365"
        try:
            async with self.bot.session.get(url_history, headers=headers, timeout=12) as r:
                if r.status != 200:
                    return await interaction.followup.send(t(langue, "fort_err_api", defaut="<:error:1512505075220611172> Impossible de récupérer l'historique depuis l'API."))
                data = await r.json()
                dungeons = data.get("dungeons", [])
        except Exception as e:
            logger.error(f"❌ Erreur API Fortress History : {e}")
            return await interaction.followup.send(t(langue, "ev_err_tech", e=str(e), defaut=f"<:error:1512505075220611172> Erreur technique : {e}"))

        if not dungeons:
            return await interaction.followup.send(t(langue, "fort_hist_empty", p=vrai_nom, defaut=f"📭 **{vrai_nom}** n'a attaqué aucune forteresse enregistrée durant l'année écoulée."))

        actualisation_dt = _get_api_timestamp(data)

        total_hits = len(dungeons)
        
        dict_royaumes = {
            1: t(langue, "fort_realm_sands", defaut="Sables <:dungeon1:1512573842277794062>"), 
            2: t(langue, "fort_realm_ice", defaut="Glaces <:dungeon2:1512573843267518546>"), 
            3: t(langue, "fort_realm_peaks", defaut="Pics <:dungeon3:1512573844538396692>")
        }
        kid_counts = Counter(d.get("kid") for d in dungeons if d.get("kid"))
        
        total_rubies = (kid_counts.get(1, 0) * 280) + (kid_counts.get(2, 0) * 50) + (kid_counts.get(3, 0) * 370)
        rubies_str = f"{total_rubies:,}".replace(",", " ")
        
        best_kid, best_kid_count = kid_counts.most_common(1)[0]
        royaume_prefere = dict_royaumes.get(best_kid, f"Monde {best_kid}")
        pct_kid = (best_kid_count / total_hits) * 100

        date_counts = Counter(d.get("attacked_at", "")[:10] for d in dungeons if d.get("attacked_at"))
        best_date_str, best_date_count = date_counts.most_common(1)[0]
        try:
            best_date_obj = datetime.strptime(best_date_str, "%Y-%m-%d")
            jour_actif_str = best_date_obj.strftime("%d/%m/%Y")
        except:
            jour_actif_str = best_date_str

        stats_desc = t(langue, "fort_hist_stats", 
            tot=total_hits, 
            rp=royaume_prefere, 
            pct=f"{pct_kid:.1f}", 
            j=jour_actif_str, 
            jc=best_date_count,
            rub=rubies_str, 
            defaut=(
                f"**<:stats:1512517930490003726> Bilan Annuel**\n"
                f"<:attaque:1512570903886692474> **Total des attaques :** {total_hits}\n"
                f"<:ruby:1520135951576334536> **Gains estimés :** {rubies_str} Rubis\n"
                f"<:words:1512574697223753798> **Royaume favori :** {royaume_prefere} *( {pct_kid:.1f}% des frappes )*\n"
                f"<:crossbowman:1533429421581533244> **Jour le plus sanglant :** {jour_actif_str} *( {best_date_count} attaques )*"
            )
        )

        dungeons.sort(key=lambda x: x.get("attacked_at", ""), reverse=True)
        
        lignes = []
        for d in dungeons:
            kid = d.get("kid")
            x = d.get("position_x")
            y = d.get("position_y")
            dt_str = d.get("attacked_at", "")
            
            realm_str = dict_royaumes.get(kid, f"M{kid}")
            try:
                dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                ts = int(dt.timestamp())
                date_fmt = f"<t:{ts}:d> <t:{ts}:t>"
            except:
                date_fmt = "Date inconnue"
                
            lignes.append(f"🔹 {date_fmt} │ {realm_str} ` {x}:{y} `")

        embeds = []
        chunk_size = 10 
        nb_pages = max(1, (len(lignes) - 1) // chunk_size + 1)

        title = t(langue, "fort_hist_title", p=vrai_nom, defaut=f"<:fortresses:1512574700839239892> Historique des Pillages : {vrai_nom}")

        for i in range(0, len(lignes), chunk_size):
            chunk = lignes[i:i+chunk_size]
            page_actuelle = (i // chunk_size) + 1
            
            embed = discord.Embed(title=title, color=self.clr_attente)
            embed.description = f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{stats_desc}"
            
            field_value = ""
            for ligne in chunk:
                if len(field_value) + len(ligne) + 1 > 1000:
                    break 
                field_value += ligne + "\n"
            
            f_title = t(langue, "fort_hist_page_title", curr=page_actuelle, tot=nb_pages, defaut=f"<:memberlist:1512572899360378971> Registre des frappes (Page {page_actuelle}/{nb_pages})")
            embed.add_field(name=f_title, value=field_value if field_value else "-", inline=False)
            
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

async def setup(bot: commands.Bot):
    await bot.add_cog(ForteressesCog(bot))