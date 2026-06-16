# -*- coding: utf-8 -*-
import os
import json
import logging
import asyncio
import aiohttp
import urllib.parse
import traceback
from datetime import datetime, timedelta
import discord
from discord import app_commands
from discord.ext import commands, tasks

# 🛠️ Import de la boîte à outils unifiée (Ajout des versions ASYNC)
from utils import (
    BASE_DATA_PATH, 
    joueur_autocomplete,
    setup_embed_footer,
    load_dungeons_async, 
    save_dungeons_async,
    PaginationView
)

logger = logging.getLogger("GGE_Bot")


# ==========================================
# LE COMPOSANT UI : 10 CODES DE COPIE CLIC-TO-COPY
# ==========================================
class DungeonCopyView(discord.ui.View):
    def __init__(self, cibles: list):
        super().__init__(timeout=None)
        self.cibles = cibles

    @discord.ui.button(label="Préparer les copies individuelles", style=discord.ButtonStyle.primary, custom_id="btn_copy_gge")
    async def copy_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cibles:
            return await interaction.response.send_message("<:error:1512505075220611172> Aucune cible disponible.", ephemeral=True)

        premiere_cible = self.cibles[0]
        await interaction.response.send_message(
            content=f"`{premiere_cible['x']}:{premiere_cible['y']}`", 
            ephemeral=True
        )
        
        for c in self.cibles[1:]:
            await asyncio.sleep(0.15) 
            
            await interaction.followup.send(
                content=f"`{c['x']}:{c['y']}`", 
                ephemeral=True
            )


# ==========================================
# 🏰 LE COG FORTERESSES
# ==========================================
class ForteressesCog(commands.GroupCog, group_name="forteresse", group_description="Radar de Forteresse (Sables, Glaces, Pics)"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()
        
        # 🎨 PALETTE VERT RADAR / EMERAUDE
        self.clr_activation = discord.Color.from_rgb(0, 204, 153) # 📡 Menthe Éclatante (Écran d'activation)
        self.clr_attente = discord.Color.from_rgb(61, 140, 107)    # 📭 Vert Sauge / De gris (Écran calme plat)

    async def cog_load(self):
        if not self.dungeon_spy_task.is_running():
            self.dungeon_spy_task.start()

    async def cog_unload(self):
        self.dungeon_spy_task.cancel()

    # ==========================================
    # 🧠 LE MOTEUR DE RECHERCHE EN CASCADE
    # ==========================================
    async def fetch_cibles(self, joueur: str, kids_to_scan: list, dist_max: int, notified: list, session: aiohttp.ClientSession):
        headers = {'User-Agent': 'Mozilla/5.0 GGE-Assistant', 'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        dict_royaumes = {1: "Sables <:dungeon1:1512573842277794062>", 2: "Glaces <:dungeon2:1512573843267518546>", 3: "Pics <:dungeon3:1512573844538396692>"}
        safe_joueur = urllib.parse.quote(joueur)
        mode_secours_actif = False

        # ------------------------------------------------------------------
        # 🟢 PASSE 1 : RECHERCHE DES CIBLES DISPONIBLES IMMÉDIATEMENT
        # ------------------------------------------------------------------
        cibles_dispo = []
        for kid in kids_to_scan:
            url = f"https://api.gge-tracker.com/api/v1/dungeons?page=1&size=50&filterByKid=%5B%22{kid}%22%5D&filterByPlayerName={safe_joueur}&nearPlayerName={safe_joueur}&filterByAttackCooldown=1"
            try:
                response_data = None
                async with session.get(url, headers=headers, timeout=10) as r:
                    if r.status == 200:
                        response_data = await r.json()
                    elif r.status == 400:
                        erreur_txt = await r.text()
                        if "invalid kid" in erreur_txt.lower() or "not found" in erreur_txt.lower():
                            url_secours = f"https://api.gge-tracker.com/api/v1/dungeons?page=1&size=50&filterByKid=%5B%22{kid}%22%5D&filterByPlayerName={safe_joueur}&filterByAttackCooldown=1"
                            async with session.get(url_secours, headers=headers, timeout=10) as r_secours:
                                if r_secours.status == 200:
                                    response_data = await r_secours.json()
                                    mode_secours_actif = True

                if response_data:
                    dungeons = response_data.get("dungeons", [])
                    for d in dungeons:
                        raw_dist = d.get("distance")
                        try: dist = float(raw_dist)
                        except: dist = -1.0

                        if dist != -1.0 and dist > dist_max: continue
                        
                        x, y = d.get("position_x"), d.get("position_y")
                        uid = f"{kid}_{x}_{y}_1"
                        
                        if uid not in notified:
                            cibles_dispo.append({
                                "uid": uid, "x": x, "y": y, "dist": dist, "cooldown": 0,
                                "ancien": d.get("player_name", "Inconnu"),
                                "royaume": dict_royaumes.get(kid, f"Monde {kid}")
                            })
            except Exception as e:
                logger.error(f"❌ [FORTERESSES] Erreur Passe 1 (Kid {kid}): {e}")

        if cibles_dispo:
            cibles_dispo.sort(key=lambda c: c["dist"] if c["dist"] != -1.0 else 99999)
            cibles_a_envoyer = cibles_dispo[:10]
            
            sessions_modifiees = False
            for c in cibles_a_envoyer:
                notified.append(c["uid"])
                sessions_modifiees = True

            titre = "<:667420141394329610:1512573711134490775> CIBLES DISPONIBLES IMMÉDIATEMENT"
            if mode_secours_actif: titre += " 🚑 (Mode Secours)"
            
            embed = discord.Embed(title=titre, color=discord.Color.green())
            embed.description = f"Le guet a repéré **{len(cibles_a_envoyer)}** forteresse(s) prête(s) au pillage :"
            for idx, c in enumerate(cibles_a_envoyer, start=1):
                dist_str = f"**{int(c['dist'])}** lieues" if c['dist'] != -1.0 else "<:search:1512504654183792690> Inconnue"
                embed.add_field(
                    name=f"{idx}. {c['royaume']} ({dist_str})",
                    value=f"<:compass:1512504625364729987> Position : `{c['x']}:{c['y']}` <:empirerankings:1512574698301423847> Dernier roi : *{c['ancien']}*\n**Statut : <:2_:1512574740915818527> Attaquable maintenant**",
                    inline=False
                )
            
            # 🛠️ CORRECTION : Ajout du footer unifié (Sans interaction)
            setup_embed_footer(embed, None)
            return sessions_modifiees, embed, cibles_a_envoyer

        # ------------------------------------------------------------------
        # ⏳ PASSE 2 : AUCUNE FORTERESSE PRÊTE ➔ ANALYSE DES COOLDOWNS
        # ------------------------------------------------------------------
        cibles_en_cooldown = []
        for kid in kids_to_scan:
            url = f"https://api.gge-tracker.com/api/v1/dungeons?page=1&size=50&filterByKid=%5B%22{kid}%22%5D&filterByPlayerName={safe_joueur}&nearPlayerName={safe_joueur}&filterByAttackCooldown=2"
            try:
                response_data = None
                async with session.get(url, headers=headers, timeout=10) as r:
                    if r.status == 200: response_data = await r.json()

                if response_data:
                    dungeons = response_data.get("dungeons", [])
                    for d in dungeons:
                        raw_dist = d.get("distance")
                        try: dist = float(raw_dist)
                        except: dist = -1.0

                        if dist != -1.0 and dist > dist_max: continue
                        
                        raw_cd = d.get("cooldown", 0)
                        try: cd_secondes = int(raw_cd)
                        except: cd_secondes = 99999

                        x, y = d.get("position_x"), d.get("position_y")
                        uid = f"{kid}_{x}_{y}_{cd_secondes}"

                        if uid not in notified:
                            cibles_en_cooldown.append({
                                "uid": uid, "x": x, "y": y, "dist": dist, "cooldown": cd_secondes,
                                "ancien": d.get("player_name", "Inconnu"),
                                "royaume": dict_royaumes.get(kid, f"Monde {kid}")
                            })
            except Exception as e:
                logger.error(f"❌ [FORTERESSES] Erreur Passe 2 (Kid {kid}): {e}")

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
            embed_title = "⏳ SURVEILLANCE : Cibles imminentes (< 5 min)"
            embed_color = discord.Color.orange()
            embed_desc = "Aucune cible libre de suite, mais le guet a identifié ces structures sur le point de s'ouvrir :"
            status_label = "🟠 Ouverture dans"
        elif cibles_moins_1h:
            cibles_moins_1h.sort(key=lambda c: c["cooldown"])
            cibles_finales = cibles_moins_1h[:10]
            embed_title = "🕒 ANTICIPATION : Cibles en recharge (< 1 heure)"
            embed_color = discord.Color.red()
            embed_desc = "Zone calme pour l'instant. Voici les prochaines cibles disponibles dans l'heure pour préparer tes calages :"
            status_label = "🔴 Verrouillée pendant"

        if cibles_finales:
            sessions_modifiees = False
            for c in cibles_finales:
                notified.append(c["uid"])
                sessions_modifiees = True

            embed = discord.Embed(title=embed_title, color=embed_color)
            embed.description = embed_desc
            
            for idx, c in enumerate(cibles_finales, start=1):
                dist_str = f"**{int(c['dist'])}** lieues" if c['dist'] != -1.0 else "<:search:1512504654183792690> Inconnue"
                minutes_restantes = max(1, int(c['cooldown'] // 60))
                embed.add_field(
                    name=f"{idx}. {c['royaume']} ({dist_str})",
                    value=f"<:compass:1512504625364729987> Position : `{c['x']}:{c['y']}` <:empirerankings:1512574698301423847> Dernier roi : *{c['ancien']}*\n**Statut : {status_label} {minutes_restantes} min**",
                    inline=False
                )
            
            # 🛠️ CORRECTION : Remplacement de 'interaction' par 'None'
            setup_embed_footer(embed, None)
            return sessions_modifiees, embed, cibles_finales

        return False, None, []

    # ==========================================
    # 🟢 COMMANDE : SCAN
    # ==========================================
    @app_commands.command(name="scan", description="Active le radar automatique des forteresses libres")
    @app_commands.autocomplete(joueur=joueur_autocomplete)
    @app_commands.describe(
        joueur="Le pseudo du joueur à cibler",
        glaces="Surveiller le Glacier Éternel ? (Défaut: Non)",
        sables="Surveiller les Sables Brûlants ? (Défaut: Non)",
        pics="Surveiller les Pics de Feu ? (Défaut: Non)"
    )
    async def f_scan(
        self, 
        interaction: discord.Interaction, 
        joueur: str, 
        glaces: bool = False, 
        sables: bool = False, 
        pics: bool = False
    ):
        await interaction.response.defer(ephemeral=False, thinking=True)

        DIST_MAX = 999 
        nom_joueur = joueur.strip()
        
        kids = []
        if sables: kids.append(1)
        if glaces: kids.append(2)
        if pics: kids.append(3)
        
        if not kids:
            return await interaction.followup.send("<:error:1512505075220611172> Tu as laissé tous les mondes sur `False`. Active au moins un royaume !")

        # 🔐 Sécurisé : Lecture asynchrone avec verrou
        data = await load_dungeons_async()
        nb_sessions_actives = len(data.get("sessions", {}))
        freq_val = min(20, 5 + (nb_sessions_actives // 3))
        duree_val = max(40, 120 - ((nb_sessions_actives // 3) * 10))

        user_id = str(interaction.user.id)
        maintenant = discord.utils.utcnow()
        end_time = maintenant + timedelta(minutes=duree_val)
        
        info_session = {
            "joueur": nom_joueur, "kids": kids, "distance_max": DIST_MAX,
            "frequence_minutes": freq_val,
            "end_time": end_time.isoformat().replace('+00:00', 'Z'),
            "next_scan": (maintenant + timedelta(minutes=freq_val)).isoformat().replace('+00:00', 'Z'),
            "notified": []
        }

        # ⚡ Optimisation : Utilisation du pool global du bot
        modifie, embed_cibles, cibles_list = await self.fetch_cibles(nom_joueur, kids, DIST_MAX, info_session["notified"], self.bot.session)

        data["sessions"][user_id] = info_session
        # 🔐 Sécurisé : Enregistrement asynchrone avec verrou
        await save_dungeons_async(data)

        ts_fin = int(end_time.timestamp())
        noms_royaumes = []
        if 1 in kids: noms_royaumes.append("Sables <:dungeon1:1512573842277794062>")
        if 2 in kids: noms_royaumes.append("Glaces <:dungeon2:1512573843267518546>")
        if 3 in kids: noms_royaumes.append("Pics <:dungeon3:1512573844538396692>")
        status_charge = "🟢 Fluide" if nb_sessions_actives < 4 else "🟡 Modérée" if nb_sessions_actives < 8 else "🔴 Élevée"

        embed_conf = discord.Embed(title="📡 Dispositif de Guet Activé", color=self.clr_activation)
        embed_conf.add_field(name="<:players:1512504277392953426> Joueur", value=f"`{joueur}`", inline=False)
        embed_conf.add_field(name="<:icon_world:1512517516012814537> Mondes", value=" • ".join(noms_royaumes), inline=False)
        embed_conf.add_field(name="⏱️ Fréquence", value=f"`{freq_val} min`", inline=True)
        embed_conf.add_field(name="⏳ Fin du guet", value=f"<t:{ts_fin}:R>", inline=True)
        embed_conf.add_field(name="📊 Charge Bot", value=f"`{status_charge}`", inline=True)
        setup_embed_footer(embed_conf, interaction)
        await interaction.followup.send(embed=embed_conf)
        
        if cibles_list:
            view = DungeonCopyView(cibles_list)
            setup_embed_footer(embed_cibles, interaction)
            await interaction.followup.send(embed=embed_cibles, view=view)
        else:
            embed_attente = discord.Embed(
                title="📭 Calme plat sur les frontières...",
                color=self.clr_attente,
                description="😴 **Aucune forteresse n'est attaquable ou proche de s'ouvrir (rien à moins d'une heure).**\nToutes les structures aux alentours sont verrouillées en phase de reconstruction.\n\n*Profites-en pour former de nouvelles troupes, gérer ton alliance ou t'accorder une pause café. Le guet reste en alerte invisible et te contactera par MP dès qu'un mur redevient vulnérable !* ☕"
            )
            setup_embed_footer(embed_attente, interaction)
            await interaction.followup.send(embed=embed_attente)

    # ==========================================
    # 🔴 COMMANDE : STOP
    # ==========================================
    @app_commands.command(name="stop", description="Arrête ta session de scan de forteresses")
    async def f_stop(self, interaction: discord.Interaction):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return

        # 🔐 Sécurisé : Chargement asynchrone avec verrou
        data = await load_dungeons_async()
        user_id = str(interaction.user.id)

        if user_id in data["sessions"]:
            del data["sessions"][user_id]
            # 🔐 Sécurisé : Écriture asynchrone avec verrou
            await save_dungeons_async(data)
            await interaction.followup.send("🛑 **Session arrêtée.** Fin des alertes.")
        else:
            await interaction.followup.send("<:error:1512505075220611172> Tu n'as aucune session active.")

    # ==========================================
    # 🛰️ LA TÂCHE DE FOND
    # ==========================================
    @tasks.loop(minutes=1)
    async def dungeon_spy_task(self):
        try:
            # 🔐 Sécurisé : Lecture asynchrone sécurisée
            data = await load_dungeons_async()
            sessions = data.get("sessions", {})
            if not sessions: return

            maintenant = discord.utils.utcnow()
            sessions_modifiees = False
            
            # ⚡ Optimisation : Utilisation du pool global du bot pour tout le traitement
            for user_id, info in list(sessions.items()):
                joueur = info.get("joueur", "Inconnu")
                
                try:
                    end_dt = datetime.fromisoformat(info["end_time"].replace('Z', '+00:00'))
                    if maintenant > end_dt:
                        del data["sessions"][user_id]
                        sessions_modifiees = True
                        try:
                            user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                            await user.send(f"⏰ **Fin de session !** Ton radar pour **{joueur}** s'est éteint.")
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
                
                modifie, embed_cibles, cibles_list = await self.fetch_cibles(joueur, kids, dist, info["notified"], self.bot.session)
                
                freq = info.get("frequence_minutes", 5)
                info["next_scan"] = (maintenant + timedelta(minutes=freq)).isoformat().replace('+00:00', 'Z')
                sessions_modifiees = True
                
                if cibles_list:
                    try:
                        user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                        view = DungeonCopyView(cibles_list)
                        await user.send(embed=embed_cibles, view=view)
                    except Exception as e: 
                        logger.error(f"❌ [FORTERESSES] Erreur d'envoi MP à {user_id}: {e}")

            if sessions_modifiees:
                # 🔐 Sécurisé : Écriture asynchrone protégée
                await save_dungeons_async(data)

        except Exception as e:
            logger.error(f"🚨 [FORTERESSES CRASH] : {traceback.format_exc()}")


async def setup(bot: commands.Bot):
    await bot.add_cog(ForteressesCog(bot))