import json
import logging
import os
import traceback
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from emojis import DICT_EMOJIS
from utils import (
    CONFIG_DIR,
    PaginationView,
    get_api_headers,
    get_cached_data,
    get_server_config,
    joueur_autocomplete,
    load_dungeons_async,
    prompt_vote_if_lucky,
    save_dungeons_async,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")


# ==========================================
# LE COMPOSANT UI : VÉRIFIER & RELANCER
# ==========================================
class FortressActionView(discord.ui.View):
    def __init__(self, cog, user_id: str, cibles: list, joueur: str, serveur: str, langue: str = "fr"):
        super().__init__(timeout=3600)
        self.cog = cog
        self.user_id = user_id
        self.message = None
        self.cibles = cibles
        self.joueur = joueur
        self.serveur = serveur
        self.langue = langue

        self.btn_verify = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label=t(langue, "fort_btn_verify", defaut="Vérifier ces cibles"),
            emoji=DICT_EMOJIS.get("e_icon_search", "🔍"),
            custom_id="btn_fort_verify",
        )
        self.btn_verify.callback = self.callback_verify

        self.btn_relaunch = discord.ui.Button(
            style=discord.ButtonStyle.primary,
            label=t(langue, "fort_btn_relaunch", defaut="Relancer (10 suivantes)"),
            emoji=DICT_EMOJIS.get("e_refresh", "🔄"),
            custom_id="fort_btn_relaunch",
        )
        self.btn_relaunch.callback = self.callback_relaunch

        self.add_item(self.btn_verify)
        self.add_item(self.btn_relaunch)

    async def callback_verify(self, interaction: discord.Interaction):
        logger.info(f"🔎 [FORTERESSES] {interaction.user.name} a cliqué sur 'Vérifier' pour {self.joueur}.")
        await interaction.response.defer(thinking=True, ephemeral=False)
        await self.cog.verify_and_send(interaction, self.cibles, self.joueur, self.serveur, self.langue)

    async def callback_relaunch(self, interaction: discord.Interaction):
        logger.info(f"🔄 [FORTERESSES] {interaction.user.name} a cliqué sur 'Relancer' pour {self.joueur}.")
        await interaction.response.defer(thinking=True, ephemeral=False)
        await self.cog.relaunch_scan(interaction, self.user_id, self.langue)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if hasattr(self, "message") and self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ==========================================
# 🏰 LE COG FORTERESSES
# ==========================================
@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
class ForteressesCog(commands.GroupCog, group_name="fortress", group_description="Fortress Radar (Sands, Ice, Peaks)"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

        # 🎨 PALETTE VERT RADAR / EMERAUDE
        self.clr_activation = discord.Color.from_rgb(180, 238, 180)
        self.clr_attente = discord.Color.from_rgb(255, 195, 160)
        self.base_api = "https://api.gge-tracker.com/api/v1"

    async def cog_load(self):
        if not self.dungeon_spy_task.is_running():
            self.dungeon_spy_task.start()

    async def cog_unload(self):
        self.dungeon_spy_task.cancel()

    async def fetch_meta_scan(self, session: aiohttp.ClientSession, headers: dict) -> str:
        """Récupère l'heure du dernier scan global des forteresses."""
        url = f"{self.base_api}/dungeons/meta"
        try:
            async with session.get(url, headers=headers, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    last_scan = data.get("last_scan_at", "")
                    if last_scan:
                        dt = datetime.fromisoformat(last_scan.replace("Z", "+00:00"))
                        return f"<t:{int(dt.timestamp())}:R>"
        except:
            pass
        return "❓ Inconnu"

    # ==========================================
    # 🧠 MÉTHODE : VÉRIFICATION EN DIRECT
    # ==========================================
    async def verify_and_send(
        self, interaction: discord.Interaction, cibles: list, joueur: str, serveur: str, langue: str
    ):
        headers = await get_api_headers(custom_server=serveur)
        safe_joueur = urllib.parse.quote(joueur)
        kids = list(set([c["kid"] for c in cibles]))

        kids_str = "%5B" + ",".join(f"%22{k}%22" for k in kids) + "%5D"
        etats = {}

        url = f"{self.base_api}/dungeons?page=1&size=4000&filterByKid={kids_str}&filterByPlayerName={safe_joueur}&nearPlayerName={safe_joueur}"

        try:
            async with self.bot.session.get(url, headers=headers, timeout=10) as r:
                if r.status == 200:
                    dungeons = (await r.json()).get("dungeons", [])
                    maintenant_ts = int(discord.utils.utcnow().timestamp())

                    for d in dungeons:
                        status = "libre"
                        cd_until = d.get("effective_cooldown_until", "")

                        if cd_until:
                            try:
                                ts_cd = int(datetime.fromisoformat(cd_until.replace("Z", "+00:00")).timestamp())
                                if ts_cd > maintenant_ts:
                                    status = "feu"
                            except:
                                pass

                        etats[f"{d['kid']}_{d['position_x']}_{d['position_y']}"] = {
                            "status": status,
                            "cd_until": cd_until,
                            "last_attack": d.get("last_attack", ""),
                        }
        except Exception as e:
            logger.error(f"❌ [VERIFY] Erreur API lors de la vérification globale : {e}")

        embed = discord.Embed(
            title=t(langue, "fort_verify_title", defaut="{e_icon_search} Résultat de la vérification"),
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )

        for idx, cible in enumerate(cibles, start=1):
            cle = f"{cible['kid']}_{cible['x']}_{cible['y']}"
            etat = etats.get(cle)

            nom_f = t(
                langue,
                "fort_verify_field_name",
                idx=idx,
                royaume=cible["royaume"],
                defaut=f"{idx}. {cible['royaume']} `({cible['x']}:{cible['y']})`",
            )

            if not etat:
                status_txt = t(langue, "fort_verify_unknown", defaut="❓ **Inconnu** *(Trop loin ou disparue)*")
            elif etat["status"] == "libre":
                status_txt = t(langue, "fort_verify_free", defaut="{e_std_green_circle} **Attaquable maintenant**")
            else:
                try:
                    dt_cd = datetime.fromisoformat(etat["cd_until"].replace("Z", "+00:00"))
                    ts_cd = int(dt_cd.timestamp())
                    maintenant_ts = int(discord.utils.utcnow().timestamp())
                    minutes_restantes = max(1, int((ts_cd - maintenant_ts) / 60))

                    status_txt = t(
                        langue,
                        "fort_verify_burning",
                        mins=minutes_restantes,
                        ts=ts_cd,
                        defaut=f"🔥 **En feu** *(Dispo dans {minutes_restantes} min)*",
                    )
                except:
                    status_txt = t(langue, "fort_verify_burning_unk", defaut="🔥 **En feu** *(Temps inconnu)*")

            embed.add_field(name=nom_f, value=status_txt, inline=False)

        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed, ephemeral=False)

    # ==========================================
    # 🧠 MÉTHODE : RELANCE (LES 10 SUIVANTES)
    # ==========================================
    async def relaunch_scan(self, interaction: discord.Interaction, user_id: str, langue: str):
        data = await load_dungeons_async()
        if user_id not in data.get("sessions", {}):
            msg_err = t(
                langue,
                "fort_err_no_session",
                defaut="{e_warning} Ta session de scan est terminée ou introuvable. Relance `/fortress scan` pour en démarrer une nouvelle.",
            )
            return await interaction.followup.send(msg_err, ephemeral=False)

        info = data["sessions"][user_id]
        joueur = info["joueur"]
        kids = info["kids"]
        dist = info["distance_max"]
        notified = info.get("notified", [])
        serveur = info.get("serveur", "E4K_FR1")

        modifie, embed_cibles, cibles_list = await self.fetch_cibles(
            joueur, kids, dist, notified, self.bot.session, serveur=serveur, langue=langue
        )

        if modifie:
            info["notified"] = notified
            maintenant = discord.utils.utcnow()
            freq = info.get("frequence_minutes", 5)
            info["next_scan"] = (maintenant + timedelta(minutes=freq)).isoformat().replace("+00:00", "Z")
            data["sessions"][user_id] = info
            await save_dungeons_async(data)

        if cibles_list:
            view = FortressActionView(self, user_id, cibles_list, joueur, serveur, langue)
            await setup_embed_footer(embed_cibles, interaction, langue)
            view.message = await interaction.followup.send(embed=embed_cibles, view=view, ephemeral=False, wait=True)
        else:
            embed_attente = discord.Embed(
                title=t(
                    langue,
                    "fort_wait_title",
                    defaut="{e_std_open_mailbox_with_lowered_flag} Calme plat sur les frontières...",
                ),
                color=self.clr_attente,
                description=t(
                    langue,
                    "fort_wait_desc",
                    defaut="{e_std_sleeping_face} **Aucune forteresse n'est attaquable ou proche de s'ouvrir.**\nToutes les structures aux alentours sont verrouillées en phase de reconstruction.\n\n*Le guet reste en alerte invisible et te contactera par MP dès qu'un mur redevient vulnérable !* {e_std_hot_beverage}",
                ),
            )
            await setup_embed_footer(embed_attente, interaction, langue)
            await interaction.followup.send(embed=embed_attente, ephemeral=False)

    # ==========================================
    # 🗺️ TRI INTELLIGENT (CORRESPONDANCE X/Y)
    # ==========================================
    def chain_targets_by_coordinates(self, cibles: list) -> list:
        if not cibles:
            return []

        from collections import defaultdict

        by_kid = defaultdict(list)
        for c in cibles:
            by_kid[c["kid"]].append(c)

        result = []
        for kid, items in by_kid.items():
            # Initier la chaîne avec la cible la plus proche du joueur
            items.sort(key=lambda c: c.get("dist", 99999))
            chain = [items.pop(0)]

            while items:
                current = chain[-1]
                best_idx = 0
                best_score = float("inf")

                for i, candidate in enumerate(items):
                    dist_sq = (current["x"] - candidate["x"]) ** 2 + (current["y"] - candidate["y"]) ** 2
                    # 💡 LE CŒUR DU TRI : Bonus massif si l'axe X ou Y correspond !
                    if candidate["x"] == current["x"] or candidate["y"] == current["y"]:
                        score = dist_sq
                    else:
                        score = 1000000 + dist_sq

                    if score < best_score:
                        best_score = score
                        best_idx = i

                chain.append(items.pop(best_idx))

            result.extend(chain)

        return result

    # ==========================================
    # 🧠 LE MOTEUR DE RECHERCHE UNIFIÉ
    # ==========================================
    async def fetch_cibles(
        self,
        joueur: str,
        kids_to_scan: list,
        dist_max: int,
        notified: list,
        session: aiohttp.ClientSession,
        serveur: str = "E4K_FR1",
        langue: str = "fr",
    ):
        headers = await get_api_headers(custom_server=serveur)

        dict_royaumes = {
            1: t(langue, "fort_realm_sands", defaut="Sables {e_dungeon1}"),
            2: t(langue, "fort_realm_ice", defaut="Glaces {e_dungeon2}"),
            3: t(langue, "fort_realm_peaks", defaut="Pics {e_dungeon3}"),
        }
        safe_joueur = urllib.parse.quote(joueur)
        kids_str = "%5B" + ",".join(f"%22{k}%22" for k in kids_to_scan) + "%5D"

        cibles_dispo = []
        cibles_moins_5min = []
        cibles_moins_1h = []

        # 🟢 LA REQUÊTE GLOBALE UNIQUE
        url = f"{self.base_api}/dungeons?page=1&size=2000&filterByKid={kids_str}&filterByPlayerName={safe_joueur}&nearPlayerName={safe_joueur}"

        try:
            async with session.get(url, headers=headers, timeout=10) as r:
                if r.status == 200:
                    response_data = await r.json()
                    dungeons = response_data.get("dungeons", [])
                    maintenant_ts = int(discord.utils.utcnow().timestamp())

                    for d in dungeons:
                        kid_dungeon = d.get("kid")
                        raw_dist = d.get("distance")
                        try:
                            dist = float(raw_dist)
                        except:
                            dist = -1.0

                        if dist != -1.0 and dist > dist_max:
                            continue

                        x, y = d.get("position_x"), d.get("position_y")

                        # ⏱️ Calcul infaillible du cooldown
                        cd_until = d.get("effective_cooldown_until", "")
                        cd_secondes = 0
                        if cd_until:
                            try:
                                ts_cd = int(datetime.fromisoformat(cd_until.replace("Z", "+00:00")).timestamp())
                                cd_secondes = ts_cd - maintenant_ts
                            except:
                                pass

                        if cd_secondes < 0:
                            cd_secondes = 0

                        uid = f"{kid_dungeon}_{x}_{y}_{cd_secondes}"
                        unk_player = t(langue, "fort_unknown_player", defaut="Inconnu")
                        unk_realm = t(langue, "fort_unknown_realm", kid=kid_dungeon, defaut=f"Monde {kid_dungeon}")

                        cible_data = {
                            "uid": uid,
                            "kid": kid_dungeon,
                            "x": x,
                            "y": y,
                            "dist": dist,
                            "cooldown": cd_secondes,
                            "ancien": d.get("player_name", unk_player),
                            "royaume": dict_royaumes.get(kid_dungeon, unk_realm),
                        }

                        if uid not in notified:
                            if cd_secondes == 0:
                                cibles_dispo.append(cible_data)
                            elif 0 < cd_secondes <= 300:
                                cibles_moins_5min.append(cible_data)
                            elif 300 < cd_secondes <= 3600:
                                cibles_moins_1h.append(cible_data)

        except Exception as e:
            logger.error(f"❌ [FORTERESSES] Erreur API Globale: {e}")

        # 🟢 APPEL DU META-SCAN
        last_scan_str = await self.fetch_meta_scan(session, headers)
        lbl_last_scan = t(langue, "fort_lbl_last_scan", defaut="Dernier scan API :")
        txt_last_scan = f"\n*{DICT_EMOJIS.get('e_std_satellite_antenna', '📡')} {lbl_last_scan} {last_scan_str}*"

        # ------------------------------------------------------------------
        # 🟢 CONSTRUCTION DES RÉSULTATS : CIBLES DISPONIBLES
        # ------------------------------------------------------------------
        if cibles_dispo:
            cibles_dispo.sort(key=lambda c: c["dist"] if c["dist"] != -1.0 else 99999)
            vivier = cibles_dispo[:30]
            cibles_a_envoyer = self.chain_targets_by_coordinates(vivier)[:10]

            sessions_modifiees = False
            for c in cibles_a_envoyer:
                notified.append(c["uid"])
                sessions_modifiees = True

            titre = t(langue, "fort_title_avail", defaut="{e_attaque} CIBLES DISPONIBLES IMMÉDIATEMENT")
            embed = discord.Embed(title=titre, color=discord.Color.green())

            embed_desc_base = t(
                langue,
                "fort_desc_avail",
                count=len(cibles_a_envoyer),
                defaut=f"Le guet a repéré **{len(cibles_a_envoyer)}** forteresse(s) prête(s) au pillage :",
            )
            embed.description = embed_desc_base + txt_last_scan

            for idx, c in enumerate(cibles_a_envoyer, start=1):
                dist_str = (
                    t(langue, "fort_dist_val", dist=int(c["dist"]), defaut=f"**{int(c['dist'])}** lieues")
                    if c["dist"] != -1.0
                    else t(langue, "fort_dist_unk", defaut="{e_icon_search} Inconnue")
                )
                name_f = t(
                    langue,
                    "fort_field_name",
                    idx=idx,
                    royaume=c["royaume"],
                    dist_str=dist_str,
                    defaut=f"{idx}. {c['royaume']} ({dist_str})",
                )
                val_f = t(
                    langue,
                    "fort_field_val_avail",
                    x=c["x"],
                    y=c["y"],
                    ancien=c["ancien"],
                    defaut=f"{{e_compass}} Position : `{c['x']}:{c['y']}` {{e_empirerankings}} Dernier roi : *{c['ancien']}*\n**Statut : {{e_std_green_circle}} Attaquable maintenant**",
                )
                embed.add_field(name=name_f, value=val_f, inline=False)

            await setup_embed_footer(embed, None, langue)
            return sessions_modifiees, embed, cibles_a_envoyer

        # ------------------------------------------------------------------
        # ⏳ CONSTRUCTION DES RÉSULTATS : CIBLES EN COOLDOWN
        # ------------------------------------------------------------------
        cibles_finales = []
        embed_color = discord.Color.gold()
        embed_title = ""
        embed_desc = ""
        status_label = ""

        if cibles_moins_5min:
            cibles_moins_5min.sort(key=lambda c: c["cooldown"])
            cibles_finales = cibles_moins_5min[:10]
            embed_title = t(
                langue,
                "fort_title_imminent",
                defaut="{e_time} SURVEILLANCE : Cibles imminentes (< 5 min)",
            )
            embed_color = discord.Color.orange()
            embed_desc = t(
                langue,
                "fort_desc_imminent",
                defaut="Aucune cible libre de suite, mais le guet a identifié ces structures sur le point de s'ouvrir :",
            )
            status_label = t(langue, "fort_status_imminent", defaut="{e_deeporangebullet} Ouverture dans")
        elif cibles_moins_1h:
            cibles_moins_1h.sort(key=lambda c: c["cooldown"])
            cibles_finales = cibles_moins_1h[:10]
            embed_title = t(
                langue,
                "fort_title_anticip",
                defaut="{e_time} ANTICIPATION : Cibles en recharge (< 1 heure)",
            )
            embed_color = discord.Color.red()
            embed_desc = t(
                langue,
                "fort_desc_anticip",
                defaut="Zone calme pour l'instant. Voici les prochaines cibles disponibles dans l'heure pour préparer tes calages :",
            )
            status_label = t(langue, "fort_status_anticip", defaut="{e_tomatobulletpoint} Verrouillée pendant")

        if cibles_finales:
            sessions_modifiees = False
            for c in cibles_finales:
                notified.append(c["uid"])
                sessions_modifiees = True

            embed = discord.Embed(title=embed_title, color=embed_color)
            embed.description = embed_desc + txt_last_scan

            for idx, c in enumerate(cibles_finales, start=1):
                dist_str = (
                    t(langue, "fort_dist_val", dist=int(c["dist"]), defaut=f"**{int(c['dist'])}** lieues")
                    if c["dist"] != -1.0
                    else t(langue, "fort_dist_unk", defaut="{e_icon_search} Inconnue")
                )
                minutes_restantes = max(1, int(c["cooldown"] // 60))

                name_f = t(
                    langue,
                    "fort_field_name",
                    idx=idx,
                    royaume=c["royaume"],
                    dist_str=dist_str,
                    defaut=f"{idx}. {c['royaume']} ({dist_str})",
                )
                val_f = t(
                    langue,
                    "fort_field_val_cd",
                    x=c["x"],
                    y=c["y"],
                    ancien=c["ancien"],
                    status=status_label,
                    mins=minutes_restantes,
                    defaut=f"{{e_compass}} Position : `{c['x']}:{c['y']}` {{e_empirerankings}} Dernier roi : *{c['ancien']}*\n**Statut : {status_label} {minutes_restantes} min**",
                )

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
        peaks="Monitor the Fire Peaks? (Default: False)",
    )
    async def f_scan(
        self, interaction: discord.Interaction, player: str, ice: bool = False, sands: bool = False, peaks: bool = False
    ):
        await interaction.response.defer(ephemeral=False, thinking=True)

        langue, serveur = await get_server_config(interaction)

        DIST_MAX = 999
        nom_joueur = player.strip()

        kids = []
        if sands:
            kids.append(1)
        if ice:
            kids.append(2)
        if peaks:
            kids.append(3)

        if not kids:
            err_msg = t(
                langue,
                "fort_err_no_realms",
                defaut="{e_error} Tu as laissé tous les mondes sur `False`. Active au moins un royaume !",
            )
            return await interaction.followup.send(err_msg, ephemeral=False)

        data = await load_dungeons_async()
        user_id = str(interaction.user.id)

        if user_id in data.get("sessions", {}):
            msg_warn = t(
                langue,
                "fort_warn_overwrite",
                defaut="{e_warning} **Attention :** Tu avais déjà un radar en cours ! Il vient d'être réinitialisé. Pense à utiliser `/fortress stop` la prochaine fois pour éviter de recevoir des alertes en double.",
            )
            try:
                await interaction.followup.send(msg_warn, ephemeral=True)
            except:
                pass

        nb_sessions_actives = len(data.get("sessions", {}))
        freq_val = min(20, 5 + (nb_sessions_actives // 3))
        duree_val = max(40, 120 - ((nb_sessions_actives // 3) * 10))

        maintenant = discord.utils.utcnow()
        end_time = maintenant + timedelta(minutes=duree_val)

        info_session = {
            "joueur": nom_joueur,
            "kids": kids,
            "distance_max": DIST_MAX,
            "frequence_minutes": freq_val,
            "end_time": end_time.isoformat().replace("+00:00", "Z"),
            "next_scan": (maintenant + timedelta(minutes=freq_val)).isoformat().replace("+00:00", "Z"),
            "notified": [],
            "serveur": serveur,
        }

        modifie, embed_cibles, cibles_list = await self.fetch_cibles(
            nom_joueur, kids, DIST_MAX, info_session["notified"], self.bot.session, serveur=serveur, langue=langue
        )

        data["sessions"][user_id] = info_session
        await save_dungeons_async(data)

        ts_fin = int(end_time.timestamp())
        noms_royaumes = []
        if 1 in kids:
            noms_royaumes.append(t(langue, "fort_realm_sands", defaut="Sables {e_dungeon1}"))
        if 2 in kids:
            noms_royaumes.append(t(langue, "fort_realm_ice", defaut="Glaces {e_dungeon2}"))
        if 3 in kids:
            noms_royaumes.append(t(langue, "fort_realm_peaks", defaut="Pics {e_dungeon3}"))

        st_fluide = t(langue, "fort_status_fluid", defaut="{e_greencirclebullet} `Fluide`")
        st_mod = t(langue, "fort_status_mod", defaut="{e_deeporangebullet} `Modérée`")
        st_elev = t(langue, "fort_status_high", defaut="{e_tomatobulletpoint} `Élevée`")
        status_charge = st_fluide if nb_sessions_actives < 4 else st_mod if nb_sessions_actives < 8 else st_elev

        embed_conf = discord.Embed(
            title=t(langue, "fort_setup_title", defaut="{e_events4} Dispositif de Guet Activé"),
            color=self.clr_activation,
        )
        embed_conf.add_field(
            name=t(langue, "fort_field_player", defaut="{e_players} Joueur"),
            value=f"`{nom_joueur}`",
            inline=False,
        )
        embed_conf.add_field(
            name=t(langue, "fort_field_realms", defaut="{e_icon_world} Mondes"),
            value=" • ".join(noms_royaumes),
            inline=False,
        )
        embed_conf.add_field(
            name=t(langue, "fort_field_freq", defaut="{e_time} Fréquence"), value=f"`{freq_val} min`", inline=True
        )
        embed_conf.add_field(
            name=t(langue, "fort_field_end", defaut="⏳ Fin du guet"), value=f"<t:{ts_fin}:R>", inline=True
        )
        embed_conf.add_field(
            name=t(langue, "fort_field_load", defaut="{e_stats} Charge Bot"), value=f"{status_charge}", inline=True
        )
        await setup_embed_footer(embed_conf, interaction, langue)
        await interaction.followup.send(embed=embed_conf, ephemeral=False)

        logger.info(f"🟢 [FORTERESSES] {interaction.user.name} a démarré un scan radar pour {nom_joueur}.")

        if cibles_list:
            view = FortressActionView(self, user_id, cibles_list, nom_joueur, serveur, langue)
            await setup_embed_footer(embed_cibles, interaction, langue)
            view.message = await interaction.followup.send(embed=embed_cibles, view=view, ephemeral=False, wait=True)
        else:
            embed_attente = discord.Embed(
                title=t(
                    langue,
                    "fort_wait_title",
                    defaut="{e_std_open_mailbox_with_lowered_flag} Calme plat sur les frontières...",
                ),
                color=self.clr_attente,
                description=t(
                    langue,
                    "fort_wait_desc",
                    defaut="{e_std_sleeping_face} **Aucune forteresse n'est attaquable ou proche de s'ouvrir (rien à moins d'une heure).**\nToutes les structures aux alentours sont verrouillées en phase de reconstruction.\n\n*Profites-en pour former de nouvelles troupes, gérer ton alliance ou t'accorder une pause café. Le guet reste en alerte invisible et te contactera par MP dès qu'un mur redevient vulnérable !* {e_std_hot_beverage}",
                ),
            )
            await setup_embed_footer(embed_attente, interaction, langue)
            await interaction.followup.send(embed=embed_attente, ephemeral=False)
        await prompt_vote_if_lucky(interaction, probability_percent=15, langue=langue)

    # ==========================================
    # 🔴 COMMANDE : STOP
    # ==========================================
    @app_commands.command(name="stop", description="Stop your fortress scanning session")
    async def f_stop(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=False, thinking=True)
        except:
            return

        langue, _ = await get_server_config(interaction)
        data = await load_dungeons_async()
        user_id = str(interaction.user.id)

        if user_id in data["sessions"]:
            del data["sessions"][user_id]
            await save_dungeons_async(data)
            logger.info(f"🛑 [FORTERESSES] {interaction.user.name} a stoppé son scan radar.")
            await interaction.followup.send(
                t(
                    langue,
                    "fort_stop_success",
                    defaut="{e_check} **Session arrêtée.** Fin des alertes.",
                ),
                ephemeral=False,
            )
        else:
            await interaction.followup.send(
                t(langue, "fort_stop_fail", defaut="{e_error} Tu n'as aucune session active."),
                ephemeral=False,
            )
        await prompt_vote_if_lucky(interaction, probability_percent=15, langue=langue)

    # ==========================================
    # 🛰️ LA TÂCHE DE FOND
    # ==========================================
    @tasks.loop(minutes=1)
    async def dungeon_spy_task(self):
        try:
            data = await load_dungeons_async()
            sessions = data.get("sessions", {})
            if not sessions:
                return

            users_data = {}
            path_users = CONFIG_DIR / "users.json"
            if os.path.exists(path_users):
                try:
                    with open(path_users, encoding="utf-8") as f:
                        users_data = json.load(f)
                except:
                    pass

            maintenant = discord.utils.utcnow()
            sessions_modifiees = False

            for user_id, info in list(sessions.items()):
                joueur = info.get("joueur", "Inconnu")
                serveur = info.get("serveur", "E4K_FR1")
                langue = users_data.get(user_id, {}).get("langue", "fr")

                try:
                    end_dt = datetime.fromisoformat(info["end_time"].replace("Z", "+00:00"))
                    if maintenant > end_dt:
                        del data["sessions"][user_id]
                        sessions_modifiees = True
                        try:
                            user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                            msg = t(
                                langue,
                                "fort_session_ended",
                                joueur=joueur,
                                defaut="{e_information} **Fin de session !** Ton radar pour **{joueur}** s'est éteint.",
                            )
                            await user.send(msg)
                            logger.info(f"🏁 [FORTERESSES] Session terminée naturellement pour {user.name}.")
                        except:
                            pass
                        continue
                except:
                    pass

                try:
                    next_scan_dt = datetime.fromisoformat(info["next_scan"].replace("Z", "+00:00"))
                    if maintenant < next_scan_dt:
                        continue
                except:
                    pass

                kids = info.get("kids", [2])
                dist = info.get("distance_max", 999)
                if "notified" not in info:
                    info["notified"] = []

                modifie, embed_cibles, cibles_list = await self.fetch_cibles(
                    joueur, kids, dist, info["notified"], self.bot.session, serveur=serveur, langue=langue
                )

                freq = info.get("frequence_minutes", 5)
                info["next_scan"] = (maintenant + timedelta(minutes=freq)).isoformat().replace("+00:00", "Z")
                sessions_modifiees = True

                if cibles_list:
                    try:
                        user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                        view = FortressActionView(self, user_id, cibles_list, joueur, serveur, langue)
                        logger.info(
                            f"📤 [FORTERESSES] Envoi automatique de nouvelles cibles à {user.name} ({user_id}) pour {joueur}."
                        )
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
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def f_history(self, interaction: discord.Interaction, player: str):
        try:
            await interaction.response.defer(ephemeral=False, thinking=True)
        except:
            return

        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(custom_server=serveur)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="{e_time} **Données datées de :**")

        player_id = None
        vrai_nom = player
        cache = await get_cached_data(serveur)
        local_data = cache.get("players_data", {})

        for p_name, p_info in local_data.items():
            if p_name.lower() == player.lower():
                player_id = str(p_info.get("player_id", p_info.get("id", "")))
                vrai_nom = p_name
                break

        if not player_id:
            try:
                async with self.bot.session.get(
                    f"{self.base_api}/players/{urllib.parse.quote(player)}", headers=headers, timeout=8
                ) as r:
                    if r.status == 200:
                        p_data = await r.json()
                        if isinstance(p_data, list) and p_data:
                            p_data = p_data[0]
                            player_id = str(p_data.get("player_id", p_data.get("id", "")))
                            vrai_nom = p_data.get("player_name", player)
            except:
                pass

        if not player_id:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_woa_player_not_found",
                    p=player,
                    defaut=f"{{e_error}} Joueur **{player}** introuvable sur le serveur {serveur}.",
                )
            )

        # On interroge l'API pour l'historique sur 365 jours
        url_history = f"{self.base_api}/dungeons/player/{player_id}?lastDays=365"
        try:
            async with self.bot.session.get(url_history, headers=headers, timeout=12) as r:
                if r.status != 200:
                    return await interaction.followup.send(
                        t(
                            langue,
                            "fort_err_api",
                            defaut="{e_error} Impossible de récupérer l'historique depuis l'API.",
                        )
                    )
                data = await r.json()
                dungeons = data.get("dungeons", [])
        except Exception as e:
            logger.error(f"❌ Erreur API Fortress History : {e}")
            return await interaction.followup.send(
                t(langue, "ev_err_tech", e=str(e), defaut=f"{{e_error}} Erreur technique : {e}")
            )

        if not dungeons:
            return await interaction.followup.send(
                t(
                    langue,
                    "fort_hist_empty",
                    p=vrai_nom,
                    defaut=f"📭 **{vrai_nom}** n'a attaqué aucune forteresse enregistrée durant l'année écoulée.",
                )
            )

        # Tri pour avoir les plus récents en haut
        dungeons.sort(key=lambda x: x.get("attacked_at", ""), reverse=True)

        latest_attack_ts = int(discord.utils.utcnow().timestamp())
        if dungeons and dungeons[0].get("attacked_at"):
            try:
                latest_dt = datetime.fromisoformat(dungeons[0]["attacked_at"].replace("Z", "+00:00"))
                latest_attack_ts = int(latest_dt.timestamp())
            except:
                pass

        total_hits = len(dungeons)

        dict_royaumes = {
            1: t(langue, "fort_realm_sands", defaut="Sables {e_dungeon1}"),
            2: t(langue, "fort_realm_ice", defaut="Glaces {e_dungeon2}"),
            3: t(langue, "fort_realm_peaks", defaut="Pics {e_dungeon3}"),
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

        stats_desc = t(
            langue,
            "fort_hist_stats",
            tot=total_hits,
            rp=royaume_prefere,
            pct=f"{pct_kid:.1f}",
            j=jour_actif_str,
            jc=best_date_count,
            rub=rubies_str,
            defaut=(
                f"**{{e_stats}} Bilan Annuel**\n"
                f"{{e_attaque}} **Total des attaques :** {total_hits}\n"
                f"{{e_ruby}} **Gains estimés :** {rubies_str} Rubis\n"
                f"{{e_std_world_map}} **Royaume favori :** {royaume_prefere} *( {pct_kid:.1f}% des frappes )*\n"
                f"{{e_std_crossed_swords}} **Jour le plus actif :** {jour_actif_str} *( {best_date_count} attaques )*"
            ),
        )

        # ---------------------------------------------------------
        # LOGIQUE : GROUPEMENT DES DONNÉES JOUR PAR JOUR
        # ---------------------------------------------------------
        daily_stats = {}
        for d in dungeons:
            kid = d.get("kid")
            dt_str = d.get("attacked_at", "")[:10]  # On extrait "YYYY-MM-DD"

            if not dt_str or not kid:
                continue

            if dt_str not in daily_stats:
                daily_stats[dt_str] = {1: 0, 2: 0, 3: 0, "rubies": 0}

            daily_stats[dt_str][kid] += 1

            # Calcul des rubis de la frappe
            rubies_gained = {1: 280, 2: 50, 3: 370}.get(kid, 0)
            daily_stats[dt_str]["rubies"] += rubies_gained

        lignes = []
        # On trie les jours du plus récent au plus ancien
        for day_str, stats in sorted(daily_stats.items(), reverse=True):
            try:
                day_obj = datetime.strptime(day_str, "%Y-%m-%d")
                day_fmt = day_obj.strftime("%d/%m/%Y")
            except:
                day_fmt = day_str

            details_royaumes = []
            if stats[1] > 0:
                details_royaumes.append(f"{stats[1]} {DICT_EMOJIS.get('e_dungeon1', '🏰')}")
            if stats[2] > 0:
                details_royaumes.append(f"{stats[2]} {DICT_EMOJIS.get('e_dungeon2', '🏰')}")
            if stats[3] > 0:
                details_royaumes.append(f"{stats[3]} {DICT_EMOJIS.get('e_dungeon3', '🏰')}")

            texte_royaumes = " │ ".join(details_royaumes)
            jour_rubis = f"{stats['rubies']:,}".replace(",", " ")

            lignes.append(f"• **{day_fmt}** : {texte_royaumes} ➔ **{jour_rubis}** {DICT_EMOJIS.get('e_ruby', '💎')}")

        embeds = []
        chunk_size = 5  # On peut mettre 5 lignes par page car elles sont plus courtes
        nb_pages = max(1, (len(lignes) - 1) // chunk_size + 1)

        title = t(
            langue,
            "fort_hist_title",
            p=vrai_nom,
            defaut=f"{{e_events4}} Historique des Pillages : {vrai_nom}",
        )

        for i in range(0, len(lignes), chunk_size):
            chunk = lignes[i : i + chunk_size]
            page_actuelle = (i // chunk_size) + 1

            embed = discord.Embed(title=title, color=self.clr_attente)
            embed.description = f"{lbl_date} <t:{latest_attack_ts}:F> (<t:{latest_attack_ts}:R>)\n\n{stats_desc}"

            field_value = "\n".join(chunk)
            f_title = t(
                langue,
                "fort_hist_page_title",
                curr=page_actuelle,
                tot=nb_pages,
                defaut=f"{{e_memberlist}} Rapports journaliers (Page {page_actuelle}/{nb_pages})",
            )

            if field_value:
                embed.add_field(name=f_title, value=field_value, inline=False)

            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        if not embeds:
            return

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            view.message = await interaction.followup.send(embed=embeds[0], view=view, wait=True)
        await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)


async def setup(bot: commands.Bot):
    await bot.add_cog(ForteressesCog(bot))
