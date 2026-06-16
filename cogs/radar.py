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

# 🛠️ On importe nos variables partagées (Versions ASYNC)
from utils import (
    BASE_DATA_PATH, 
    joueur_autocomplete, 
    alliance_autocomplete, 
    format_num, 
    PaginationView,
    setup_embed_footer,
    load_surveillance_async,
    save_surveillance_async,
    get_discord_timestamp
)

logger = logging.getLogger("GGE_Bot")

SURVEILLANCE_FILE = BASE_DATA_PATH / 'surveillance.json'
RANKS_MAP = {0: "Chef", 1: "Représentant", 2: "Maréchal", 3: "Trésorier", 4: "Diplomate", 5: "Recruteur", 6: "Général", 7: "Sergent", 8: "Membre", 9: "Novice"}

def get_discord_time(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        return f"<t:{int(dt.timestamp())}:R>" 
    except:
        return "Récemment"

# ==========================================
# 🎛️ MENU INTERACTIF DES FILTRES JOUEURS
# ==========================================
class RadarSettingsView(discord.ui.View):
    def __init__(self, p_id: str, user_id: str, joueur_name: str, initial_prefs: dict):
        super().__init__(timeout=None)
        self.p_id = p_id
        self.user_id = str(user_id)
        self.joueur_name = joueur_name
        self.prefs = initial_prefs  # ⚡ Optimisation RAM/Vitesse : Injection directe de l'état
        self.update_buttons_state()

    async def toggle_pref(self, pref_key: str):
        # 🔐 Sécurisé : Lecture/Écriture isolée via verrous asynchrones
        data = await load_surveillance_async()
        if self.p_id in data.get("players", {}) and self.user_id in data["players"][self.p_id]["abonnes"]:
            current = data["players"][self.p_id]["abonnes"][self.user_id].get(pref_key, False)
            new_val = not current
            data["players"][self.p_id]["abonnes"][self.user_id][pref_key] = new_val
            self.prefs[pref_key] = new_val  # Synchronisation locale immédiate
            await save_surveillance_async(data)

    def update_buttons_state(self):
        self.btn_pseudo.style = discord.ButtonStyle.success if self.prefs.get("pseudo") else discord.ButtonStyle.danger
        self.btn_position.style = discord.ButtonStyle.success if self.prefs.get("position") else discord.ButtonStyle.danger
        self.btn_alliance.style = discord.ButtonStyle.success if self.prefs.get("alliance") else discord.ButtonStyle.danger
        self.btn_puissance.style = discord.ButtonStyle.success if self.prefs.get("puissance") else discord.ButtonStyle.danger
        self.btn_colombe.style = discord.ButtonStyle.success if self.prefs.get("colombe") else discord.ButtonStyle.danger

    @discord.ui.button(label="Pseudo", custom_id="pref_pseudo", row=0)
    async def btn_pseudo(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("pseudo")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Position", custom_id="pref_position", row=0)
    async def btn_position(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("position")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Alliance", custom_id="pref_alliance", row=0)
    async def btn_alliance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("alliance")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Puissance", custom_id="pref_puissance", row=1)
    async def btn_puissance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("puissance")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Colombe", custom_id="pref_colombe", row=1)
    async def btn_colombe(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("colombe")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="✅ Terminer et Fermer", style=discord.ButtonStyle.secondary, row=2)
    async def btn_fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ **Préférences enregistrées avec succès !** Le radar joueur est actif.", view=None)

# ==========================================
# 🎛️ MENU INTERACTIF DES FILTRES ALLIANCES
# ==========================================
class RadarAllianceSettingsView(discord.ui.View):
    def __init__(self, a_id: str, user_id: str, alliance_name: str, initial_prefs: dict):
        super().__init__(timeout=None)
        self.a_id = a_id
        self.user_id = str(user_id)
        self.alliance_name = alliance_name
        self.prefs = initial_prefs
        self.update_buttons_state()

    async def toggle_pref(self, pref_key: str):
        # 🔐 Sécurisé : Gestionnaire de verrous asynchrones activé
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

    @discord.ui.button(label="Entrées/Sorties", custom_id="pref_alli_mouv", row=0)
    async def btn_mouvements(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("mouvements")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Promotions/Rétrogradations", custom_id="pref_alli_rangs", row=0)
    async def btn_rangs(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("rangs")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Changement Nom/Chef", custom_id="pref_alli_infos", row=0)
    async def btn_infos(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("infos")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="✅ Terminer et Fermer", style=discord.ButtonStyle.secondary, row=1)
    async def btn_fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ **Préférences d'alliance enregistrées !** Le radar global est actif.", view=None)


# ==========================================
# 📊 MODULE COG RADAR
# ==========================================
class RadarCog(commands.GroupCog, group_name="radar", group_description="Radar de Guerre personnel"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_scan_hour = None  # ⏱️ Traceur chronologique anti-aveuglement au démarrage
        super().__init__()
        
        # 🎨 PALETTE DES COGS RADAR
        self.clr_add_joueur     = discord.Color.from_rgb(25, 42, 86)     
        self.clr_add_alliance   = discord.Color.from_rgb(39, 60, 117)    
        self.clr_list_alliances = discord.Color.from_rgb(12, 36, 97)     
        self.clr_list_joueurs   = discord.Color.from_rgb(74, 105, 124)   

    async def cog_load(self):
        if not self.radar_spy_task.is_running():
            self.radar_spy_task.start()

    async def cog_unload(self):
        self.radar_spy_task.cancel()

    async def envoyer_alerte_privee(self, abonnes: dict, type_filtre: str, embed: discord.Embed):
        """Envoie l'alerte MP uniquement aux joueurs qui ont coché ce filtre"""
        for user_id, prefs in abonnes.items():
            if prefs.get(type_filtre, False):
                try:
                    setup_embed_footer(embed, None) 
                    user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                    await user.send(embed=embed)
                except discord.Forbidden:
                    logger.warning(f"Impossible d'envoyer un MP à {user_id} (DMs bloqués).")
                except Exception as e:
                    logger.error(f"Erreur MP à {user_id} : {e}")

    # ==========================================
    # 🕵️‍♂️ COMMANDES : RADAR JOUEUR
    # ==========================================
    @app_commands.command(name="add", description="Ajoute un joueur à ton radar personnel (Limite : 25 suivis)")
    @app_commands.autocomplete(joueur=joueur_autocomplete)
    @app_commands.describe(raison="Raison de la surveillance")
    async def s_add(self, interaction: discord.Interaction, joueur: str, raison: str = "Surveillance générale"):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return

        logger.info(f"🎯 [Radar Add] {interaction.user.name} a ajouté le joueur {joueur}")

        p_id, p_might = None, 0
        try:
            player_files = list((BASE_DATA_PATH / 'server_scans').rglob('server_*.json'))
            if player_files:
                latest = max(player_files, key=lambda p: p.stat().st_mtime)
                with open(latest, 'r', encoding='utf-8') as f:
                    local_data = json.load(f).get('players', {})
                    for p_name, p_info in local_data.items():
                        if p_name.lower() == joueur.lower():
                            raw_id = str(p_info.get('player_id', p_info.get('id', '')))
                            p_id = raw_id + '164' if raw_id and not raw_id.endswith('164') else raw_id
                            p_might = int(p_info.get('main_points', 0)) 
                            joueur = p_name 
                            break
        except: pass

        if not p_id:
            return await interaction.followup.send(f"⚠️ Joueur **{joueur}** introuvable dans le cache local.")

        data = await load_surveillance_async()
        user_id = str(interaction.user.id)
        now_str = discord.utils.utcnow().isoformat().replace('+00:00', 'Z')

        already_tracking = user_id in data.get("players", {}).get(p_id, {}).get("abonnes", {})
        if not already_tracking:
            count_tracked = sum(1 for p_info in data.get("players", {}).values() if user_id in p_info.get("abonnes", {}))
            if count_tracked >= 25:
                return await interaction.followup.send(f"❌ **Limite atteinte** : Suivi maximal `{count_tracked}/25` joueurs. Retire un profil avec `/radar remove` d'abord.")

        if p_id not in data["players"]:
            data["players"][p_id] = {
                "name": joueur, "last_alliance": now_str, "last_name": now_str, "last_pos": now_str, "last_might": p_might,
                "peace_disabled_at": None, "is_protected": False, "abonnes": {}
            }
        
        if "abonnes" not in data["players"][p_id]: data["players"][p_id]["abonnes"] = {}

        if user_id not in data["players"][p_id]["abonnes"]:
            data["players"][p_id]["abonnes"][user_id] = {
                "raison": raison, "pseudo": False, "position": False, "alliance": False, "puissance": False, "colombe": False
            }
        else:
            return await interaction.followup.send(f"Tu surveilles DÉJÀ **{joueur}** ! 😉")

        await save_surveillance_async(data)
        
        embed = discord.Embed(
            title=f"🎯 Cible verrouillée : {joueur}", 
            description=f"**{joueur}** a bien été ajouté à ton radar.\n*(Raison: {raison})*\n\n👇 **Configure tes alertes (Rouge = OFF, Vert = ON) :**", 
            color=self.clr_add_joueur
        )
        view = RadarSettingsView(p_id, user_id, joueur, data["players"][p_id]["abonnes"][user_id])
        setup_embed_footer(embed, interaction)
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="remove", description="Retire un joueur de TON radar personnel")
    @app_commands.autocomplete(joueur=joueur_autocomplete)
    async def s_remove(self, interaction: discord.Interaction, joueur: str):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return

        data = await load_surveillance_async()
        user_id = str(interaction.user.id)
        cible_trouvee = False
        
        for pid, info in list(data["players"].items()):
            if info["name"].lower() == joueur.lower():
                if user_id in info.get("abonnes", {}):
                    cible_trouvee = True
                    del data["players"][pid]["abonnes"][user_id]
                    if not data["players"][pid]["abonnes"]: del data["players"][pid]
                    break

        if cible_trouvee:
            await save_surveillance_async(data)
            await interaction.followup.send(f"✅ **{joueur}** a bien été retiré de ton radar personnel.")
        else:
            await interaction.followup.send(f"⚠️ **{joueur}** n'est pas dans ton radar.")

    # ==========================================
    # 🛡️ COMMANDES : RADAR ALLIANCE (SOUS-GROUPE)
    # ==========================================
    alliance_group = app_commands.Group(name="alliance", description="Gérer le radar des alliances complètes")

    @alliance_group.command(name="add", description="Ajoute une alliance entière à ton radar (Limite : 3 suivis)")
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.describe(raison="Pourquoi surveilles-tu cette alliance ?")
    async def a_add(self, interaction: discord.Interaction, alliance: str, raison: str = "Surveillance globale"):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return

        logger.info(f"🛡️ [Radar Alli Add] {interaction.user.name} a ajouté l'alliance {alliance}")

        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        a_id, a_name_real, members_dict = None, alliance, {}

        # ⚡ Optimisation : Plus de ClientSession éphémère locale, utilisation de self.bot.session
        try:
            url_search = f"https://api.gge-tracker.com/api/v1/alliances/name/{urllib.parse.quote(alliance)}"
            async with self.bot.session.get(url_search, headers=headers, timeout=10) as r:
                if r.status == 200:
                    search_data = await r.json()
                    cible = search_data[0] if isinstance(search_data, list) and search_data else search_data
                    a_id = str(cible.get("alliance_id") or cible.get("id"))
                    a_name_real = cible.get("alliance_name", alliance)
            
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
            logger.error(f"[Radar Alli Add] Erreur API : {e}")
            return await interaction.followup.send("❌ Impossible de joindre GGE-Tracker pour trouver cette alliance.")

        if not a_id:
            return await interaction.followup.send(f"⚠️ Alliance **{alliance}** introuvable.")

        data = await load_surveillance_async()
        user_id = str(interaction.user.id)

        already_tracking = user_id in data.get("alliances", {}).get(a_id, {}).get("abonnes", {})
        if not already_tracking:
            count_tracked = sum(1 for a_info in data.get("alliances", {}).values() if user_id in a_info.get("abonnes", {}))
            if count_tracked >= 3:
                return await interaction.followup.send(f"❌ **Limite atteinte** : Surveillance maximale `{count_tracked}/3` alliances.")

        if a_id not in data["alliances"]:
            data["alliances"][a_id] = {
                "name": a_name_real, "members": members_dict, "abonnes": {}
            }
        
        if "abonnes" not in data["alliances"][a_id]: data["alliances"][a_id]["abonnes"] = {}

        if user_id not in data["alliances"][a_id]["abonnes"]:
            data["alliances"][a_id]["abonnes"][user_id] = {
                "raison": raison, "mouvements": False, "rangs": False, "infos": False
            }
        else:
            return await interaction.followup.send(f"Tu surveilles DÉJÀ l'alliance **{a_name_real}** ! 😉")

        await save_surveillance_async(data)
        
        embed = discord.Embed(
            title=f"🛡️ Alliance verrouillée : {a_name_real}", 
            description=f"Le radar global est activé sur **{a_name_real}** ({len(members_dict)} membres).\n*(Raison: {raison})*\n\n👇 **Configure tes alertes d'alliance (Rouge = OFF, Vert = ON) :**", 
            color=self.clr_add_alliance
        )
        view = RadarAllianceSettingsView(a_id, user_id, a_name_real, data["alliances"][a_id]["abonnes"][user_id])
        setup_embed_footer(embed, interaction)
        await interaction.followup.send(embed=embed, view=view)

    @alliance_group.command(name="remove", description="Retire une alliance de TON radar personnel")
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    async def a_remove(self, interaction: discord.Interaction, alliance: str):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return

        data = await load_surveillance_async()
        user_id = str(interaction.user.id)
        cible_trouvee = False
        
        for aid, info in list(data.get("alliances", {}).items()):
            if info["name"].lower() == alliance.lower():
                if user_id in info.get("abonnes", {}):
                    cible_trouvee = True
                    del data["alliances"][aid]["abonnes"][user_id]
                    if not data["alliances"][aid]["abonnes"]: del data["alliances"][aid]
                    break

        if cible_trouvee:
            await save_surveillance_async(data)
            await interaction.followup.send(f"✅ L'alliance **{alliance}** a bien été retirée de ton radar.")
        else:
            await interaction.followup.send(f"⚠️ L'alliance **{alliance}** n'est pas dans ton radar.")

    # ==========================================
    # 📋 COMMANDE : LISTE GLOBALE
    # ==========================================
    @app_commands.command(name="list", description="Affiche ton radar personnel (Joueurs & Alliances)")
    async def s_list(self, interaction: discord.Interaction):
        try: await interaction.response.defer(ephemeral=True, thinking=True)
        except: return
        
        data = await load_surveillance_async()
        user_id = str(interaction.user.id)
        
        mes_joueurs = []
        for pid, info in data.get("players", {}).items():
            abonnes = info.get("abonnes", {})
            if user_id in abonnes:
                prefs = abonnes[user_id]
                nom = info["name"]
                raison = prefs.get("raison", "Sans raison")
                
                filtres_actifs = []
                if prefs.get("pseudo"): filtres_actifs.append("Pseudo")
                if prefs.get("position"): filtres_actifs.append("Position")
                if prefs.get("alliance"): filtres_actifs.append("Alliance")
                if prefs.get("puissance"): filtres_actifs.append("PP")
                if prefs.get("colombe"): filtres_actifs.append("Colombe")
                
                str_filtres = f"✅ {', '.join(filtres_actifs)}" if filtres_actifs else "❌ Aucun filtre actif"
                mes_joueurs.append(f"🎯 **{nom}** ➔ *{raison}*\n└ {str_filtres}")

        mes_alliances = []
        for aid, info in data.get("alliances", {}).items():
            abonnes = info.get("abonnes", {})
            if user_id in abonnes:
                prefs = abonnes[user_id]
                nom = info["name"]
                raison = prefs.get("raison", "Sans raison")
                
                filtres_actifs = []
                if prefs.get("mouvements"): filtres_actifs.append("Mouvements")
                if prefs.get("rangs"): filtres_actifs.append("Rangs")
                if prefs.get("infos"): filtres_actifs.append("Infos")
                
                str_filtres = f"✅ {', '.join(filtres_actifs)}" if filtres_actifs else "❌ Aucun filtre actif"
                mes_alliances.append(f"🛡️ **{nom}** ➔ *{raison}*\n└ {str_filtres}")

        if not mes_joueurs and not mes_alliances:
            return await interaction.followup.send("🕸️ Ton radar est vide ! Utilise `/radar add` ou `/radar alliance add`.")

        embeds = []
        
        if mes_alliances:
            embed = discord.Embed(title="🕵️‍♂️ Mon Radar - Alliances", color=self.clr_list_alliances)
            embed.description = "\n\n".join(mes_alliances)
            setup_embed_footer(embed, interaction)
            embeds.append(embed)
            
        if mes_joueurs:
            lignes_par_page = 10
            for i in range(0, len(mes_joueurs), lignes_par_page):
                chunk = mes_joueurs[i:i+lignes_par_page]
                embed = discord.Embed(title="🕵️‍♂️ Mon Radar - Joueurs", color=self.clr_list_joueurs)
                embed.description = "\n\n".join(chunk)
                setup_embed_footer(embed, interaction)
                embeds.append(embed)

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

    # ==========================================
    # 🛰️ LE SATELLITE ESPION
    # ==========================================
    @tasks.loop(minutes=1)
    async def radar_spy_task(self):
        try:
            maintenant = discord.utils.utcnow()
            current_hour_key = maintenant.strftime("%Y-%m-%d-%H")

            # 🛠️ Correction : Éviter le trou noir des 59 min d'aveuglement sur redémarrage
            if maintenant.minute != 46 and self.last_scan_hour == current_hour_key:
                return

            logger.info(f"🛰️ [SATELLITE] Lancement du scan global (Minute de synchronisation: {maintenant.minute}/46)...")
            self.last_scan_hour = current_hour_key

            data = await load_surveillance_async()
            players = data.get("players", {})
            alliances_trackees = data.get("alliances", {})
            
            if not players and not alliances_trackees: return
            
            headers = {'User-Agent': 'Mozilla/5.0 GGE-Assistant', 'accept': 'application/json', 'gge-server': 'E4K_FR1'}
            changes_detected = False
            session = self.bot.session  # Pool de connexion global unique

            # ==========================================
            # --- ÉTAPE 1 : SURVEILLANCE ALLIANCES ---
            # ==========================================
            if alliances_trackees:
                for a_id, a_info in list(alliances_trackees.items()):
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
                                    embed = discord.Embed(title="🛡️ ALERTE ALLIANCE : NOUVEAU NOM", color=discord.Color.purple())
                                    embed.add_field(name="Modification", value=f"~~{old_name}~~ ➔ **{new_name}**")
                                    await self.envoyer_alerte_privee(abonnes_alliance, "infos", embed)
                                    a_info["name"] = new_name
                                    changes_detected = True

                                entrees, sorties, rangs_changes, new_leader = [], [], [], None
                                
                                for pid, m_info in new_members_dict.items():
                                    if pid not in old_members:
                                        entrees.append(f"🟢 **{m_info['name']}** a rejoint l'alliance.")
                                    else:
                                        old_rank = old_members[pid]["rank"]
                                        new_rank = m_info["rank"]
                                        if new_rank != old_rank:
                                            if new_rank == 0:
                                                new_leader = m_info['name']
                                            elif new_rank < old_rank:
                                                rangs_changes.append(f"📈 **{m_info['name']}** a été promu ({RANKS_MAP.get(old_rank, 'Membre')} ➔ {RANKS_MAP.get(new_rank, 'Général')})")
                                            else:
                                                rangs_changes.append(f"📉 **{m_info['name']}** a été rétrogradé ({RANKS_MAP.get(old_rank, 'Général')} ➔ {RANKS_MAP.get(new_rank, 'Membre')})")

                                for pid, m_info in old_members.items():
                                    if pid not in new_members_dict:
                                        sorties.append(f"🔴 **{m_info['name']}** a quitté l'alliance.")

                                if new_leader:
                                    embed = discord.Embed(title=f"👑 ALERTE ALLIANCE : NOUVEAU CHEF", description=f"L'alliance **{new_name}** a changé de couronne !", color=discord.Color.gold())
                                    embed.add_field(name="Nouveau Chef", value=f"**{new_leader}** a pris le contrôle.")
                                    await self.envoyer_alerte_privee(abonnes_alliance, "infos", embed)

                                if entrees or sorties:
                                    embed = discord.Embed(title=f"🚪 ALERTE ALLIANCE : MOUVEMENTS", description=f"Mouvements récents chez **{new_name}**", color=discord.Color.blue())
                                    if entrees: embed.add_field(name="Nouvelles Recrues", value="\n".join(entrees[:10]), inline=False)
                                    if sorties: embed.add_field(name="Départs", value="\n".join(sorties[:10]), inline=False)
                                    await self.envoyer_alerte_privee(abonnes_alliance, "mouvements", embed)

                                if rangs_changes:
                                    embed = discord.Embed(title=f"🎖️ ALERTE ALLIANCE : GRADES", description=f"Changements de hiérarchie chez **{new_name}**", color=discord.Color.orange())
                                    embed.add_field(name="Mouvements internes", value="\n".join(rangs_changes[:10]), inline=False)
                                    await self.envoyer_alerte_privee(abonnes_alliance, "rangs", embed)

                                if new_name != old_name or entrees or sorties or rangs_changes:
                                    a_info["members"] = new_members_dict
                                    changes_detected = True

                    except Exception as e:
                        logger.error(f"[Radar Spy] Erreur analyse alliance {a_id} : {e}")

            # ==========================================
            # --- ÉTAPE 2 : ANALYSE DES JOUEURS ---
            # ==========================================
            for p_id, info in list(players.items()):
                joueur = info["name"]
                abonnes = info.get("abonnes", {})
                if not abonnes: continue 
                
                try:
                    url_alli = f"https://api.gge-tracker.com/api/v1/updates/players/{p_id}/alliances"
                    async with session.get(url_alli, headers=headers, timeout=5) as r:
                        if r.status == 200:
                            for u in (await r.json()).get("updates", []):
                                if u["date"] > info["last_alliance"]:
                                    old, new = u.get("old_alliance_name") or "Sans alliance", u.get("new_alliance_name") or "Sans alliance"
                                    embed = discord.Embed(title="🚨 ALERTE ALLIANCE", color=discord.Color.brand_red())
                                    embed.add_field(name="Cible", value=f"**{joueur}**\n*{old}* ➔ **{new}**\n🕒 *Fait {get_discord_time(u['date'])}*")
                                    await self.envoyer_alerte_privee(abonnes, "alliance", embed)
                                    info["last_alliance"] = u["date"]
                                    changes_detected = True
                except: pass

                try:
                    url_name = f"https://api.gge-tracker.com/api/v1/updates/players/{p_id}/names"
                    async with session.get(url_name, headers=headers, timeout=5) as r:
                        if r.status == 200:
                            for u in (await r.json()).get("updates", []):
                                if u["date"] > info["last_name"]:
                                    old, new = u.get("old_player_name") or "Inconnu", u.get("new_player_name") or "Inconnu"
                                    embed = discord.Embed(title="🚨 ALERTE PSEUDO", color=discord.Color.orange())
                                    embed.add_field(name="Cible", value=f"~~{old}~~ ➔ **{new}**\n🕒 *Fait {get_discord_time(u['date'])}*")
                                    await self.envoyer_alerte_privee(abonnes, "pseudo", embed)
                                    info["last_name"], info["name"], joueur = u["date"], new, new
                                    changes_detected = True
                except: pass

                try:
                    url_pos = f"https://api.gge-tracker.com/api/v1/server/movements?page=1&castleType=1&movementType=3&search={urllib.parse.quote(joueur)}&searchType=player"
                    async with session.get(url_pos, headers=headers, timeout=5) as r:
                        if r.status == 200:
                            for m in (await r.json()).get("movements", []):
                                if m["created_at"] > info["last_pos"]:
                                    x_old, y_old, x_new, y_new = m.get('position_x_old'), m.get('position_y_old'), m.get('position_x_new'), m.get('position_y_new')
                                    embed = discord.Embed(title="🚨 ALERTE DÉMÉNAGEMENT", color=discord.Color.dark_purple())
                                    embed.add_field(name="Cible", value=f"**{joueur}**\n`{x_old}:{y_old}` ➔ `{x_new}:{y_new}`\n🕒 *Fait {get_discord_time(m['created_at'])}*")
                                    await self.envoyer_alerte_privee(abonnes, "position", embed)
                                    info["last_pos"] = m["created_at"]
                                    changes_detected = True
                except: pass

                try:
                    url_player = f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(joueur)}"
                    async with session.get(url_player, headers=headers, timeout=5) as r:
                        if r.status == 200:
                            p_data = await r.json()
                            if isinstance(p_data, list) and p_data: p_data = p_data[0]
                                
                            if p_data:
                                current_might = int(p_data.get("might_current", 0))
                                diff = current_might - info.get("last_might", current_might)
                                if abs(diff) >= 500_000:
                                    emoji, color = ("📈", discord.Color.green()) if diff > 0 else ("📉", discord.Color.brand_red())
                                    embed = discord.Embed(title=f"🚨 ALERTE PUISSANCE {emoji}", color=color)
                                    embed.add_field(name="Cible", value=f"**{joueur}**\nAncienne: {format_num(info.get('last_might'))}\nNouvelle: **{format_num(current_might)}**\nDiff: **{'+' if diff > 0 else ''}{format_num(diff)} PP**")
                                    await self.envoyer_alerte_privee(abonnes, "puissance", embed)
                                    info["last_might"] = current_might 
                                    changes_detected = True

                                new_peace = p_data.get("peace_disabled_at")
                                if new_peace == "null": new_peace = None
                                old_peace, was_protected = info.get("peace_disabled_at"), info.get("is_protected", False)
                                is_protected, new_dt = False, None
                                
                                if new_peace:
                                    try:
                                        new_dt = datetime.fromisoformat(new_peace.replace('Z', '+00:00'))
                                        if new_dt > discord.utils.utcnow(): is_protected = True
                                    except: pass

                                messages = []
                                if new_peace != old_peace and is_protected:
                                    ts = int(new_dt.timestamp())
                                    if not was_protected:
                                        messages.append(discord.Embed(title="🕊️ ALERTE COLOMBE : ACTIVÉE", description=f"**{joueur}** est sous protection !\n🕒 Fin : <t:{ts}:f> (<t:{ts}:R>)", color=discord.Color.light_grey()))
                                    else:
                                        send_update = True
                                        if old_peace:
                                            try:
                                                if abs((new_dt - datetime.fromisoformat(old_peace.replace('Z', '+00:00'))).total_seconds()) < 60: send_update = False
                                            except: pass
                                        if send_update: messages.append(discord.Embed(title="🔄 ALERTE COLOMBE : MODIFIÉE", description=f"**{joueur}** a modifié sa protection !\n🕒 Fin : <t:{ts}:f> (<t:{ts}:R>)", color=discord.Color.blue()))
                                
                                if was_protected and not is_protected:
                                    titre = "⚔️ CONFIRMATION : SANS COLOMBE" if not new_peace else "⚔️ ALERTE COLOMBE : TERMINÉE"
                                    messages.append(discord.Embed(title=titre, description=f"La protection de **{joueur}** a expiré ou a été annulée. Il est vulnérable !", color=discord.Color.brand_green()))

                                if (new_peace != old_peace) or (was_protected != is_protected):
                                    info["peace_disabled_at"], info["is_protected"] = new_peace, is_protected
                                    changes_detected = True
                                    for emb in messages: await self.envoyer_alerte_privee(abonnes, "colombe", emb)

                except Exception: pass
                await asyncio.sleep(1)  # ⚡ Non bloquant : Remplacement de time.sleep par un sleep asynchrone pour laisser respirer l'IO

            if changes_detected:
                # 🔐 Sécurisé : Écriture asynchrone protégée par verrou
                await save_surveillance_async(data)

        except Exception as e:
            logger.error(f"🚨 [RADAR CRASH] : {traceback.format_exc()}")

async def setup(bot: commands.Bot):
    await bot.add_cog(RadarCog(bot))