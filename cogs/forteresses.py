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

from utils import BASE_DATA_PATH, joueur_autocomplete, PaginationView

logger = logging.getLogger("GGE_Bot")

DUNGEONS_FILE = BASE_DATA_PATH / 'dungeons_sessions.json'

def load_dungeons():
    if os.path.exists(DUNGEONS_FILE):
        try:
            with open(DUNGEONS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {"sessions": {}}

def save_dungeons(data):
    with open(DUNGEONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

# ==========================================
# 📝 LE FORMULAIRE POP-UP (Modal)
# ==========================================
class ScanModal(discord.ui.Modal, title='🏰 Formulaire de Radar à Forteresses'):
    joueur = discord.ui.TextInput(
        label='Pseudo du joueur cible', placeholder='Ex: Hydrocat', style=discord.TextStyle.short, required=True
    )
    royaumes = discord.ui.TextInput(
        label='Royaumes (1=Sable, 2=Glaces, 3=Pics)', default='1, 2, 3', placeholder='Ex: 2, 3', style=discord.TextStyle.short, required=True
    )
    distance = discord.ui.TextInput(
        label='Distance Max (en lieues)', default='150', style=discord.TextStyle.short, required=True
    )
    frequence = discord.ui.TextInput(
        label='Fréquence de scan (minutes)', default='5', style=discord.TextStyle.short, required=True
    )
    duree = discord.ui.TextInput(
        label='Durée totale de la session (minutes)', default='120', style=discord.TextStyle.short, required=True
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            dist_val = max(5, min(999, int(self.distance.value)))
            freq_val = max(2, min(60, int(self.frequence.value)))
            duree_val = max(10, min(360, int(self.duree.value)))
            nom_joueur = self.joueur.value.strip()
            
            kids = []
            if "1" in self.royaumes.value: kids.append(1)
            if "2" in self.royaumes.value: kids.append(2)
            if "3" in self.royaumes.value: kids.append(3)
            
            if not kids: return await interaction.followup.send("⚠️ Erreur : Tu dois au moins spécifier un royaume valide (1, 2 ou 3).")

        except ValueError:
            return await interaction.followup.send("⚠️ Erreur : Les cases Distance, Fréquence et Durée doivent être des NOMBRES.")

        data = load_dungeons()
        user_id = str(interaction.user.id)
        maintenant = discord.utils.utcnow()
        end_time = maintenant + timedelta(minutes=duree_val)
        next_scan_time = maintenant + timedelta(minutes=freq_val)
        
        info_session = {
            "joueur": nom_joueur, "kids": kids, "distance_max": dist_val,
            "frequence_minutes": freq_val,
            "end_time": end_time.isoformat().replace('+00:00', 'Z'),
            "next_scan": next_scan_time.isoformat().replace('+00:00', 'Z'),
            "notified": []
        }

        async with aiohttp.ClientSession() as http_session:
            modifie, listes_embeds_royaumes = await self.cog.fetch_cibles(nom_joueur, kids, dist_val, info_session["notified"], http_session)

        data["sessions"][user_id] = info_session
        save_dungeons(data)

        ts_fin = int(end_time.timestamp())
        noms_royaumes = []
        if 1 in kids: noms_royaumes.append("Sables 🏜️")
        if 2 in kids: noms_royaumes.append("Glaces ❄️")
        if 3 in kids: noms_royaumes.append("Pics 🌋")

        embed_conf = discord.Embed(title="📡 Radar à Forteresses Activé !", color=discord.Color.green())
        embed_conf.description = f"Scan des **{', '.join(noms_royaumes)}** activé pour **{nom_joueur}**."
        embed_conf.add_field(name="Fréquence", value=f"Toutes les **{freq_val} min**", inline=True)
        embed_conf.add_field(name="Rayon", value=f"**{dist_val} lieues**", inline=True)
        embed_conf.add_field(name="Fin", value=f"<t:{ts_fin}:R>", inline=True)
        await interaction.followup.send(embed=embed_conf)
        
        if listes_embeds_royaumes:
            for embeds_pages in listes_embeds_royaumes:
                if len(embeds_pages) == 1:
                    await interaction.followup.send(embed=embeds_pages[0])
                else:
                    view = PaginationView(embeds_pages)
                    await interaction.followup.send(embed=embeds_pages[0], view=view)
        else:
            await interaction.followup.send("⏳ *Scan immédiat effectué : Aucune forteresse à portée pour l'instant.*")

# ==========================================
# 🏰 Création du Cog Forteresses
# ==========================================
class ForteressesCog(commands.GroupCog, group_name="forteresse", group_description="🏰 Radar de Farm (Sables, Glaces, Pics)"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def cog_load(self):
        if not self.dungeon_spy_task.is_running():
            self.dungeon_spy_task.start()

    async def cog_unload(self):
        self.dungeon_spy_task.cancel()

    # ==========================================
    # 🧠 LE MOTEUR DE RECHERCHE (Avec Mode Secours)
    # ==========================================
    async def fetch_cibles(self, joueur: str, kids_to_scan: list, dist_max: int, notified: list, session: aiohttp.ClientSession):
        resultats_royaumes = []
        sessions_modifiees = False
        headers = {'User-Agent': 'Mozilla/5.0 GGE-Assistant', 'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        dict_royaumes = {1: "Sables Brûlants 🏜️", 2: "Glacier Éternel ❄️", 3: "Pics de Feu 🌋"}
        safe_joueur = urllib.parse.quote(joueur)

        for kid in kids_to_scan:
            nom_royaume = dict_royaumes.get(kid, f"Royaume {kid}")
            url = f"https://api.gge-tracker.com/api/v1/dungeons?page=1&size=50&filterByKid=[%22{kid}%22]&filterByPlayerName={safe_joueur}&nearPlayerName={safe_joueur}&filterByAttackCooldown=1"
            
            
            try:
                response_data = None
                mode_secours_actif = False

                async with session.get(url, headers=headers, timeout=10) as r:
                    if r.status == 200:
                        response_data = await r.json()
                    elif r.status == 400:
                        erreur_txt = await r.text()
                        # 🚑 DÉCLENCHEMENT DU MODE SECOURS (Si l'API ne trouve pas le joueur)
                        if "invalid kid" in erreur_txt.lower() or "not found" in erreur_txt.lower():
                            logger.warning(f"⚠️ [MODE SECOURS] GGE-Tracker a perdu {joueur} (Royaume {kid}). Activation du Radar Aveugle !")
                            url_secours = f"https://api.gge-tracker.com/api/v1/dungeons?page=1&size=50&filterByKid=[%22{kid}%22]&filterByPlayerName={safe_joueur}&filterByAttackCooldown=1"
                            async with session.get(url_secours, headers=headers, timeout=10) as r_secours:
                                if r_secours.status == 200:
                                    response_data = await r_secours.json()
                                    mode_secours_actif = True
                        else:
                            logger.error(f"❌ [FORTERESSES] Erreur 400 inattendue : {erreur_txt}")
                    else:
                        logger.warning(f"⚠️ [FORTERESSES] Code {r.status} reçu de l'API.")

                # Traitement des données si on en a reçu (Normal ou Secours)
                if response_data:
                    dungeons = response_data.get("dungeons", [])
                    nouvelles_cibles = []
                    
                    for d in dungeons:
                        # Gestion blindée de la distance (Si API renvoie null, "?", ou si on est en mode secours)
                        raw_dist = d.get("distance")
                        try:
                            dist = float(raw_dist)
                        except (ValueError, TypeError):
                            dist = -1.0 # -1 est notre code secret pour "Distance Inconnue"

                        # Si on a une vraie distance et qu'elle est trop loin, on ignore
                        if dist != -1.0 and dist > dist_max: 
                            continue
                        
                        x, y = d.get("position_x"), d.get("position_y")
                        uid = f"{kid}_{x}_{y}"
                        
                        if uid not in notified:
                            notified.append(uid)
                            sessions_modifiees = True
                            nouvelles_cibles.append({
                                "x": x, "y": y, "dist": dist, "ancien": d.get("player_name", "Inconnu")
                            })
                    
                    if nouvelles_cibles:
                        # On trie : Les distances connues d'abord, les inconnues (-1) à la fin
                        nouvelles_cibles.sort(key=lambda c: c["dist"] if c["dist"] != -1.0 else 99999)
                        
                        
                        embeds_pages = []
                        cibles_par_page = 10
                        nb_pages = max(1, (len(nouvelles_cibles) - 1) // cibles_par_page + 1)

                        for i in range(0, len(nouvelles_cibles), cibles_par_page):
                            chunk = nouvelles_cibles[i:i+cibles_par_page]
                            
                            titre = f"🏰 CIBLES LIBRES : {nom_royaume}"
                            if mode_secours_actif: titre += " 🚑 (Mode Secours)"
                                
                            embed = discord.Embed(title=titre, color=discord.Color.gold())
                            embed.description = f"Le radar a repéré **{len(nouvelles_cibles)}** forteresse(s) attaquable(s) !"
                            
                            if nb_pages > 1:
                                embed.set_footer(text=f"Page {i//cibles_par_page + 1}/{nb_pages} • Utilise les boutons")

                            for c in chunk:
                                dist_str = f"**{int(c['dist'])}** lieues" if c['dist'] != -1.0 else "❓ Inconnue"
                                embed.add_field(
                                    name=f"📍 Forteresse ({dist_str})",
                                    value=f"Coords : `{c['x']}:{c['y']}`\nAncien : *{c['ancien']}*\n**Status : 🟢 Attaquable**",
                                    inline=False
                                )
                            embeds_pages.append(embed)
                            
                        resultats_royaumes.append(embeds_pages)

            except Exception as e:
                logger.error(f"❌ [FORTERESSES] Crash lors de l'analyse API pour {joueur} : {traceback.format_exc()}")

        return sessions_modifiees, resultats_royaumes

    # ==========================================
    # 🟢 COMMANDE : SCAN
    # ==========================================
    @app_commands.command(name="scan", description="🏰 Ouvre le formulaire de radar à forteresses")
    async def f_scan(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ScanModal(self))

    # ==========================================
    # 🔴 COMMANDE : STOP
    # ==========================================
    @app_commands.command(name="stop", description="🛑 Arrête ta session de scan de forteresses")
    async def f_stop(self, interaction: discord.Interaction):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return

        data = load_dungeons()
        user_id = str(interaction.user.id)

        if user_id in data["sessions"]:
            del data["sessions"][user_id]
            save_dungeons(data)
            await interaction.followup.send("🛑 **Session arrêtée.** Tu ne recevras plus d'alertes de forteresses.")
        else:
            await interaction.followup.send("⚠️ Tu n'as aucune session de scan active en ce moment.")

    # ==========================================
    # 🛰️ LA TÂCHE DE FOND
    # ==========================================
    @tasks.loop(minutes=1)
    async def dungeon_spy_task(self):
        try:
            data = load_dungeons()
            sessions = data.get("sessions", {})
            if not sessions: return

            maintenant = discord.utils.utcnow()
            sessions_modifiees = False
            

            async with aiohttp.ClientSession() as http_session:
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
                        if maintenant < next_scan_dt:
                            logger.debug(f"💤 [FORTERESSES] Pas encore l'heure pour {joueur}.")
                            continue 
                    except: pass

                    kids = info.get("kids", [2])
                    dist = info.get("distance_max", 150)
                    if "notified" not in info: info["notified"] = []
                    
                    modifie, listes_embeds_royaumes = await self.fetch_cibles(joueur, kids, dist, info["notified"], http_session)
                    
                    freq = info.get("frequence_minutes", 5)
                    info["next_scan"] = (maintenant + timedelta(minutes=freq)).isoformat().replace('+00:00', 'Z')
                    sessions_modifiees = True
                    
                    if listes_embeds_royaumes:
                        try:
                            user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                            for embeds_pages in listes_embeds_royaumes:
                                if len(embeds_pages) == 1:
                                    await user.send(embed=embeds_pages[0])
                                else:
                                    view = PaginationView(embeds_pages)
                                    await user.send(embed=embeds_pages[0], view=view)
                        except Exception as e: 
                            logger.error(f"❌ [FORTERESSES] Erreur d'envoi MP à {user_id}: {e}")

            if sessions_modifiees:
                save_dungeons(data)
                logger.debug("💾 [FORTERESSES] Fichier de sessions mis à jour.")

        except Exception as e:
            logger.error(f"🚨 [FORTERESSES CRASH] : {traceback.format_exc()}")

async def setup(bot: commands.Bot):
    await bot.add_cog(ForteressesCog(bot))