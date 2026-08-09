# -*- coding: utf-8 -*-
import os
import json
import logging
import asyncio
import aiohttp
import urllib.parse
import traceback
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import (
    BASE_DATA_PATH, 
    CONFIG_DIR,
    t,
    joueur_autocomplete, 
    alliance_autocomplete, 
    format_num, 
    PaginationView,
    setup_embed_footer,
    load_surveillance_async,
    save_surveillance_async,
    load_configuration_async,
    get_discord_timestamp,
    get_server_config,   
    get_api_headers
)

logger = logging.getLogger("GGE_Bot")

RANKS_MAP = {0: "Chef", 1: "Représentant", 2: "Maréchal", 3: "Trésorier", 4: "Diplomate", 5: "Recruteur", 6: "Général", 7: "Sergent", 8: "Membre", 9: "Novice"}

def get_rank_str(rank_int, langue):
    mapping = {
        0: t(langue, "prof_role_0", defaut="Chef"),
        1: t(langue, "prof_role_1", defaut="Représentant"),
        2: t(langue, "prof_role_2", defaut="Maréchal"),
        3: t(langue, "prof_role_3", defaut="Trésorier"),
        4: t(langue, "prof_role_4", defaut="Diplomate"),
        5: t(langue, "prof_role_5", defaut="Recruteur"),
        6: t(langue, "prof_role_6", defaut="Général"),
        7: t(langue, "prof_role_7", defaut="Sergent"),
        8: t(langue, "prof_role_8", defaut="Membre"),
        9: t(langue, "prof_role_9", defaut="Novice")
    }
    return mapping.get(rank_int, t(langue, "prof_role_fallback", r=rank_int, defaut=f"Grade {rank_int}"))

def get_discord_time(iso_str, langue="fr"):
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        return f"<t:{int(dt.timestamp())}:R>" 
    except:
        return t(langue, "rad_time_recent", defaut="Récemment")

# ==========================================
# 🎛️ MENU INTERACTIF DES FILTRES JOUEURS
# ==========================================
class RadarSettingsView(discord.ui.View):
    def __init__(self, p_id: str, user_id: str, player_name: str, initial_prefs: dict, langue: str = "fr"):
        super().__init__(timeout=None)
        self.p_id = p_id
        self.user_id = str(user_id)
        self.player_name = player_name
        self.prefs = initial_prefs  
        self.langue = langue
        
        self.btn_pseudo.label = t(langue, "rad_btn_pseudo", defaut="Pseudo")
        self.btn_position.label = t(langue, "rad_btn_pos", defaut="Position")
        self.btn_alliance.label = t(langue, "rad_btn_alli", defaut="Alliance")
        self.btn_puissance.label = t(langue, "rad_btn_pp", defaut="Puissance")
        self.btn_colombe.label = t(langue, "rad_btn_dove", defaut="Colombe")
        self.btn_fermer.label = t(langue, "rad_btn_close", defaut="Terminer et Fermer")
        self.update_buttons_state()

    async def toggle_pref(self, pref_key: str):
        data = await load_surveillance_async()
        if self.p_id in data.get("players", {}) and self.user_id in data["players"][self.p_id]["abonnes"]:
            current = data["players"][self.p_id]["abonnes"][self.user_id].get(pref_key, False)
            new_val = not current
            data["players"][self.p_id]["abonnes"][self.user_id][pref_key] = new_val
            self.prefs[pref_key] = new_val  
            await save_surveillance_async(data)

    def update_buttons_state(self):
        self.btn_pseudo.style = discord.ButtonStyle.success if self.prefs.get("pseudo") else discord.ButtonStyle.danger
        self.btn_position.style = discord.ButtonStyle.success if self.prefs.get("position") else discord.ButtonStyle.danger
        self.btn_alliance.style = discord.ButtonStyle.success if self.prefs.get("alliance") else discord.ButtonStyle.danger
        self.btn_puissance.style = discord.ButtonStyle.success if self.prefs.get("puissance") else discord.ButtonStyle.danger
        self.btn_colombe.style = discord.ButtonStyle.success if self.prefs.get("colombe") else discord.ButtonStyle.danger

    @discord.ui.button(custom_id="pref_pseudo", row=0)
    async def btn_pseudo(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("pseudo"); self.update_buttons_state(); await interaction.response.edit_message(view=self)

    @discord.ui.button(custom_id="pref_position", row=0)
    async def btn_position(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("position"); self.update_buttons_state(); await interaction.response.edit_message(view=self)

    @discord.ui.button(custom_id="pref_alliance", row=0)
    async def btn_alliance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("alliance"); self.update_buttons_state(); await interaction.response.edit_message(view=self)

    @discord.ui.button(custom_id="pref_puissance", row=1)
    async def btn_puissance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("puissance"); self.update_buttons_state(); await interaction.response.edit_message(view=self)

    @discord.ui.button(custom_id="pref_colombe", row=1)
    async def btn_colombe(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("colombe"); self.update_buttons_state(); await interaction.response.edit_message(view=self)

    @discord.ui.button(emoji="<:greencirclebullet:1533440867598340186>", style=discord.ButtonStyle.secondary, row=2)
    async def btn_fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg = t(self.langue, "rad_saved_player", defaut="✅ **Préférences enregistrées avec succès !** Le radar joueur est actif.")
        await interaction.response.edit_message(content=msg, view=None)

# ==========================================
# 🎛️ MENU INTERACTIF DES FILTRES ALLIANCES
# ==========================================
class RadarAllianceSettingsView(discord.ui.View):
    def __init__(self, a_id: str, user_id: str, alliance_name: str, initial_prefs: dict, langue: str = "fr"):
        super().__init__(timeout=None)
        self.a_id = a_id
        self.user_id = str(user_id)
        self.alliance_name = alliance_name
        self.prefs = initial_prefs
        self.langue = langue
        
        self.btn_mouvements.label = t(langue, "rad_alli_btn_mouv", defaut="Entrées/Sorties")
        self.btn_rangs.label = t(langue, "rad_alli_btn_rangs", defaut="Promotions/Rétrogradations")
        self.btn_infos.label = t(langue, "rad_alli_btn_infos", defaut="Changement Nom/Chef")
        self.btn_fermer.label = t(langue, "rad_btn_close", defaut="Terminer et Fermer")
        self.update_buttons_state()

    async def toggle_pref(self, pref_key: str):
        data = await load_surveillance_async()
        if self.a_id in data.get("alliances", {}) and self.user_id in data["alliances"][self.a_id]["abonnes"]:
            current = data["alliances"][self.a_id]["abonnes"][self.user_id].get(pref_key, False)
            new_val = not current
            data["alliances"][self.a_id]["abonnes"][self.user_id][pref_key] = new_val
            self.prefs[pref_key] = new_val
            await save_surveillance_async(data)

    def update_buttons_state(self):
        self.btn_mouvements.style = discord.ButtonStyle.success if self.prefs.get("mouvements") else discord.ButtonStyle.danger
        self.btn_rangs.style = discord.ButtonStyle.success if self.prefs.get("rangs") else discord.ButtonStyle.danger
        self.btn_infos.style = discord.ButtonStyle.success if self.prefs.get("infos") else discord.ButtonStyle.danger

    @discord.ui.button(custom_id="pref_alli_mouv", row=0)
    async def btn_mouvements(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("mouvements"); self.update_buttons_state(); await interaction.response.edit_message(view=self)

    @discord.ui.button(custom_id="pref_alli_rangs", row=0)
    async def btn_rangs(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("rangs"); self.update_buttons_state(); await interaction.response.edit_message(view=self)

    @discord.ui.button(custom_id="pref_alli_infos", row=0)
    async def btn_infos(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("infos"); self.update_buttons_state(); await interaction.response.edit_message(view=self)

    @discord.ui.button(emoji="<:greencirclebullet:1533440867598340186>", style=discord.ButtonStyle.secondary, row=2)
    async def btn_fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg = t(self.langue, "rad_alli_saved", defaut="✅ **Préférences d'alliance enregistrées !** Le radar global est actif.")
        await interaction.response.edit_message(content=msg, view=None)

# ==========================================
# 📊 MODULE COG RADAR
# ==========================================
@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
class RadarCog(commands.GroupCog, group_name="radar", group_description="Personal War Radar"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_scan_hour = None  
        super().__init__()
        
        self.clr_add_joueur     = discord.Color.from_rgb(255,246,143)
        self.clr_add_alliance   = discord.Color.from_rgb(229,221,128)
        self.clr_list_alliances = discord.Color.from_rgb(183,176,102)
        self.clr_list_joueurs   = discord.Color.from_rgb(128,123,71)
        
        # Cache des langues utilisateurs pour soulager le disque
        self.users_lang_cache = {}
        self.users_cache_mtime = 0

    async def cog_load(self):
        if not self.radar_spy_task.is_running():
            self.radar_spy_task.start()

    async def cog_unload(self):
        self.radar_spy_task.cancel()

    async def envoyer_alerte_privee(self, abonnes: dict, type_filtre: str, embeds_locales: dict, users_lang: dict):
        """Envoie l'alerte MP uniquement aux joueurs qui ont coché ce filtre"""
        for user_id, prefs in abonnes.items():
            if prefs.get(type_filtre, False):
                try:
                    langue = users_lang.get(user_id, "fr")
                    embed = embeds_locales.get(langue, embeds_locales.get("fr"))
                    if not embed: continue
                    
                    await setup_embed_footer(embed, None, langue) 
                    user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                    await user.send(embed=embed)
                except discord.Forbidden:
                    logger.warning(f"⚠️ Impossible d'envoyer un MP à {user_id} (DMs bloqués).")
                except Exception as e:
                    logger.error(f"❌ Erreur MP à {user_id} : {e}")

    # ==========================================
    # 🕵️‍♂️ COMMANDES : RADAR PLAYER
    # ==========================================
    @app_commands.command(name="add", description="Add a player to your personal radar (Limit: 25 followed)")
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.describe(reason="Reason for surveillance")
    async def s_add(self, interaction: discord.Interaction, player: str, reason: str = "Surveillance générale"):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return

        langue, serveur = await get_server_config(interaction)

        p_id, p_might = None, 0
        try:
            player_files = list((BASE_DATA_PATH / 'server_scans' / serveur).rglob('server_*.json'))
            if player_files:
                latest = max(player_files, key=lambda p: p.stat().st_mtime)
                with open(latest, 'r', encoding='utf-8') as f:
                    local_data = json.load(f).get('players', {})
                    for p_name, p_info in local_data.items():
                        if p_name.lower() == player.lower():
                            p_id = str(p_info.get("player_id", p_info.get("id", "")))
                            p_might = int(p_info.get('main_points', 0)) 
                            player = p_name 
                            break
        except: pass

        if not p_id:
            msg = t(langue, "rad_err_not_found_cache", p=player, defaut=f"⚠️ Joueur **{player}** introuvable dans le cache local.")
            return await interaction.followup.send(msg)

        data = await load_surveillance_async()
        user_id = str(interaction.user.id)
        now_str = discord.utils.utcnow().isoformat().replace('+00:00', 'Z')

        already_tracking = user_id in data.get("players", {}).get(p_id, {}).get("abonnes", {})
        if not already_tracking:
            count_tracked = sum(1 for p_info in data.get("players", {}).values() if user_id in p_info.get("abonnes", {}))
            if count_tracked >= 25:
                msg = t(langue, "rad_err_limit_player", cnt=count_tracked, defaut=f"❌ **Limite atteinte** : Suivi maximal `{count_tracked}/25` joueurs. Retire un profil avec `/radar remove` d'abord.")
                return await interaction.followup.send(msg)

        if p_id not in data["players"]:
            data["players"][p_id] = {
                "name": player, "last_alliance": now_str, "last_name": now_str, "last_pos": now_str, "last_might": p_might,
                "peace_disabled_at": None, "is_protected": False, "abonnes": {},
                "serveur": serveur 
            }
        
        if "abonnes" not in data["players"][p_id]: data["players"][p_id]["abonnes"] = {}

        if user_id not in data["players"][p_id]["abonnes"]:
            data["players"][p_id]["abonnes"][user_id] = {
                "raison": reason, "pseudo": False, "position": False, "alliance": False, "puissance": False, "colombe": False
            }
        else:
            msg = t(langue, "rad_err_already_track", p=player, defaut=f"Tu surveilles DÉJÀ **{player}** ! 😉")
            return await interaction.followup.send(msg)

        await save_surveillance_async(data)
        
        embed = discord.Embed(
            title=t(langue, "rad_add_title", j=player, defaut=f"🎯 Cible verrouillée : {player}"), 
            description=t(langue, "rad_add_desc", j=player, r=reason, defaut=f"**{player}** a bien été ajouté à ton radar.\n*(Raison: {reason})*\n\n👇 **Configure tes alertes (Rouge = OFF, Vert = ON) :**"), 
            color=self.clr_add_joueur
        )
        view = RadarSettingsView(p_id, user_id, player, data["players"][p_id]["abonnes"][user_id], langue)
        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="remove", description="Remove a player from your personal radar")
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def s_remove(self, interaction: discord.Interaction, player: str):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return

        langue, _ = await get_server_config(interaction)
        data = await load_surveillance_async()
        user_id = str(interaction.user.id)
        cible_trouvee = False
        
        for pid, info in list(data["players"].items()):
            if info["name"].lower() == player.lower():
                if user_id in info.get("abonnes", {}):
                    cible_trouvee = True
                    del data["players"][pid]["abonnes"][user_id]
                    if not data["players"][pid]["abonnes"]: del data["players"][pid]
                    break

        if cible_trouvee:
            await save_surveillance_async(data)
            msg = t(langue, "rad_rem_succ", j=player, defaut=f"✅ **{player}** a bien été retiré de ton radar personnel.")
            await interaction.followup.send(msg)
        else:
            msg = t(langue, "rad_rem_fail", j=player, defaut=f"⚠️ **{player}** n'est pas dans ton radar.")
            await interaction.followup.send(msg)

    # ==========================================
    # 🛡️ COMMANDES : RADAR ALLIANCE (SOUS-GROUPE)
    # ==========================================
    alliance_group = app_commands.Group(name="alliance", description="Manage the radar of complete alliances")

    @alliance_group.command(name="add", description="Add an entire alliance to your radar (Limit: 3 followed)")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    @app_commands.describe(reason="Why are you monitoring this alliance?")
    async def a_add(self, interaction: discord.Interaction, alliance_name: str, reason: str = "Surveillance globale"):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return

        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)
        
        a_id, a_name_real, members_dict = None, alliance_name, {}

        try:
            url_search = f"https://api.gge-tracker.com/api/v1/alliances/name/{urllib.parse.quote(alliance_name)}"
            async with self.bot.session.get(url_search, headers=headers, timeout=10) as r:
                if r.status == 200:
                    search_data = await r.json()
                    cible = search_data[0] if isinstance(search_data, list) and search_data else search_data
                    a_id = str(cible.get("alliance_id") or cible.get("id"))
                    a_name_real = cible.get("alliance_name", alliance_name)
            
            if a_id:
                url_alli = f"https://api.gge-tracker.com/api/v1/alliances/id/{a_id}"
                async with self.bot.session.get(url_alli, headers=headers, timeout=10) as r:
                    if r.status == 200:
                        alli_data = await r.json()
                        if isinstance(alli_data, list) and alli_data: alli_data = alli_data[0]
                        members = alli_data.get("players", alli_data.get("members", alli_data.get("playerList", [])))
                        
                        for m in members:
                            p_id = str(m.get('player_id', m.get('playerId', '')))
                            if p_id:
                                members_dict[p_id] = {
                                    "name": m.get('player_name', m.get('playerName', 'Inconnu')),
                                    "rank": int(m.get('alliance_rank', 8))
                                }
        except Exception as e:
            logger.error(f"❌ [Radar Alli Add] Erreur API : {e}")
            msg = t(langue, "rad_err_api_join", defaut="❌ Impossible de joindre GGE-Tracker pour trouver cette alliance.")
            return await interaction.followup.send(msg)

        if not a_id:
            msg = t(langue, "rad_err_alli_not_found", a=alliance_name, defaut=f"⚠️ Alliance **{alliance_name}** introuvable.")
            return await interaction.followup.send(msg)

        data = await load_surveillance_async()
        user_id = str(interaction.user.id)

        already_tracking = user_id in data.get("alliances", {}).get(a_id, {}).get("abonnes", {})
        if not already_tracking:
            count_tracked = sum(1 for a_info in data.get("alliances", {}).values() if user_id in a_info.get("abonnes", {}))
            if count_tracked >= 3:
                msg = t(langue, "rad_err_limit_alli", cnt=count_tracked, defaut=f"❌ **Limite atteinte** : Surveillance maximale `{count_tracked}/3` alliances.")
                return await interaction.followup.send(msg)

        if a_id not in data["alliances"]:
            data["alliances"][a_id] = {
                "name": a_name_real, "members": members_dict, "abonnes": {},
                "serveur": serveur
            }
        
        if "abonnes" not in data["alliances"][a_id]: data["alliances"][a_id]["abonnes"] = {}

        if user_id not in data["alliances"][a_id]["abonnes"]:
            data["alliances"][a_id]["abonnes"][user_id] = {
                "raison": reason, "mouvements": False, "rangs": False, "infos": False
            }
        else:
            msg = t(langue, "rad_err_already_track_alli", a=a_name_real, defaut=f"Tu surveilles DÉJÀ l'alliance **{a_name_real}** ! 😉")
            return await interaction.followup.send(msg)

        await save_surveillance_async(data)
        
        embed = discord.Embed(
            title=t(langue, "rad_alli_add_title", a=a_name_real, defaut=f"🛡️ Alliance verrouillée : {a_name_real}"), 
            description=t(langue, "rad_alli_add_desc", a=a_name_real, cnt=len(members_dict), r=reason, defaut=f"Le radar global est activé sur **{a_name_real}** ({len(members_dict)} membres).\n*(Raison: {reason})*\n\n👇 **Configure tes alertes d'alliance (Rouge = OFF, Vert = ON) :**"), 
            color=self.clr_add_alliance
        )
        view = RadarAllianceSettingsView(a_id, user_id, a_name_real, data["alliances"][a_id]["abonnes"][user_id], langue)
        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed, view=view)

    @alliance_group.command(name="remove", description="Remove an alliance from your personal radar")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def a_remove(self, interaction: discord.Interaction, alliance_name: str):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return

        langue, _ = await get_server_config(interaction)
        data = await load_surveillance_async()
        user_id = str(interaction.user.id)
        cible_trouvee = False
        
        for aid, info in list(data.get("alliances", {}).items()):
            if info["name"].lower() == alliance_name.lower():
                if user_id in info.get("abonnes", {}):
                    cible_trouvee = True
                    del data["alliances"][aid]["abonnes"][user_id]
                    if not data["alliances"][aid]["abonnes"]: del data["alliances"][aid]
                    break

        if cible_trouvee:
            await save_surveillance_async(data)
            msg = t(langue, "rad_alli_rem_succ", a=alliance_name, defaut=f"✅ L'alliance **{alliance_name}** a bien été retirée de ton radar.")
            await interaction.followup.send(msg)
        else:
            msg = t(langue, "rad_alli_rem_fail", a=alliance_name, defaut=f"⚠️ L'alliance **{alliance_name}** n'est pas dans ton radar.")
            await interaction.followup.send(msg)

    # ==========================================
    # 📋 COMMANDE : LIST GLOBAL
    # ==========================================
    @app_commands.command(name="list", description="Display your personal radar (Players & Alliances)")
    async def s_list(self, interaction: discord.Interaction):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return
        
        langue, _ = await get_server_config(interaction)
        data = await load_surveillance_async()
        user_id = str(interaction.user.id)
        
        lbl_sr = t(langue, "rad_list_no_reason", defaut="Sans raison")
        
        mes_joueurs = []
        for pid, info in data.get("players", {}).items():
            abonnes = info.get("abonnes", {})
            if user_id in abonnes:
                prefs = abonnes[user_id]
                nom = info["name"]
                raison = prefs.get("raison", lbl_sr)
                
                filtres_actifs = []
                if prefs.get("pseudo"): filtres_actifs.append(t(langue, "rad_btn_pseudo", defaut="Pseudo"))
                if prefs.get("position"): filtres_actifs.append(t(langue, "rad_btn_pos", defaut="Position"))
                if prefs.get("alliance"): filtres_actifs.append(t(langue, "rad_btn_alli", defaut="Alliance"))
                if prefs.get("puissance"): filtres_actifs.append(t(langue, "rad_btn_pp", defaut="Puissance"))
                if prefs.get("colombe"): filtres_actifs.append(t(langue, "rad_btn_dove", defaut="Colombe"))
                
                str_filtres = t(langue, "rad_list_filter_active", f=", ".join(filtres_actifs), defaut=f"✅ {', '.join(filtres_actifs)}") if filtres_actifs else t(langue, "rad_list_no_filter", defaut="❌ Aucun filtre actif")
                mes_joueurs.append(f"🎯 **{nom}** ➔ *{raison}*\n└ {str_filtres}")

        mes_alliances = []
        for aid, info in data.get("alliances", {}).items():
            abonnes = info.get("abonnes", {})
            if user_id in abonnes:
                prefs = abonnes[user_id]
                nom = info["name"]
                raison = prefs.get("raison", lbl_sr)
                
                filtres_actifs = []
                if prefs.get("mouvements"): filtres_actifs.append(t(langue, "rad_alli_btn_mouv", defaut="Mouvements"))
                if prefs.get("rangs"): filtres_actifs.append(t(langue, "rad_alli_btn_rangs", defaut="Rangs"))
                if prefs.get("infos"): filtres_actifs.append(t(langue, "rad_alli_btn_infos", defaut="Infos"))
                
                str_filtres = t(langue, "rad_list_filter_active", f=", ".join(filtres_actifs), defaut=f"✅ {', '.join(filtres_actifs)}") if filtres_actifs else t(langue, "rad_list_no_filter", defaut="❌ Aucun filtre actif")
                mes_alliances.append(f"🛡️ **{nom}** ➔ *{raison}*\n└ {str_filtres}")

        if not mes_joueurs and not mes_alliances:
            msg = t(langue, "rad_list_empty", defaut="🕸️ Ton radar est vide ! Utilise `/radar add` ou `/radar alliance add`.")
            return await interaction.followup.send(msg)

        embeds = []
        
        if mes_alliances:
            embed = discord.Embed(title=t(langue, "rad_list_title_alli", defaut="🕵️‍♂️ Mon Radar - Alliances"), color=self.clr_list_alliances)
            embed.description = "\n\n".join(mes_alliances)
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)
            
        if mes_joueurs:
            lignes_par_page = 10
            for i in range(0, len(mes_joueurs), lignes_par_page):
                chunk = mes_joueurs[i:i+lignes_par_page]
                embed = discord.Embed(title=t(langue, "rad_list_title_player", defaut="🕵️‍♂️ Mon Radar - Joueurs"), color=self.clr_list_joueurs)
                embed.description = "\n\n".join(chunk)
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

    # ==========================================
    # 🛰️ LE SATELLITE ESPION (Tâche de fond ultra-optimisée)
    # ==========================================
    @tasks.loop(minutes=1)
    async def radar_spy_task(self):
        try:
            maintenant = discord.utils.utcnow()

            config_data = await load_configuration_async()
            server_scan_minutes = config_data.get("scan_minutes", {})

            data = await load_surveillance_async()
            players = data.get("players", {})
            alliances_trackees = data.get("alliances", {})
            
            if not players and not alliances_trackees: return

            # 🟢 OPTIMISATION 3: On met en cache users.json pour soulager les I/O du NAS
            path_users = CONFIG_DIR / 'users.json'
            if os.path.exists(path_users):
                mtime = os.path.getmtime(path_users)
                if mtime > self.users_cache_mtime:
                    try:
                        with open(path_users, 'r', encoding='utf-8') as f:
                            users_data = json.load(f)
                            self.users_lang_cache = {uid: info.get("langue", "fr") for uid, info in users_data.items()}
                        self.users_cache_mtime = mtime
                    except: pass
            users_lang = self.users_lang_cache
            
            changes_detected = False
            session = self.bot.session  

            # 🟢 OPTIMISATION 4: Groupement par Serveur (On ne lit l'heure qu'une seule fois !)
            targets_by_server = {}
            for a_id, a_info in alliances_trackees.items():
                srv = a_info.get("serveur", "E4K_FR1").upper()
                if srv not in targets_by_server: targets_by_server[srv] = {"alliances": [], "players": []}
                targets_by_server[srv]["alliances"].append((a_id, a_info))

            for p_id, p_info in players.items():
                srv = p_info.get("serveur", "E4K_FR1").upper()
                if srv not in targets_by_server: targets_by_server[srv] = {"alliances": [], "players": []}
                targets_by_server[srv]["players"].append((p_id, p_info))

            for serveur, targets in targets_by_server.items():
                # ⏱️ Vérification du timing
                minute_cible = server_scan_minutes.get(serveur, 46)
                minutes_valides = [
                    minute_cible,
                    (minute_cible + 5) % 60,
                    (minute_cible + 10) % 60,
                    (minute_cible + 15) % 60
                ]
                    
                if maintenant.minute not in minutes_valides:
                    continue

                headers = await get_api_headers(custom_server=serveur)

                # ==========================================
                # --- ÉTAPE 1 : SURVEILLANCE ALLIANCES ---
                # ==========================================
                for a_id, a_info in targets["alliances"]:
                    old_name = a_info.get("name", "Inconnu")
                    old_members = a_info.get("members", {})
                    abonnes_alliance = a_info.get("abonnes", {})
                    if not abonnes_alliance: continue
                    
                    try:
                        url_alli_live = f"https://api.gge-tracker.com/api/v1/alliances/id/{a_id}"
                        async with session.get(url_alli_live, headers=headers, timeout=10) as r_live:
                            if r_live.status == 200:
                                max_data = await r_live.json()
                                if isinstance(max_data, list) and max_data: max_data = max_data[0]
                                
                                new_name = max_data.get("alliance_name", old_name)
                                new_members_raw = max_data.get("players", max_data.get("members", max_data.get("playerList", [])))
                                
                                new_members_dict = {}
                                for m in new_members_raw:
                                    p_id = str(m.get('player_id', m.get('playerId', '')))
                                    if p_id:
                                        new_members_dict[p_id] = {
                                            "name": m.get('player_name', m.get('playerName', 'Inconnu')),
                                            "rank": int(m.get('alliance_rank', 8))
                                        }

                                if new_name != old_name:
                                    embeds_locales = {}
                                    for lg in ["fr", "de", "en"]:
                                        embed = discord.Embed(title=t(lg, "rad_spy_alli_name_title", defaut="🛡️ ALERTE ALLIANCE : NOUVEAU NOM"), color=discord.Color.purple())
                                        embed.add_field(name=t(lg, "rad_spy_alli_name_mod", defaut="Modification"), value=f"~~{old_name}~~ ➔ **{new_name}**")
                                        embeds_locales[lg] = embed
                                    await self.envoyer_alerte_privee(abonnes_alliance, "infos", embeds_locales, users_lang)
                                    a_info["name"] = new_name
                                    changes_detected = True

                                entrees, sorties, rangs_changes, new_leader = [], [], [], None
                                
                                for pid, m_info in new_members_dict.items():
                                    if pid not in old_members:
                                        entrees.append(m_info)
                                    else:
                                        old_rank = old_members[pid]["rank"]
                                        new_rank = m_info["rank"]
                                        if new_rank != old_rank:
                                            if new_rank == 0:
                                                new_leader = m_info['name']
                                            elif new_rank < old_rank:
                                                rangs_changes.append({"info": m_info, "old": old_rank, "new": new_rank, "type": "promo"})
                                            else:
                                                rangs_changes.append({"info": m_info, "old": old_rank, "new": new_rank, "type": "demo"})

                                for pid, m_info in old_members.items():
                                    if pid not in new_members_dict:
                                        sorties.append(m_info)

                                if new_leader:
                                    embeds_locales = {}
                                    for lg in ["fr", "de", "en"]:
                                        embed = discord.Embed(title=t(lg, "rad_spy_alli_lead_title", defaut="👑 ALERTE ALLIANCE : NOUVEAU CHEF"), description=t(lg, "rad_spy_alli_lead_desc", a=new_name, defaut=f"L'alliance **{new_name}** a changé de couronne !"), color=discord.Color.gold())
                                        embed.add_field(name=t(lg, "rad_spy_alli_lead_f", defaut="Nouveau Chef"), value=t(lg, "rad_spy_alli_lead_v", p=new_leader, defaut=f"**{new_leader}** a pris le contrôle."))
                                        embeds_locales[lg] = embed
                                    await self.envoyer_alerte_privee(abonnes_alliance, "infos", embeds_locales, users_lang)

                                if entrees or sorties:
                                    embeds_locales = {}
                                    for lg in ["fr", "de", "en"]:
                                        entrees_loc, sorties_loc = [], []
                                        for m in entrees: entrees_loc.append(t(lg, "rad_spy_alli_join", p=m['name'], defaut=f"🟢 **{m['name']}** a rejoint l'alliance."))
                                        for m in sorties: sorties_loc.append(t(lg, "rad_spy_alli_leave", p=m['name'], defaut=f"🔴 **{m['name']}** a quitté l'alliance."))
                                        
                                        embed = discord.Embed(title=t(lg, "rad_spy_alli_mouv_title", defaut="🚪 ALERTE ALLIANCE : MOUVEMENTS"), description=t(lg, "rad_spy_alli_mouv_desc", a=new_name, defaut=f"Mouvements récents chez **{new_name}**"), color=discord.Color.blue())
                                        if entrees_loc: embed.add_field(name=t(lg, "rad_spy_alli_mouv_in", defaut="Nouvelles Recrues"), value="\n".join(entrees_loc[:10]), inline=False)
                                        if sorties_loc: embed.add_field(name=t(lg, "rad_spy_alli_mouv_out", defaut="Départs"), value="\n".join(sorties_loc[:10]), inline=False)
                                        embeds_locales[lg] = embed
                                    await self.envoyer_alerte_privee(abonnes_alliance, "mouvements", embeds_locales, users_lang)

                                if rangs_changes:
                                    embeds_locales = {}
                                    for lg in ["fr", "de", "en"]:
                                        rc_loc = []
                                        for r in rangs_changes:
                                            old_l = get_rank_str(r['old'], lg)
                                            new_l = get_rank_str(r['new'], lg)
                                            if r['type'] == "promo":
                                                rc_loc.append(t(lg, "rad_spy_alli_prom", p=r['info']['name'], r1=old_l, r2=new_l, defaut=f"📈 **{r['info']['name']}** a été promu ({old_l} ➔ {new_l})"))
                                            else:
                                                rc_loc.append(t(lg, "rad_spy_alli_demo", p=r['info']['name'], r1=old_l, r2=new_l, defaut=f"📉 **{r['info']['name']}** a été rétrogradé ({old_l} ➔ {new_l})"))
                                        embed = discord.Embed(title=t(lg, "rad_spy_alli_rank_title", defaut="🎖️ ALERTE ALLIANCE : GRADES"), description=t(lg, "rad_spy_alli_rank_desc", a=new_name, defaut=f"Changements de hiérarchie chez **{new_name}**"), color=discord.Color.orange())
                                        embed.add_field(name=t(lg, "rad_spy_alli_rank_f", defaut="Mouvements internes"), value="\n".join(rc_loc[:10]), inline=False)
                                        embeds_locales[lg] = embed
                                    await self.envoyer_alerte_privee(abonnes_alliance, "rangs", embeds_locales, users_lang)

                                if new_name != old_name or entrees or sorties or rangs_changes:
                                    a_info["members"] = new_members_dict
                                    changes_detected = True
                    except Exception as e:
                        logger.error(f"❌ [Radar Spy] Erreur analyse alliance {a_id} : {e}")


                # ==========================================
                # --- ÉTAPE 2 : ANALYSE DES JOUEURS (En vrac / Bulk) ---
                # ==========================================
                # 🟢 OPTIMISATION 1: Rassembler tous les joueurs surveillés du serveur en UNE seule requête !
                tracked_players = targets["players"]
                if tracked_players:
                    player_ids = [p_id for p_id, p_info in tracked_players]
                    
                    try:
                        url_bulk = f"https://api.gge-tracker.com/api/v1/players"
                        async with session.post(url_bulk, headers=headers, json=player_ids, timeout=10) as r:
                            if r.status == 200:
                                bulk_data = (await r.json()).get("players", [])
                                api_players = {str(p["player_id"]): p for p in bulk_data}
                                
                                for p_id, info in tracked_players:
                                    abonnes = info.get("abonnes", {})
                                    if not abonnes: continue
                                    
                                    p_data = api_players.get(p_id)
                                    if not p_data: continue

                                    player = info["name"]
                                    
                                    # 🔹 VÉRIFICATION PSEUDO
                                    new_name = p_data.get("player_name", player)
                                    if new_name != player:
                                        embeds_locales = {}
                                        for lg in ["fr", "de", "en"]:
                                            old_t = t(lg, "prof_unknown", defaut="Inconnu") if player == "Inconnu" else player
                                            new_t = t(lg, "prof_unknown", defaut="Inconnu") if new_name == "Inconnu" else new_name
                                            embed = discord.Embed(title=t(lg, "rad_spy_p_name_title", defaut="🚨 ALERTE PSEUDO"), color=discord.Color.orange())
                                            embed.add_field(name="Cible", value=t(lg, "rad_spy_p_name_f", old=old_t, new=new_t, time=get_discord_time(maintenant.isoformat(), lg), defaut=f"~~{old_t}~~ ➔ **{new_t}**\n🕒 *Fait {get_discord_time(maintenant.isoformat(), lg)}*"))
                                            embeds_locales[lg] = embed
                                        
                                        await self.envoyer_alerte_privee(abonnes, "pseudo", embeds_locales, users_lang)
                                        info["name"], info["last_name"] = new_name, maintenant.isoformat().replace('+00:00', 'Z')
                                        player = new_name # Update for following checks
                                        changes_detected = True

                                    # 🔹 VÉRIFICATION ALLIANCE
                                    old_alli = info.get("last_alliance_name")
                                    new_alli = p_data.get("alliance_name") or "Sans alliance"
                                    
                                    if old_alli is None:
                                        info["last_alliance_name"] = new_alli
                                        changes_detected = True
                                    elif new_alli != old_alli:
                                        embeds_locales = {}
                                        for lg in ["fr", "de", "en"]:
                                            old_t = t(lg, "prof_no_alliance", defaut="Sans alliance") if old_alli == "Sans alliance" else old_alli
                                            new_t = t(lg, "prof_no_alliance", defaut="Sans alliance") if new_alli == "Sans alliance" else new_alli
                                            embed = discord.Embed(title=t(lg, "rad_spy_p_alli_title", defaut="🚨 ALERTE ALLIANCE"), color=discord.Color.brand_red())
                                            embed.add_field(name="Cible", value=t(lg, "rad_spy_p_alli_f", j=player, old=old_t, new=new_t, time=get_discord_time(maintenant.isoformat(), lg), defaut=f"**{player}**\n*{old_t}* ➔ **{new_t}**\n🕒 *Fait {get_discord_time(maintenant.isoformat(), lg)}*"))
                                            embeds_locales[lg] = embed
                                        
                                        await self.envoyer_alerte_privee(abonnes, "alliance", embeds_locales, users_lang)
                                        info["last_alliance_name"], info["last_alliance"] = new_alli, maintenant.isoformat().replace('+00:00', 'Z')
                                        changes_detected = True

                                    # 🔹 VÉRIFICATION PUISSANCE
                                    current_might = int(p_data.get("might_current", 0))
                                    diff = current_might - info.get("last_might", current_might)
                                    if abs(diff) >= 500_000:
                                        emoji, color = ("📈", discord.Color.green()) if diff > 0 else ("📉", discord.Color.brand_red())
                                        sign = '+' if diff > 0 else ''
                                        embeds_locales = {}
                                        for lg in ["fr", "de", "en"]:
                                            embed = discord.Embed(title=t(lg, "rad_spy_p_pp_title", emoji=emoji, defaut=f"🚨 ALERTE PUISSANCE {emoji}"), color=color)
                                            embed.add_field(name="Cible", value=t(lg, "rad_spy_p_pp_f", j=player, old=format_num(info.get('last_might')), new=format_num(current_might), diff=f"{sign}{format_num(diff)}", defaut=f"**{player}**\nAncienne: {format_num(info.get('last_might'))}\nNouvelle: **{format_num(current_might)}**\nDiff: **{sign}{format_num(diff)} PP**"))
                                            embeds_locales[lg] = embed
                                        
                                        await self.envoyer_alerte_privee(abonnes, "puissance", embeds_locales, users_lang)
                                        info["last_might"] = current_might 
                                        changes_detected = True

                                    # 🔹 VÉRIFICATION COLOMBE
                                    new_peace = p_data.get("peace_disabled_at")
                                    if new_peace == "null": new_peace = None
                                    old_peace, was_protected = info.get("peace_disabled_at"), info.get("is_protected", False)
                                    is_protected, new_dt = False, None
                                    
                                    if new_peace:
                                        try:
                                            new_dt = datetime.fromisoformat(new_peace.replace('Z', '+00:00'))
                                            if new_dt > discord.utils.utcnow(): is_protected = True
                                        except: pass

                                    msgs_trigger = []
                                    if new_peace != old_peace and is_protected:
                                        if not was_protected: msgs_trigger.append("on")
                                        else:
                                            send_update = True
                                            if old_peace:
                                                try:
                                                    if abs((new_dt - datetime.fromisoformat(old_peace.replace('Z', '+00:00'))).total_seconds()) < 60: send_update = False
                                                except: pass
                                            if send_update: msgs_trigger.append("mod")
                                    
                                    if was_protected and not is_protected:
                                        if not new_peace: msgs_trigger.append("off")
                                        else: msgs_trigger.append("end")

                                    if msgs_trigger:
                                        ts = int(new_dt.timestamp()) if new_dt else 0
                                        for trigger in msgs_trigger:
                                            embeds_locales = {}
                                            for lg in ["fr", "de", "en"]:
                                                if trigger == "on":
                                                    embed = discord.Embed(title=t(lg, "rad_spy_p_dove_on_title", defaut="🕊️ ALERTE COLOMBE : ACTIVÉE"), description=t(lg, "rad_spy_p_dove_on_desc", j=player, ts=ts, defaut=f"**{player}** est sous protection !\n🕒 Fin : <t:{ts}:f> (<t:{ts}:R>)"), color=discord.Color.light_grey())
                                                elif trigger == "mod":
                                                    embed = discord.Embed(title=t(lg, "rad_spy_p_dove_mod_title", defaut="🔄 ALERTE COLOMBE : MODIFIÉE"), description=t(lg, "rad_spy_p_dove_mod_desc", j=player, ts=ts, defaut=f"**{player}** a modifié sa protection !\n🕒 Fin : <t:{ts}:f> (<t:{ts}:R>)"), color=discord.Color.blue())
                                                elif trigger == "off":
                                                    embed = discord.Embed(title=t(lg, "rad_spy_p_dove_off_title", defaut="⚔️ CONFIRMATION : SANS COLOMBE"), description=t(lg, "rad_spy_p_dove_off_desc", j=player, defaut=f"La protection de **{player}** a expiré ou a été annulée. Il est vulnérable !"), color=discord.Color.brand_green())
                                                elif trigger == "end":
                                                    embed = discord.Embed(title=t(lg, "rad_spy_p_dove_end_title", defaut="⚔️ ALERTE COLOMBE : TERMINÉE"), description=t(lg, "rad_spy_p_dove_off_desc", j=player, defaut=f"La protection de **{player}** a expiré ou a été annulée. Il est vulnérable !"), color=discord.Color.brand_green())
                                                embeds_locales[lg] = embed
                                                
                                            await self.envoyer_alerte_privee(abonnes, "colombe", embeds_locales, users_lang)

                                    if (new_peace != old_peace) or (was_protected != is_protected):
                                        info["peace_disabled_at"], info["is_protected"] = new_peace, is_protected
                                        changes_detected = True
                    except Exception as e:
                        logger.error(f"❌ [Radar Spy] Erreur Bulk Players : {e}")

                    # ==========================================
                    # --- ÉTAPE 3 : MOUVEMENTS GLOBAUX ---
                    # ==========================================
                    # 🟢 OPTIMISATION 2 : Une seule requête globale pour vérifier TOUS les déménagements
                    try:
                        url_movements = f"https://api.gge-tracker.com/api/v1/server/movements?page=1&castleType=1&movementType=3"
                        async with session.get(url_movements, headers=headers, timeout=5) as r:
                            if r.status == 200:
                                movements = (await r.json()).get("movements", [])
                                
                                # On vérifie localement si un mouvement concerne l'un de nos joueurs
                                for m in movements:
                                    m_name = m.get("player_name")
                                    
                                    for p_id, info in tracked_players:
                                        if info["name"] == m_name and m["created_at"] > info["last_pos"]:
                                            abonnes = info.get("abonnes", {})
                                            if not abonnes: continue

                                            x_old, y_old, x_new, y_new = m.get('position_x_old'), m.get('position_y_old'), m.get('position_x_new'), m.get('position_y_new')
                                            
                                            embeds_locales = {}
                                            for lg in ["fr", "de", "en"]:
                                                embed = discord.Embed(title=t(lg, "rad_spy_p_pos_title", defaut="🚨 ALERTE DÉMÉNAGEMENT"), color=discord.Color.dark_purple())
                                                embed.add_field(name="Cible", value=t(lg, "rad_spy_p_pos_f", j=m_name, xo=x_old, yo=y_old, xn=x_new, yn=y_new, time=get_discord_time(m['created_at'], lg), defaut=f"**{m_name}**\n`{x_old}:{y_old}` ➔ `{x_new}:{y_new}`\n🕒 *Fait {get_discord_time(m['created_at'], lg)}*"))
                                                embeds_locales[lg] = embed
                                                
                                            await self.envoyer_alerte_privee(abonnes, "position", embeds_locales, users_lang)
                                            info["last_pos"] = m["created_at"]
                                            changes_detected = True
                    except Exception as e:
                        logger.error(f"❌ [Radar Spy] Erreur Global Movements : {e}")

                await asyncio.sleep(1) # Sécurité Rate Limiting

            if changes_detected:
                await save_surveillance_async(data)

        except Exception as e:
            logger.error(f"❌ [RADAR CRASH] : {traceback.format_exc()}")

async def setup(bot: commands.Bot):
    await bot.add_cog(RadarCog(bot))