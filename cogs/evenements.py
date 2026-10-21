import json
import logging
import os
import urllib.parse
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

import observability as obs
from utils import (
    CONFIG_DIR,
    TRACKER_EVENTS,
    PaginationView,
    alliance_autocomplete,
    event_alliance_autocomplete,
    event_autocomplete,
    format_num,
    generer_rapport_alliance_embed,
    get_api_headers,
    get_cached_data,
    get_discord_timestamp,
    get_server_config,
    joueur_autocomplete,
    load_configuration_async,
    load_pseudos_async,
    load_rivals_async,
    prompt_vote_if_lucky,
    save_pseudos_async,
    save_rivals_async,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")

EVENT_TRAD_KEYS = {
    "Nomad Invasion": ("cal_ev_nomad", "Nomade"),
    "Samurai Invasion": ("cal_ev_samurai", "Samouraï"),
    "Bloodcrow Invasion": ("cal_ev_bloodcrow", "Corbeaux de Sang"),
    "War of the Realms": ("cal_ev_realms", "Guerre des Royaumes"),
    "Battle of Berimond": ("cal_ev_berimond", "Bérimond"),
}


def get_ev_name(event_en, langue):
    if event_en in EVENT_TRAD_KEYS:
        key, defaut = EVENT_TRAD_KEYS[event_en]
        return t(langue, key, defaut=defaut)
    return event_en


def _get_api_timestamp(*sources):
    dates_trouvees = []

    def search_ts(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in [
                    "updated_at",
                    "updatedAt",
                    "last_update",
                    "date",
                    "collected_at",
                    "last_collected_at",
                ] and isinstance(v, str):
                    if len(v) >= 10 and v[4] == "-":
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
            return datetime.fromisoformat(latest_str.replace("Z", "+00:00"))
        except:
            pass

    return discord.utils.utcnow()


class EvenementsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.clr_joueur_dernier = discord.Color.from_rgb(192, 214, 228)
        self.clr_joueur_cumul = discord.Color.from_rgb(172, 192, 205)
        self.clr_alliance = discord.Color.from_rgb(214, 228, 192)
        self.clr_rival_list = discord.Color.from_rgb(228, 206, 192)

    async def cog_load(self):
        if not self.rival_check_task.is_running():
            self.rival_check_task.start()

    async def cog_unload(self):
        self.rival_check_task.cancel()

    @app_commands.command(name="event_player", description="View a player's latest score or history")
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
    @app_commands.autocomplete(event_name=event_autocomplete)
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Latest score", value="latest"),
            app_commands.Choice(name="History", value="history"),
        ]
    )
    async def event_player(
        self, interaction: discord.Interaction, event_name: str, player: str, mode: app_commands.Choice[str]
    ):
        await interaction.followup.defer(
            thinking=True
        ) if interaction.response.is_done() else await interaction.response.defer(thinking=True)

        langue, serveur = await get_server_config(interaction)
        event_trad = get_ev_name(event_name, langue)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")

        player_id = None
        alliance_name = "Sans alliance"
        cache = await get_cached_data(serveur)
        local_data = cache.get("players_data", {})

        for p_name, p_info in local_data.items():
            if p_name.lower() == player.lower():
                player_id = str(p_info.get("player_id", p_info.get("id", "")))
                player = p_name
                alli_raw = p_info.get("allianceName", p_info.get("alliance_id", "Sans alliance"))
                if isinstance(alli_raw, dict):
                    alli_raw = alli_raw.get("name", "Sans alliance")
                if alli_raw and alli_raw not in ["", "None"]:
                    alliance_name = alli_raw
                break

        if not player_id:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_err_player_local",
                    player=player,
                    defaut=f"{{e_error}} Joueur **{player}** introuvable dans le cache local.",
                )
            )

        headers = await get_api_headers(interaction)
        base_api = "https://api.gge-tracker.com/api/v1"

        event_keys = TRACKER_EVENTS.get(event_name)
        if not event_keys:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_err_unsupported",
                    defaut="{e_error} Événement inconnu ou non géré par l'API.",
                )
            )

        try:
            async with self.bot.session.get(
                f"{base_api}/statistics/player/{player_id}", headers=headers, timeout=10
            ) as resp:
                if resp.status != 200:
                    return await interaction.followup.send(
                        t(
                            langue,
                            "ev_err_api_player",
                            player=player,
                            defaut=f"{{e_error}} Erreur API GGE-Tracker pour {player}.",
                        )
                    )
                stats_data = await resp.json()
        except Exception as e:
            obs.record_error(source="command", scope="event_player", exception=e, cog="events")
            return await interaction.followup.send(
                t(
                    langue,
                    "utils_err_api_connection",
                    defaut="{e_error} Impossible de se connecter à l'API.",
                )
            )

        actualisation_dt = _get_api_timestamp(stats_data)
        merged_history = []
        for key in event_keys:
            merged_history.extend(stats_data.get("points", {}).get(key, []))

        if not merged_history:
            return await interaction.followup.send(
                t(
                    langue,
                    "utils_err_no_points",
                    alliance=player,
                    nom_event=event_trad,
                    defaut=f"{{e_error}} Aucun point enregistré pour **{player}** sur **{event_trad}**.",
                )
            )

        merged_history.sort(key=lambda x: x.get("date", ""))
        alliance_name = stats_data.get("alliance_name") or "Sans alliance"

        sessions, current_session = [], []

        for entry in merged_history:
            d_str = entry.get("date")
            pt = int(entry.get("point", 0))
            if not d_str:
                continue
            try:
                dt = datetime.fromisoformat(d_str.replace("Z", "+00:00"))
                if not current_session:
                    current_session.append((dt, pt))
                else:
                    last_dt = current_session[-1][0]
                    last_pt = current_session[-1][1]
                    if pt < last_pt or (dt - last_dt).days > 3:
                        sessions.append(current_session)
                        current_session = [(dt, pt)]
                    else:
                        current_session.append((dt, pt))
            except:
                pass
        if current_session:
            sessions.append(current_session)

        if mode.value == "history":
            nb_events = 30
            events_joues = len(sessions)
            avertissement = ""
            if events_joues < nb_events:
                avertissement = t(
                    langue,
                    "ev_warn_missing_data",
                    nb=nb_events,
                    acts=events_joues,
                    defaut=f"\n\n{{e_error}} **Manque de données** : Cumul sur {nb_events} events, mais seulement **{events_joues}** ont été joués/enregistrés.",
                )
                nb_events = events_joues

            recent_sessions = sessions[-nb_events:] if nb_events > 0 else []
            if not recent_sessions:
                return await interaction.followup.send(
                    t(
                        langue,
                        "ev_err_no_usable_hist",
                        defaut="{e_error} Aucun historique exploitable pour calculer un cumul.",
                    )
                )

            scores, lignes_details = [], []
            for i, s in enumerate(reversed(recent_sessions)):
                max_score = max(s, key=lambda x: x[1])[1]
                scores.append(max_score)
                start_d = s[0][0].strftime("%d/%m/%Y")
                lignes_details.append(
                    t(
                        langue,
                        "ev_session_line",
                        idx=i + 1,
                        d=start_d,
                        s=format_num(max_score),
                        defaut=f"🔹 **Event -{i + 1}** ({start_d}) : **{format_num(max_score)}** pts",
                    )
                )

            total_score = sum(scores)
            moyenne = total_score // len(scores)
            sorted_scores = sorted(scores)
            n = len(sorted_scores)
            mediane = (sorted_scores[n // 2 - 1] + sorted_scores[n // 2]) // 2 if n % 2 == 0 else sorted_scores[n // 2]
            pire_score, meilleur_score = sorted_scores[0], sorted_scores[-1]

            start_date_global = recent_sessions[0][0][0].strftime("%d/%m/%Y")
            end_date_global = recent_sessions[-1][-1][0].strftime("%d/%m/%Y")

            stats_txt = t(
                langue,
                "ev_cumul_stats_text",
                t=format_num(total_score),
                m=format_num(moyenne),
                med=format_num(mediane),
                b=format_num(meilleur_score),
                w=format_num(pire_score),
                defaut=(
                    f"> {{e_podium}} **TOTAL CUMULÉ : {format_num(total_score)} pts**\n"
                    f"> {{e_ranking}} **Moyenne/Event** : {format_num(moyenne)} pts\n"
                    f"> ⚖️ **Médiane** : {format_num(mediane)} pts\n"
                    f"> 🚀 **Meilleur Score** : {format_num(meilleur_score)} pts\n"
                    f"> 📉 **Pire Score** : {format_num(pire_score)} pts"
                ),
            )

            embeds = []
            chunk_size = 10
            nb_pages = max(1, (len(lignes_details) - 1) // chunk_size + 1)

            for i in range(0, len(lignes_details), chunk_size):
                chunk = lignes_details[i : i + chunk_size]
                page_actuelle = (i // chunk_size) + 1

                embed = discord.Embed(
                    title=t(
                        langue,
                        "ev_cumul_title",
                        player=player,
                        event=event_trad,
                        defaut=f"{{e_stats}} Analyse & Historique de {player} pour : {event_trad}",
                    ),
                    color=self.clr_joueur_cumul,
                )
                desc_i18n = t(
                    langue,
                    "ev_cumul_desc",
                    a=alliance_name,
                    w=avertissement,
                    defaut=f"{{e_alliance_icon}} **Alliance actuelle :** [{alliance_name}]{avertissement}",
                )
                embed.description = f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{desc_i18n}"

                f_title1 = t(
                    langue,
                    "ev_cumul_field_stats",
                    n=len(recent_sessions),
                    start=start_date_global,
                    end=end_date_global,
                    defaut=f"{{e_stats}} Bilan sur les {len(recent_sessions)} derniers events\n*(Période du {start_date_global} au {end_date_global})*",
                )
                embed.add_field(name=f_title1, value=stats_txt, inline=False)

                field_value = ""
                for ligne in chunk:
                    if len(field_value) + len(ligne) + 1 > 1000:
                        break
                    field_value += ligne + "\n"

                f_title2 = t(
                    langue,
                    "ev_session_details_page",
                    curr=page_actuelle,
                    tot=nb_pages,
                    defaut=f"Détails des sessions (Page {page_actuelle}/{nb_pages})",
                )
                embed.add_field(name=f_title2, value=field_value.strip(), inline=False)

                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

            if len(embeds) == 1:
                await interaction.followup.send(embed=embeds[0])
            else:
                view = PaginationView(embeds)
                view.message = await interaction.followup.send(embed=embeds[0], view=view, wait=True)
            await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)

        else:
            latest_point, latest_date = 0, ""
            for entry in merged_history:
                d_str = entry.get("date", "")
                pt = int(entry.get("point", 0))
                if d_str > latest_date:
                    latest_date = d_str
                    latest_point = pt

            if latest_point == 0:
                return await interaction.followup.send(
                    t(
                        langue,
                        "ev_err_zero_pt",
                        player=player,
                        ev=event_trad,
                        defaut=f"{{e_error}} **{player}** est actuellement à 0 pt sur **{event_trad}**.",
                    )
                )

            embed = discord.Embed(
                title=t(
                    langue,
                    "ev_live_title",
                    player=player,
                    ev=event_trad,
                    defaut=f"{{e_podium}} Score en direct de {player} pour : {event_trad}",
                ),
                color=self.clr_joueur_dernier,
            )
            embed.description = (
                f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)"
            )

            embed.add_field(
                name=t(langue, "ev_prof_title", defaut="{e_players} Profil"),
                value=t(
                    langue,
                    "ev_prof_desc",
                    p=player,
                    a=alliance_name,
                    defaut=f"**Joueur :** {player}\n**Alliance :** [{alliance_name}]",
                ),
                inline=True,
            )
            embed.add_field(
                name=t(langue, "ev_live_score_title", defaut="{e_icon_points} Score Actuel"),
                value=f"**{format_num(latest_point)} pts**",
                inline=True,
            )

            if latest_date:
                ts_r = get_discord_timestamp(latest_date, "R", langue)
                ts_t = get_discord_timestamp(latest_date, "t", langue)
                embed.add_field(
                    name=t(langue, "ev_live_last_scan_title", defaut="⏱️ Dernier relevé"),
                    value=t(
                        langue, "ev_aqua_last_hit_desc", r=ts_r, t=ts_t, defaut=f"Relevé par l'API {ts_r} (*{ts_t}*)"
                    ),
                    inline=False,
                )

            await setup_embed_footer(embed, interaction, langue)
            await interaction.followup.send(embed=embed)
            await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)

    @app_commands.command(name="event_alliance", description="Ranking and participation of an alliance in an event")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.autocomplete(event_name=event_alliance_autocomplete)
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    @app_commands.choices(
        display_mode=[
            app_commands.Choice(name="📑 Interactive pages (Buttons)", value="pages"),
            app_commands.Choice(name="📜 Full list (Single message)", value="list"),
        ]
    )
    async def event_alliance(
        self, interaction: discord.Interaction, event_name: str, alliance_name: str, display_mode: str = "list"
    ):
        await interaction.followup.defer(
            thinking=True
        ) if interaction.response.is_done() else await interaction.response.defer(thinking=True)

        langue, serveur = await get_server_config(interaction)
        event_trad = get_ev_name(event_name, langue)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")

        event_keys = TRACKER_EVENTS.get(event_name)
        if not event_keys:
            return await interaction.followup.send(t(langue, "ev_err_unknown", defaut="{e_error} Événement inconnu."))

        try:
            embed, error_or_lignes, stats_text, global_latest_str = await generer_rapport_alliance_embed(
                self.bot,
                event_trad,
                event_keys,
                alliance_name,
                self.clr_alliance,
                interaction=interaction,
                custom_server=serveur,
            )
        except Exception as e:
            obs.record_error(source="command", scope="event_alliance", exception=e, cog="events")
            return await interaction.followup.send(f"{{e_error}} Erreur interne : {e}")

        if not embed:
            return await interaction.followup.send(f"{{e_error}} {error_or_lignes}")

        if display_mode == "list":
            await setup_embed_footer(embed, interaction, langue)
            await interaction.followup.send(embed=embed)
        else:
            if global_latest_str:
                try:
                    actualisation_dt = datetime.fromisoformat(global_latest_str.replace("Z", "+00:00"))
                except:
                    actualisation_dt = discord.utils.utcnow()
            else:
                actualisation_dt = discord.utils.utcnow()

            embeds = []
            chunk_size = 15
            nb_pages = max(1, (len(error_or_lignes) - 1) // chunk_size + 1)
            for i in range(0, len(error_or_lignes), chunk_size):
                chunk = error_or_lignes[i : i + chunk_size]
                emb = discord.Embed(
                    title=t(
                        langue,
                        "ev_alli_title",
                        a=alliance_name,
                        ev=event_trad,
                        defaut=f"{{e_alliance_icon}} {alliance_name} - {event_trad}",
                    ),
                    color=self.clr_alliance,
                )

                emb.description = (
                    f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)"
                )

                emb.add_field(
                    name=t(langue, "utils_embed_stats_title", defaut="{e_stats} Statistiques"),
                    value=stats_text,
                    inline=False,
                )

                f_title = t(
                    langue,
                    "ev_alli_page_title",
                    i=(i // chunk_size) + 1,
                    n=nb_pages,
                    defaut=f"{{e_ranking}} Classement (Page {i // chunk_size + 1}/{nb_pages})",
                )
                emb.add_field(name=f_title, value="\n".join(chunk), inline=False)

                await setup_embed_footer(emb, interaction, langue)
                embeds.append(emb)
            view = PaginationView(embeds)
            view.message = await interaction.followup.send(embed=embeds[0], view=view, wait=True)
        await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)

    @app_commands.command(name="link_account", description="Link your Discord account to your GGE username")
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def link_account(self, interaction: discord.Interaction, player: str):
        data = await load_pseudos_async()
        data[str(interaction.user.id)] = player
        await save_pseudos_async(data)
        langue, _ = await get_server_config(interaction)
        msg = t(
            langue,
            "ev_pseudo_linked",
            pseudo=player,
            defaut=f"{{e_players}} Compte lié à **{player}** !",
        )
        await interaction.response.send_message(msg, ephemeral=True)

    rival_group = app_commands.Group(
        name="rival",
        description="Competition Radar (MP only)",
        allowed_contexts=app_commands.AppCommandContext(guild=False, dm_channel=True, private_channel=True),
    )

    @rival_group.command(name="start", description="Turn on your competition radar")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.autocomplete(event_name=event_autocomplete)
    async def rival_start(self, interaction: discord.Interaction, event_name: str, threshold: int = 90):
        langue, serveur = await get_server_config(interaction)
        if interaction.guild:
            return await interaction.response.send_message(
                t(langue, "ev_err_dm_only", defaut="{e_error} À faire en **Message Privé**."),
                ephemeral=True,
            )
        pseudos = await load_pseudos_async()
        if str(interaction.user.id) not in pseudos:
            return await interaction.response.send_message(
                t(langue, "ev_err_need_pseudo", defaut="{e_error} Fais `/link_account` d'abord.")
            )

        data = await load_rivals_async()
        data[str(interaction.user.id)] = {
            "event": event_name,
            "seuil": max(50, min(99, threshold)),
            "rivaux": [],
            "last_known_scores": {},
            "started_at": discord.utils.utcnow().isoformat(),
            "serveur": serveur,
        }
        await save_rivals_async(data)

        event_trad = get_ev_name(event_name, langue)
        await interaction.response.send_message(
            t(
                langue,
                "ev_rival_started",
                event=event_trad,
                defaut=f"{{e_icon_analyze}} Radar activé pour **{event_trad}** !",
            )
        )
        await prompt_vote_if_lucky(interaction, probability_percent=5, langue=langue)

    @rival_group.command(name="add", description="Add rivals (Max 10)")
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
    async def rival_add(
        self,
        interaction: discord.Interaction,
        player1: str,
        player2: str = None,
        player3: str = None,
        player4: str = None,
        player5: str = None,
    ):
        langue, _ = await get_server_config(interaction)
        if interaction.guild:
            return await interaction.response.send_message(
                t(langue, "ev_err_dm_only", defaut="{e_error} En privé uniquement."), ephemeral=True
            )
        data = await load_rivals_async()
        uid = str(interaction.user.id)
        if uid not in data:
            return await interaction.response.send_message(
                t(langue, "ev_err_need_rival_start", defaut="{e_error} Fais `/rival start` d'abord.")
            )
        for j in [player1, player2, player3, player4, player5]:
            if j and j not in data[uid]["rivaux"] and len(data[uid]["rivaux"]) < 10:
                data[uid]["rivaux"].append(j)
        await save_rivals_async(data)
        await interaction.response.send_message(
            t(langue, "ev_rival_added", defaut="{e_icon_search} Rivaux mis à jour !")
        )

    @rival_group.command(name="list", description="Show your rivals")
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
    async def rival_list(self, interaction: discord.Interaction):
        langue, _ = await get_server_config(interaction)
        if interaction.guild:
            return await interaction.response.send_message(
                t(langue, "ev_err_dm_only", defaut="{e_error} En privé uniquement."), ephemeral=True
            )
        data = await load_rivals_async()
        if str(interaction.user.id) not in data:
            return await interaction.response.send_message(t(langue, "ev_rival_inactive", defaut="🕸️ Radar inactif."))
        config = data[str(interaction.user.id)]

        event_trad = get_ev_name(config["event"], langue)
        title = t(
            langue,
            "ev_rival_list_title",
            event=event_trad,
            defaut=f"{{e_icon_name}} Radar Actif : {event_trad}",
        )
        desc = t(
            langue, "ev_rival_list_desc", s=config["seuil"], defaut=f"**Seuil :** {config['seuil']}%\n"
        ) + "\n".join([f"🔸 {r}" for r in config["rivaux"]])

        embed = discord.Embed(title=title, description=desc, color=self.clr_rival_list)
        await setup_embed_footer(embed, interaction, langue)
        await interaction.response.send_message(embed=embed)
        await prompt_vote_if_lucky(interaction, probability_percent=5, langue=langue)

    @rival_group.command(name="stop", description="Turn off the radar")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    async def rival_stop(self, interaction: discord.Interaction):
        langue, _ = await get_server_config(interaction)
        data = await load_rivals_async()
        if str(interaction.user.id) in data:
            del data[str(interaction.user.id)]
            await save_rivals_async(data)
            await interaction.response.send_message(t(langue, "ev_rival_stopped", defaut="🛑 Radar désactivé."))

    @tasks.loop(minutes=1)
    async def rival_check_task(self):
        obs.set_task_name("rival_check_task")
        try:
            maintenant = discord.utils.utcnow()

            config_data = await load_configuration_async()
            servers_info = config_data.get("servers_info", {})

            data = await load_rivals_async()
            if not data:
                return

            path_users = CONFIG_DIR / "users.json"
            users_lang = {}
            if os.path.exists(path_users):
                try:
                    with open(path_users, encoding="utf-8") as f:
                        users_data = json.load(f)
                        for uid, info in users_data.items():
                            users_lang[uid] = info.get("langue", "fr")
                except:
                    pass

            changes_detected = False
            session = self.bot.session
            base_api = "https://api.gge-tracker.com/api/v1"

            for user_id, config in list(data.items()):
                serveur = config.get("serveur", "E4K_FR1").upper()

                srv_data = servers_info.get(serveur, {})
                minute_cible = srv_data.get("scan_minutes")

                if minute_cible is None:
                    minute_cible = 46

                minutes_valides = [
                    minute_cible,
                    (minute_cible + 5) % 60,
                    (minute_cible + 10) % 60,
                    (minute_cible + 15) % 60,
                ]

                if maintenant.minute not in minutes_valides:
                    continue

                langue = users_lang.get(user_id, "fr")
                headers = await get_api_headers(custom_server=serveur)
                event_name = config.get("event")
                threshold = config.get("seuil", 90)
                rivaux = config.get("rivaux", [])
                last_scores = config.get("last_known_scores", {})

                event_keys = TRACKER_EVENTS.get(event_name)
                if not event_keys or not rivaux:
                    continue

                pseudos = await load_pseudos_async()
                mon_pseudo = pseudos.get(user_id)
                if not mon_pseudo:
                    continue

                async def get_score(pseudo):
                    try:
                        async with session.get(
                            f"{base_api}/players/{urllib.parse.quote(pseudo)}", headers=headers, timeout=5
                        ) as r:
                            if r.status != 200:
                                return 0
                            p_data = await r.json()
                            if isinstance(p_data, list) and p_data:
                                p_data = p_data[0]
                            p_id = p_data.get("player_id", p_data.get("id"))
                            if not p_id:
                                return 0

                        async with session.get(f"{base_api}/statistics/player/{p_id}", headers=headers, timeout=5) as r:
                            if r.status != 200:
                                return 0
                            stats = await r.json()
                            merged = []
                            for key in event_keys:
                                merged.extend(stats.get("points", {}).get(key, []))
                            if not merged:
                                return 0
                            merged.sort(key=lambda x: x.get("date", ""))
                            return int(merged[-1].get("point", 0))
                    except:
                        return 0

                mon_score = await get_score(mon_pseudo)

                for rival in rivaux:
                    score_rival = await get_score(rival)
                    if score_rival == 0:
                        continue

                    old_score_rival = last_scores.get(rival, 0)

                    if score_rival > old_score_rival:
                        pourcentage = (score_rival / mon_score * 100) if mon_score > 0 else 999

                        if pourcentage >= threshold:
                            diff = score_rival - mon_score

                            embeds_locales = {}
                            for lg in ["fr", "de", "en"]:
                                event_trad_alert = get_ev_name(event_name, lg)
                                if diff > 0:
                                    desc = t(
                                        lg,
                                        "ev_rival_alert_overtake",
                                        r=rival,
                                        diff=format_num(diff),
                                        s=format_num(score_rival),
                                        ms=format_num(mon_score),
                                        defaut=f"{{e_warning}} **DANGER !**\n**{rival}** vient de te dépasser avec **{format_num(diff)}** points d'avance !\n\nLui : {format_num(score_rival)} pts\nToi : {format_num(mon_score)} pts",
                                    )
                                else:
                                    desc = t(
                                        lg,
                                        "ev_rival_alert_danger",
                                        r=rival,
                                        pct=f"{pourcentage:.1f}",
                                        s=format_num(score_rival),
                                        ms=format_num(mon_score),
                                        defaut=f"{{e_warning}} **ATTENTION !**\n**{rival}** se rapproche dangereusement de ton score ({pourcentage:.1f}%).\n\nLui : {format_num(score_rival)} pts\nToi : {format_num(mon_score)} pts",
                                    )

                                emb = discord.Embed(
                                    title=t(
                                        lg,
                                        "ev_rival_alert_title",
                                        ev=event_trad_alert,
                                        defaut=f"🎯 Radar Rival : {event_trad_alert}",
                                    ),
                                    description=desc,
                                    color=discord.Color.red(),
                                )
                                await setup_embed_footer(emb, None, langue=lg)
                                embeds_locales[lg] = emb

                            try:
                                user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                                await user.send(embed=embeds_locales.get(langue, embeds_locales["fr"]))
                                obs.record_alert(
                                    source="rival",
                                    alert_type="overtake" if diff > 0 else "danger",
                                    gge_server=serveur,
                                    channel="dm",
                                    recipients=1,
                                    delivered=1,
                                    failed=0,
                                    dm_blocked=0,
                                )
                            except discord.Forbidden:
                                obs.record_alert(
                                    source="rival",
                                    alert_type="overtake" if diff > 0 else "danger",
                                    gge_server=serveur,
                                    channel="dm",
                                    recipients=1,
                                    delivered=0,
                                    failed=1,
                                    dm_blocked=1,
                                )
                            except Exception as e:
                                logger.error(f"❌ Erreur envoi MP Rival: {e}")
                                obs.record_alert(
                                    source="rival",
                                    alert_type="overtake" if diff > 0 else "danger",
                                    gge_server=serveur,
                                    channel="dm",
                                    recipients=1,
                                    delivered=0,
                                    failed=1,
                                    dm_blocked=0,
                                )

                    last_scores[rival] = score_rival
                    changes_detected = True

                config["last_known_scores"] = last_scores

            if changes_detected:
                await save_rivals_async(data)

        except Exception as e:
            logger.error(f"❌ [RIVAL TASK CRASH] : {e}")
            obs.record_error(source="task", scope="rival_check_task", exception=e, cog="events", severity="critical")


async def setup(bot: commands.Bot):
    await bot.add_cog(EvenementsCog(bot))
