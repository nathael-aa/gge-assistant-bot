import asyncio
import json
import logging
import traceback
from datetime import datetime

import discord
from discord.ext import tasks

import observability as obs
from utils import (
    CONFIG_DIR,
    DICT_EMOJIS,
    format_num,
    get_api_headers,
    get_discord_timestamp,
    load_surveillance_async,
    save_surveillance_async,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")

RANKS_MAP = {
    0: "Chef",
    1: "Représentant",
    2: "Maréchal",
    3: "Trésorier",
    4: "Diplomate",
    5: "Recruteur",
    6: "Général",
    7: "Sergent",
    8: "Membre",
    9: "Novice",
}


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
        9: t(langue, "prof_role_9", defaut="Novice"),
    }
    return mapping.get(rank_int, t(langue, "prof_role_fallback", r=rank_int, defaut=f"Grade {rank_int}"))


class RadarSpyMixin:
    """Mixin pour injecter les tâches de fond dans le RadarCog"""

    FILTRES_ALLIANCE = ("infos", "mouvements", "rangs")

    async def envoyer_alerte_privee(
        self,
        abonnes: dict,
        type_filtre: str,
        embeds_locales: dict,
        users_lang: dict,
        target_name: str = "",
        target_id: str = "",
        gge_server: str = "",
    ):
        destinataires = livres = bloques = echecs = 0

        for user_id, prefs in abonnes.items():
            if prefs.get(type_filtre, False):
                destinataires += 1
                try:
                    langue = users_lang.get(user_id, "fr")
                    embed = embeds_locales.get(langue, embeds_locales.get("fr"))
                    if not embed:
                        destinataires -= 1
                        continue

                    await setup_embed_footer(embed, None, langue)
                    user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                    await user.send(embed=embed)
                    livres += 1
                except discord.Forbidden:
                    bloques += 1
                    logger.warning(f"⚠️ MP bloqués pour {user_id}. Ajout à la purge.")
                    self.users_to_purge.add(user_id)
                except discord.HTTPException as e:
                    if getattr(e, "code", 0) == 50007:
                        bloques += 1
                        logger.warning(f"⚠️ MP impossibles pour {user_id}. Ajout à la purge.")
                        self.users_to_purge.add(user_id)
                    else:
                        echecs += 1
                except Exception as e:
                    echecs += 1

        if destinataires:
            est_alliance = type_filtre in self.FILTRES_ALLIANCE
            obs.record_alert(
                source="radar",
                alert_type=type_filtre,
                gge_server=gge_server or self._serveur_courant,
                target_type="alliance" if est_alliance else "player",
                target_id=target_id,
                target_name=target_name,
                channel="dm",
                recipients=destinataires,
                delivered=livres,
                failed=echecs,
                dm_blocked=bloques,
            )

    async def envoyer_alerte_serveur(self, guild_abonnes: dict, type_filtre: str, embeds_locales: dict):
        if not guild_abonnes:
            return

        data = await load_surveillance_async()
        guild_configs = data.get("guild_configs", {})

        for guild_id_str, prefs in guild_abonnes.items():
            if not prefs.get(type_filtre, False):
                continue

            config = guild_configs.get(guild_id_str)
            if not config or not config.get("channel_id"):
                continue

            langue = config.get("langue", "fr")
            embed = embeds_locales.get(langue, embeds_locales.get("fr"))
            if not embed:
                continue

            try:
                channel = self.bot.get_channel(config["channel_id"]) or await self.bot.fetch_channel(
                    config["channel_id"]
                )

                content = None
                if type_filtre in ["colombe", "infos", "mouvements"] and config.get("ping_role"):
                    content = config["ping_role"]

                await setup_embed_footer(embed, None, langue)
                await channel.send(content=content, embed=embed)
            except (discord.Forbidden, discord.NotFound):
                logger.warning(f"⚠️ Accès bloqué ou salon supprimé pour le serveur {guild_id_str}. Ajout à la purge.")
                self.guilds_to_purge.add(guild_id_str)
            except Exception as e:
                logger.error(f"❌ Impossible d'envoyer l'alerte radar au serveur {guild_id_str} : {e}")

    @tasks.loop(seconds=20)
    async def radar_spy_task(self):
        obs.set_task_name("radar_spy_task")
        try:
            maintenant = discord.utils.utcnow()
            now_ts = maintenant.timestamp()

            data = await load_surveillance_async()
            players = data.get("players", {})
            alliances_trackees = data.get("alliances", {})

            if not players and not alliances_trackees:
                return

            path_users = CONFIG_DIR / "users.json"

            def _check_and_read_users(cache_mtime):
                if path_users.exists():
                    current_mtime = path_users.stat().st_mtime
                    if current_mtime > cache_mtime:
                        with open(path_users, encoding="utf-8") as f:
                            return current_mtime, json.load(f)
                return None, None

            try:
                new_mtime, users_data = await asyncio.to_thread(_check_and_read_users, self.users_cache_mtime)
                if users_data is not None:
                    self.users_lang_cache = {uid: info.get("langue", "fr") for uid, info in users_data.items()}
                    self.users_cache_mtime = new_mtime
            except Exception as e:
                logger.error(f"❌ [Radar Spy] Erreur lecture users.json : {e}")

            users_lang = self.users_lang_cache
            changes_detected = False
            session = self.bot.session

            targets_by_server = {}
            for a_id, a_info in alliances_trackees.items():
                srv = a_info.get("serveur", "E4K_FR1").upper()
                if srv not in targets_by_server:
                    targets_by_server[srv] = {"alliances": [], "players": []}
                targets_by_server[srv]["alliances"].append((a_id, a_info))

            for p_id, p_info in players.items():
                srv = p_info.get("serveur", "E4K_FR1").upper()
                if srv not in targets_by_server:
                    targets_by_server[srv] = {"alliances": [], "players": []}
                targets_by_server[srv]["players"].append((p_id, p_info))

            e_alli = DICT_EMOJIS.get("e_alliance_icon", "🛡️")
            e_crown = DICT_EMOJIS.get("e_std_crown", "👑")
            e_door = DICT_EMOJIS.get("e_std_door", "🚪")
            e_green = DICT_EMOJIS.get("e_std_green_circle", "🟢")
            e_red = DICT_EMOJIS.get("e_std_red_circle", "🔴")
            e_inc = DICT_EMOJIS.get("e_std_chart_increasing", "📈")
            e_dec = DICT_EMOJIS.get("e_std_chart_decreasing", "📉")
            e_honor = DICT_EMOJIS.get("e_honor", "🎖️")
            e_warning = DICT_EMOJIS.get("e_warning", "⚠️")
            e_time = DICT_EMOJIS.get("e_time", "⏱️")
            e_dove = DICT_EMOJIS.get("e_std_dove", "🕊️")
            e_swords = DICT_EMOJIS.get("e_std_crossed_swords", "⚔️")
            e_refresh = DICT_EMOJIS.get("e_refresh", "🔄")

            for serveur, targets in targets_by_server.items():
                self._serveur_courant = serveur
                headers = await get_api_headers(custom_server=serveur)
                cache_key_server = f"server_base_{serveur}"

                if now_ts < self.next_scan.get(cache_key_server, 0):
                    continue

                url_base = "https://api.gge-tracker.com/api/v1/"
                req_headers = headers.copy()
                if cache_key_server in self.etags_cache:
                    req_headers["If-None-Match"] = self.etags_cache[cache_key_server]

                try:
                    async with session.get(url_base, headers=req_headers, timeout=10) as r_base:
                        if r_base.status == 304:
                            continue

                        if r_base.status == 200:
                            base_data = await r_base.json()
                            polling = base_data.get("polling", {})
                            new_etag = polling.get("etag") or r_base.headers.get("ETag")
                            interval = polling.get("recommended_interval_seconds", 60)

                            if new_etag:
                                self.etags_cache[cache_key_server] = str(new_etag)
                            self.next_scan[cache_key_server] = now_ts + interval

                except Exception as e:
                    logger.error(f"❌ [Radar Spy] Erreur check global {serveur} : {e}")
                    continue

                for a_id, a_info in targets["alliances"]:
                    old_name = a_info.get("name", "Inconnu")
                    old_members = a_info.get("members", {})
                    abonnes_alliance = a_info.get("abonnes", {})
                    guild_abonnes_alliance = a_info.get("guild_abonnes", {})

                    if not abonnes_alliance and not guild_abonnes_alliance:
                        continue

                    try:
                        url_alli_live = f"https://api.gge-tracker.com/api/v1/alliances/id/{a_id}"
                        async with session.get(url_alli_live, headers=headers, timeout=10) as r_live:
                            if r_live.status == 200:
                                max_data = await r_live.json()
                                if isinstance(max_data, list) and max_data:
                                    max_data = max_data[0]

                                new_name = max_data.get("alliance_name", old_name)
                                new_members_raw = max_data.get(
                                    "players", max_data.get("members", max_data.get("playerList", []))
                                )

                                new_members_dict = {}
                                for m in new_members_raw:
                                    p_id = str(m.get("player_id", m.get("playerId", "")))
                                    if p_id:
                                        new_members_dict[p_id] = {
                                            "name": m.get("player_name", m.get("playerName", "Inconnu")),
                                            "rank": int(m.get("alliance_rank", 8)),
                                        }

                                if new_name != old_name:
                                    embeds_locales = {}
                                    for lg in ["fr", "de", "en"]:
                                        embed = discord.Embed(
                                            title=t(
                                                lg,
                                                "rad_spy_alli_name_title",
                                                defaut=f"{e_alli} ALERTE ALLIANCE : NOUVEAU NOM",
                                            ),
                                            color=discord.Color.purple(),
                                        )
                                        embed.add_field(
                                            name=t(lg, "rad_spy_alli_name_mod", defaut="Modification"),
                                            value=f"~~{old_name}~~ ➔ **{new_name}**",
                                        )
                                        embeds_locales[lg] = embed
                                    await self.envoyer_alerte_privee(
                                        abonnes_alliance,
                                        "infos",
                                        embeds_locales,
                                        users_lang,
                                        target_name=a_info.get("name", ""),
                                        target_id=str(a_id),
                                        gge_server=serveur,
                                    )
                                    await self.envoyer_alerte_serveur(guild_abonnes_alliance, "infos", embeds_locales)
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
                                                new_leader = m_info["name"]
                                            elif new_rank < old_rank:
                                                rangs_changes.append(
                                                    {"info": m_info, "old": old_rank, "new": new_rank, "type": "promo"}
                                                )
                                            else:
                                                rangs_changes.append(
                                                    {"info": m_info, "old": old_rank, "new": new_rank, "type": "demo"}
                                                )

                                for pid, m_info in old_members.items():
                                    if pid not in new_members_dict:
                                        sorties.append(m_info)

                                if new_leader:
                                    embeds_locales = {}
                                    for lg in ["fr", "de", "en"]:
                                        embed = discord.Embed(
                                            title=t(
                                                lg,
                                                "rad_spy_alli_lead_title",
                                                defaut=f"{e_crown} ALERTE ALLIANCE : NOUVEAU CHEF",
                                            ),
                                            description=t(
                                                lg,
                                                "rad_spy_alli_lead_desc",
                                                a=new_name,
                                                defaut=f"L'alliance **{new_name}** a changé de couronne !",
                                            ),
                                            color=discord.Color.gold(),
                                        )
                                        embed.add_field(
                                            name=t(lg, "rad_spy_alli_lead_f", defaut="Nouveau Chef"),
                                            value=t(
                                                lg,
                                                "rad_spy_alli_lead_v",
                                                p=new_leader,
                                                defaut=f"**{new_leader}** a pris le contrôle.",
                                            ),
                                        )
                                        embeds_locales[lg] = embed
                                    await self.envoyer_alerte_privee(
                                        abonnes_alliance,
                                        "infos",
                                        embeds_locales,
                                        users_lang,
                                        target_name=a_info.get("name", ""),
                                        target_id=str(a_id),
                                        gge_server=serveur,
                                    )
                                    await self.envoyer_alerte_serveur(guild_abonnes_alliance, "infos", embeds_locales)

                                if entrees or sorties:
                                    embeds_locales = {}
                                    for lg in ["fr", "de", "en"]:
                                        entrees_loc, sorties_loc = [], []
                                        for m in entrees:
                                            entrees_loc.append(
                                                t(
                                                    lg,
                                                    "rad_spy_alli_join",
                                                    p=m["name"],
                                                    defaut=f"{e_green} **{m['name']}** a rejoint l'alliance.",
                                                )
                                            )
                                        for m in sorties:
                                            sorties_loc.append(
                                                t(
                                                    lg,
                                                    "rad_spy_alli_leave",
                                                    p=m["name"],
                                                    defaut=f"{e_red} **{m['name']}** a quitté l'alliance.",
                                                )
                                            )

                                        embed = discord.Embed(
                                            title=t(
                                                lg,
                                                "rad_spy_alli_mouv_title",
                                                defaut=f"{e_door} ALERTE ALLIANCE : MOUVEMENTS",
                                            ),
                                            description=t(
                                                lg,
                                                "rad_spy_alli_mouv_desc",
                                                a=new_name,
                                                defaut=f"Mouvements récents chez **{new_name}**",
                                            ),
                                            color=discord.Color.blue(),
                                        )
                                        if entrees_loc:
                                            embed.add_field(
                                                name=t(lg, "rad_spy_alli_mouv_in", defaut="Nouvelles Recrues"),
                                                value="\n".join(entrees_loc[:10]),
                                                inline=False,
                                            )
                                        if sorties_loc:
                                            embed.add_field(
                                                name=t(lg, "rad_spy_alli_mouv_out", defaut="Départs"),
                                                value="\n".join(sorties_loc[:10]),
                                                inline=False,
                                            )
                                        embeds_locales[lg] = embed
                                    await self.envoyer_alerte_privee(
                                        abonnes_alliance,
                                        "mouvements",
                                        embeds_locales,
                                        users_lang,
                                        target_name=a_info.get("name", ""),
                                        target_id=str(a_id),
                                        gge_server=serveur,
                                    )
                                    await self.envoyer_alerte_serveur(
                                        guild_abonnes_alliance, "mouvements", embeds_locales
                                    )

                                if rangs_changes:
                                    embeds_locales = {}
                                    for lg in ["fr", "de", "en"]:
                                        rc_loc = []
                                        for r in rangs_changes:
                                            old_l = get_rank_str(r["old"], lg)
                                            new_l = get_rank_str(r["new"], lg)
                                            if r["type"] == "promo":
                                                rc_loc.append(
                                                    t(
                                                        lg,
                                                        "rad_spy_alli_prom",
                                                        p=r["info"]["name"],
                                                        r1=old_l,
                                                        r2=new_l,
                                                        defaut=f"{e_inc} **{r['info']['name']}** a été promu ({old_l} ➔ {new_l})",
                                                    )
                                                )
                                            else:
                                                rc_loc.append(
                                                    t(
                                                        lg,
                                                        "rad_spy_alli_demo",
                                                        p=r["info"]["name"],
                                                        r1=old_l,
                                                        r2=new_l,
                                                        defaut=f"{e_dec} **{r['info']['name']}** a été rétrogradé ({old_l} ➔ {new_l})",
                                                    )
                                                )
                                        embed = discord.Embed(
                                            title=t(
                                                lg,
                                                "rad_spy_alli_rank_title",
                                                defaut=f"{e_honor} ALERTE ALLIANCE : GRADES",
                                            ),
                                            description=t(
                                                lg,
                                                "rad_spy_alli_rank_desc",
                                                a=new_name,
                                                defaut=f"Changements de hiérarchie chez **{new_name}**",
                                            ),
                                            color=discord.Color.orange(),
                                        )
                                        embed.add_field(
                                            name=t(lg, "rad_spy_alli_rank_f", defaut="Mouvements internes"),
                                            value="\n".join(rc_loc[:10]),
                                            inline=False,
                                        )
                                        embeds_locales[lg] = embed
                                    await self.envoyer_alerte_privee(
                                        abonnes_alliance,
                                        "rangs",
                                        embeds_locales,
                                        users_lang,
                                        target_name=a_info.get("name", ""),
                                        target_id=str(a_id),
                                        gge_server=serveur,
                                    )
                                    await self.envoyer_alerte_serveur(guild_abonnes_alliance, "rangs", embeds_locales)

                                if new_name != old_name or entrees or sorties or rangs_changes:
                                    a_info["members"] = new_members_dict
                                    changes_detected = True
                    except Exception as e:
                        logger.error(f"❌ [Radar Spy] Erreur analyse alliance {a_id} : {e}")

                tracked_players = targets["players"]
                if tracked_players:
                    player_ids = [p_id for p_id, p_info in tracked_players]
                    try:
                        url_bulk = "https://api.gge-tracker.com/api/v1/players"
                        async with session.post(url_bulk, headers=headers, json=player_ids, timeout=10) as r:
                            if r.status == 200:
                                bulk_data_raw = await r.json()
                                bulk_data = bulk_data_raw.get("players", [])
                                api_players = {str(p["player_id"]): p for p in bulk_data}

                                for p_id, info in tracked_players:
                                    abonnes = info.get("abonnes", {})
                                    guild_abonnes = info.get("guild_abonnes", {})

                                    if not abonnes and not guild_abonnes:
                                        continue

                                    p_data = api_players.get(p_id)
                                    if not p_data:
                                        continue

                                    player = info["name"]
                                    new_name = p_data.get("player_name", player)
                                    if new_name != player:
                                        embeds_locales = {}
                                        for lg in ["fr", "de", "en"]:
                                            old_t = (
                                                t(lg, "prof_unknown", defaut="Inconnu")
                                                if player == "Inconnu"
                                                else player
                                            )
                                            new_t = (
                                                t(lg, "prof_unknown", defaut="Inconnu")
                                                if new_name == "Inconnu"
                                                else new_name
                                            )
                                            embed = discord.Embed(
                                                title=t(
                                                    lg, "rad_spy_p_name_title", defaut=f"{e_warning} ALERTE PSEUDO"
                                                ),
                                                color=discord.Color.orange(),
                                            )
                                            embed.add_field(
                                                name="Cible",
                                                value=t(
                                                    lg,
                                                    "rad_spy_p_name_f",
                                                    j=player,
                                                    old=old_t,
                                                    new=new_t,
                                                    time=get_discord_timestamp(maintenant.isoformat(), "R", lg),
                                                    defaut=f"~~{old_t}~~ ➔ **{new_t}**\n{e_time} *Fait {get_discord_timestamp(maintenant.isoformat(), 'R', lg)}*",
                                                ),
                                            )
                                            embeds_locales[lg] = embed

                                        await self.envoyer_alerte_privee(
                                            abonnes,
                                            "pseudo",
                                            embeds_locales,
                                            users_lang,
                                            target_name=player,
                                            target_id=str(p_id),
                                            gge_server=serveur,
                                        )
                                        await self.envoyer_alerte_serveur(guild_abonnes, "pseudo", embeds_locales)
                                        info["name"], info["last_name"] = (
                                            new_name,
                                            maintenant.isoformat().replace("+00:00", "Z"),
                                        )
                                        player = new_name
                                        changes_detected = True

                                    old_alli = info.get("last_alliance_name")
                                    new_alli = p_data.get("alliance_name") or "Sans alliance"

                                    if old_alli is None:
                                        info["last_alliance_name"] = new_alli
                                        changes_detected = True
                                    elif new_alli != old_alli:
                                        embeds_locales = {}
                                        for lg in ["fr", "de", "en"]:
                                            old_t = (
                                                t(lg, "prof_no_alliance", defaut="Sans alliance")
                                                if old_alli == "Sans alliance"
                                                else old_alli
                                            )
                                            new_t = (
                                                t(lg, "prof_no_alliance", defaut="Sans alliance")
                                                if new_alli == "Sans alliance"
                                                else new_alli
                                            )
                                            embed = discord.Embed(
                                                title=t(
                                                    lg, "rad_spy_p_alli_title", defaut=f"{e_warning} ALERTE ALLIANCE"
                                                ),
                                                color=discord.Color.brand_red(),
                                            )
                                            embed.add_field(
                                                name="Cible",
                                                value=t(
                                                    lg,
                                                    "rad_spy_p_alli_f",
                                                    j=player,
                                                    old=old_t,
                                                    new=new_t,
                                                    time=get_discord_timestamp(maintenant.isoformat(), "R", lg),
                                                    defaut=f"**{player}**\n*{old_t}* ➔ **{new_t}**\n{e_time} *Fait {get_discord_timestamp(maintenant.isoformat(), 'R', lg)}*",
                                                ),
                                            )
                                            embeds_locales[lg] = embed

                                        await self.envoyer_alerte_privee(
                                            abonnes,
                                            "alliance",
                                            embeds_locales,
                                            users_lang,
                                            target_name=player,
                                            target_id=str(p_id),
                                            gge_server=serveur,
                                        )
                                        await self.envoyer_alerte_serveur(guild_abonnes, "alliance", embeds_locales)
                                        info["last_alliance_name"], info["last_alliance"] = (
                                            new_alli,
                                            maintenant.isoformat().replace("+00:00", "Z"),
                                        )
                                        changes_detected = True

                                    current_might = int(p_data.get("might_current", 0))
                                    diff = current_might - info.get("last_might", current_might)
                                    if abs(diff) >= 500_000:
                                        emoji_str, color = (
                                            (e_inc, discord.Color.green())
                                            if diff > 0
                                            else (e_dec, discord.Color.brand_red())
                                        )
                                        sign = "+" if diff > 0 else ""
                                        embeds_locales = {}
                                        for lg in ["fr", "de", "en"]:
                                            embed = discord.Embed(
                                                title=t(
                                                    lg,
                                                    "rad_spy_p_pp_title",
                                                    emoji=emoji_str,
                                                    defaut=f"{e_warning} ALERTE PUISSANCE {emoji_str}",
                                                ),
                                                color=color,
                                            )
                                            embed.add_field(
                                                name="Cible",
                                                value=t(
                                                    lg,
                                                    "rad_spy_p_pp_f",
                                                    j=player,
                                                    old=format_num(info.get("last_might")),
                                                    new=format_num(current_might),
                                                    diff=f"{sign}{format_num(diff)}",
                                                    defaut=f"**{player}**\nAncienne: {format_num(info.get('last_might'))}\nNouvelle: **{format_num(current_might)}**\nDiff: **{sign}{format_num(diff)} PP**",
                                                ),
                                            )
                                            embeds_locales[lg] = embed

                                        await self.envoyer_alerte_privee(
                                            abonnes,
                                            "puissance",
                                            embeds_locales,
                                            users_lang,
                                            target_name=player,
                                            target_id=str(p_id),
                                            gge_server=serveur,
                                        )
                                        await self.envoyer_alerte_serveur(guild_abonnes, "puissance", embeds_locales)
                                        info["last_might"] = current_might
                                        changes_detected = True

                                    new_peace = p_data.get("peace_disabled_at")
                                    if new_peace == "null":
                                        new_peace = None
                                    old_peace, was_protected = (
                                        info.get("peace_disabled_at"),
                                        info.get("is_protected", False),
                                    )
                                    is_protected, new_dt = False, None

                                    if new_peace:
                                        try:
                                            new_dt = datetime.fromisoformat(new_peace.replace("Z", "+00:00"))
                                            if new_dt > discord.utils.utcnow():
                                                is_protected = True
                                        except Exception:
                                            pass

                                    msgs_trigger = []
                                    if new_peace != old_peace and is_protected:
                                        if not was_protected:
                                            msgs_trigger.append("on")
                                        else:
                                            send_update = True
                                            if old_peace:
                                                try:
                                                    if (
                                                        abs(
                                                            (
                                                                new_dt
                                                                - datetime.fromisoformat(
                                                                    old_peace.replace("Z", "+00:00")
                                                                )
                                                            ).total_seconds()
                                                        )
                                                        < 60
                                                    ):
                                                        send_update = False
                                                except Exception:
                                                    pass
                                            if send_update:
                                                msgs_trigger.append("mod")

                                    if was_protected and not is_protected:
                                        if not new_peace:
                                            msgs_trigger.append("off")
                                        else:
                                            msgs_trigger.append("end")

                                    if msgs_trigger:
                                        ts = int(new_dt.timestamp()) if new_dt else 0
                                        for trigger in msgs_trigger:
                                            embeds_locales = {}
                                            for lg in ["fr", "de", "en"]:
                                                if trigger == "on":
                                                    embed = discord.Embed(
                                                        title=t(
                                                            lg,
                                                            "rad_spy_p_dove_on_title",
                                                            defaut=f"{e_dove} ALERTE COLOMBE : ACTIVÉE",
                                                        ),
                                                        description=t(
                                                            lg,
                                                            "rad_spy_p_dove_on_desc",
                                                            j=player,
                                                            ts=ts,
                                                            defaut=f"**{player}** est sous protection !\n{e_time} Fin : <t:{ts}:f> (<t:{ts}:R>)",
                                                        ),
                                                        color=discord.Color.light_grey(),
                                                    )
                                                elif trigger == "mod":
                                                    embed = discord.Embed(
                                                        title=t(
                                                            lg,
                                                            "rad_spy_p_dove_mod_title",
                                                            defaut=f"{e_refresh} ALERTE COLOMBE : MODIFIÉE",
                                                        ),
                                                        description=t(
                                                            lg,
                                                            "rad_spy_p_dove_mod_desc",
                                                            j=player,
                                                            ts=ts,
                                                            defaut=f"**{player}** a modifié sa protection !\n{e_time} Fin : <t:{ts}:f> (<t:{ts}:R>)",
                                                        ),
                                                        color=discord.Color.blue(),
                                                    )
                                                elif trigger == "off":
                                                    embed = discord.Embed(
                                                        title=t(
                                                            lg,
                                                            "rad_spy_p_dove_off_title",
                                                            defaut=f"{e_swords} CONFIRMATION : SANS COLOMBE",
                                                        ),
                                                        description=t(
                                                            lg,
                                                            "rad_spy_p_dove_off_desc",
                                                            j=player,
                                                            defaut=f"La protection de **{player}** a expiré ou a été annulée. Il est vulnérable !",
                                                        ),
                                                        color=discord.Color.brand_green(),
                                                    )
                                                elif trigger == "end":
                                                    embed = discord.Embed(
                                                        title=t(
                                                            lg,
                                                            "rad_spy_p_dove_end_title",
                                                            defaut=f"{e_swords} ALERTE COLOMBE : TERMINÉE",
                                                        ),
                                                        description=t(
                                                            lg,
                                                            "rad_spy_p_dove_off_desc",
                                                            j=player,
                                                            defaut=f"La protection de **{player}** a expiré ou a été annulée. Il est vulnérable !",
                                                        ),
                                                        color=discord.Color.brand_green(),
                                                    )
                                                embeds_locales[lg] = embed

                                            await self.envoyer_alerte_privee(
                                                abonnes,
                                                "colombe",
                                                embeds_locales,
                                                users_lang,
                                                target_name=player,
                                                target_id=str(p_id),
                                                gge_server=serveur,
                                            )
                                            await self.envoyer_alerte_serveur(guild_abonnes, "colombe", embeds_locales)

                                    if (new_peace != old_peace) or (was_protected != is_protected):
                                        info["peace_disabled_at"], info["is_protected"] = (new_peace, is_protected)
                                        changes_detected = True
                    except Exception as e:
                        logger.error(f"❌ [Radar Spy] Erreur Bulk Players : {e}")

                try:
                    url_movements = (
                        "https://api.gge-tracker.com/api/v1/server/movements?page=1&castleType=1&movementType=3"
                    )
                    async with session.get(url_movements, headers=headers, timeout=5) as r:
                        if r.status == 200:
                            data_mouv = await r.json()
                            movements = data_mouv.get("movements", [])

                            for m in movements:
                                m_name = m.get("player_name")

                                for p_id, info in tracked_players:
                                    if info["name"] == m_name and m["created_at"] > info["last_pos"]:
                                        abonnes = info.get("abonnes", {})
                                        guild_abonnes = info.get("guild_abonnes", {})

                                        if not abonnes and not guild_abonnes:
                                            continue

                                        x_old, y_old, x_new, y_new = (
                                            m.get("position_x_old"),
                                            m.get("position_y_old"),
                                            m.get("position_x_new"),
                                            m.get("position_y_new"),
                                        )

                                        embeds_locales = {}
                                        for lg in ["fr", "de", "en"]:
                                            embed = discord.Embed(
                                                title=t(
                                                    lg, "rad_spy_p_pos_title", defaut=f"{e_warning} ALERTE DÉMÉNAGEMENT"
                                                ),
                                                color=discord.Color.dark_purple(),
                                            )
                                            embed.add_field(
                                                name="Cible",
                                                value=t(
                                                    lg,
                                                    "rad_spy_p_pos_f",
                                                    j=m_name,
                                                    xo=x_old,
                                                    yo=y_old,
                                                    xn=x_new,
                                                    yn=y_new,
                                                    time=get_discord_timestamp(m["created_at"], "R", lg),
                                                    defaut=f"**{m_name}**\n`{x_old}:{y_old}` ➔ `{x_new}:{y_new}`\n{e_time} *Fait {get_discord_timestamp(m['created_at'], 'R', lg)}*",
                                                ),
                                            )
                                            embeds_locales[lg] = embed

                                        await self.envoyer_alerte_privee(
                                            abonnes,
                                            "position",
                                            embeds_locales,
                                            users_lang,
                                            target_name=info.get("name", ""),
                                            target_id=str(p_id),
                                            gge_server=serveur,
                                        )
                                        await self.envoyer_alerte_serveur(guild_abonnes, "position", embeds_locales)
                                        info["last_pos"] = m["created_at"]
                                        changes_detected = True
                except Exception as e:
                    logger.error(f"❌ [Radar Spy] Erreur Global Movements : {e}")

            if self.users_to_purge:
                for uid in self.users_to_purge:
                    for p_id in list(data.get("players", {}).keys()):
                        if uid in data["players"][p_id].get("abonnes", {}):
                            del data["players"][p_id]["abonnes"][uid]
                            if not data["players"][p_id].get("abonnes", {}) and not data["players"][p_id].get(
                                "guild_abonnes", {}
                            ):
                                del data["players"][p_id]

                    for a_id in list(data.get("alliances", {}).keys()):
                        if uid in data["alliances"][a_id].get("abonnes", {}):
                            del data["alliances"][a_id]["abonnes"][uid]
                            if not data["alliances"][a_id].get("abonnes", {}) and not data["alliances"][a_id].get(
                                "guild_abonnes", {}
                            ):
                                del data["alliances"][a_id]

                logger.info(f"🧹 Purge MP terminée pour {len(self.users_to_purge)} compte(s).")
                self.users_to_purge.clear()
                changes_detected = True

            if self.guilds_to_purge:
                for gid in self.guilds_to_purge:
                    if gid in data.get("guild_configs", {}):
                        del data["guild_configs"][gid]

                    for p_id in list(data.get("players", {}).keys()):
                        if gid in data["players"][p_id].get("guild_abonnes", {}):
                            del data["players"][p_id]["guild_abonnes"][gid]
                            if not data["players"][p_id].get("abonnes", {}) and not data["players"][p_id].get(
                                "guild_abonnes", {}
                            ):
                                del data["players"][p_id]

                    for a_id in list(data.get("alliances", {}).keys()):
                        if gid in data["alliances"][a_id].get("guild_abonnes", {}):
                            del data["alliances"][a_id]["guild_abonnes"][gid]
                            if not data["alliances"][a_id].get("abonnes", {}) and not data["alliances"][a_id].get(
                                "guild_abonnes", {}
                            ):
                                del data["alliances"][a_id]

                logger.info(f"🧹 Purge Guilds terminée pour {len(self.guilds_to_purge)} serveur(s).")
                self.guilds_to_purge.clear()
                changes_detected = True

            if changes_detected:
                await save_surveillance_async(data)

        except Exception as e:
            logger.error(f"❌ [RADAR CRASH] : {traceback.format_exc()}")
            obs.record_error(source="task", scope="radar_spy_task", exception=e, cog="radar", severity="critical")
