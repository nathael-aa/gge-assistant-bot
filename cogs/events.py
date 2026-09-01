import asyncio
import json
import logging
import os
import urllib.parse
from datetime import datetime

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

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
radar_logger = logging.getLogger("Radar_Log")
radar_logger.setLevel(logging.INFO)

EVENT_TRAD_KEYS = {
    "Nomad Invasion": ("cal_ev_nomad", "Nomade"),
    "Samurai Invasion": ("cal_ev_samurai", "Samouraï"),
    "Bloodcrow Invasion": ("cal_ev_bloodcrow", "Corbeaux de Sang"),
    "War of the Realms": ("cal_ev_realms", "Guerre des Royaumes"),
    "Storm Islands": ("cal_ev_storm", "Îles Orageuses"),
    "Aquamarine": ("cal_ev_storm", "Îles Orageuses"),
    "Battle of Berimond": ("cal_ev_berimond", "Bérimond"),
}


def get_ev_name(event_en, langue):
    """Traduit le nom de l'événement à la volée pour l'affichage"""
    if event_en in EVENT_TRAD_KEYS:
        key, defaut = EVENT_TRAD_KEYS[event_en]
        return t(langue, key, defaut=defaut)
    return event_en


def get_tier_and_label(lvl, leg, langue="fr"):
    """Détermine la tranche de niveau du joueur (avec traduction)"""
    if leg < 300 and lvl <= 70:
        return "T1", t(langue, "ev_tier_1", defaut="Niv 1-299")
    if leg < 650:
        return "T2", t(langue, "ev_tier_2", defaut="Niv 300-649")
    if leg < 950:
        return "T3", t(langue, "ev_tier_3", defaut="Niv 650-949")
    return "T4", t(langue, "ev_tier_4", defaut="Niv 950+")


def get_month_name(month_num_str, langue="fr"):
    return t(langue, f"month_{month_num_str}", defaut=month_num_str)


def log_rival_event(user_id, rival_name, event_type, message):
    """📝 Enregistre une trace sécurisée dans le log radar."""
    pass


def _get_api_timestamp(*sources):
    """
    Explore de manière récursive et profonde les structures de données renvoyées par l'API.
    Traque TOUTES les dates trouvées pour s'assurer d'extraire la plus récente.
    """
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


class AquamarineSelectView(discord.ui.View):
    def __init__(
        self,
        interaction,
        player,
        alliance_name,
        actualisation_dt,
        langue,
        clr,
        sorted_months,
        monthly_finals,
        gt_cargo,
        gt_am,
    ):
        super().__init__(timeout=3600)
        self.interaction = interaction
        self.player = player
        self.alliance_name = alliance_name
        self.actualisation_dt = actualisation_dt
        self.langue = langue
        self.clr = clr
        self.sorted_months = sorted_months
        self.monthly_finals = monthly_finals
        self.gt_cargo = gt_cargo
        self.gt_am = gt_am
        self.message = None

        # --- Création des options du menu déroulant ---
        options = []
        for i, m_key in enumerate(sorted_months[:25]):
            annee, mois_num = m_key.split("-")
            nom_mois = f"{get_month_name(mois_num, langue)} {annee}"
            # Emoji calendrier natif pour la sécurité du select
            options.append(discord.SelectOption(label=nom_mois, value=m_key, default=(i == 0), emoji="📅"))

        placeholder_txt = t(langue, "ev_aqua_select_placeholder", defaut="Sélectionnez un mois...")
        self.select = discord.ui.Select(placeholder=placeholder_txt, options=options)
        self.select.callback = self.on_select
        self.add_item(self.select)

        self.current_month = sorted_months[0]

    async def on_select(self, interaction: discord.Interaction):
        self.current_month = self.select.values[0]

        for opt in self.select.options:
            opt.default = opt.value == self.current_month

        # Sécurité pour le timeout du message
        if not self.message:
            self.message = interaction.message

        embed = await self.generate_embed(self.current_month)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if hasattr(self, "message") and self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def generate_embed(self, month_key):
        annee, mois_num = month_key.split("-")
        nom_mois = f"{get_month_name(mois_num, self.langue)} {annee}"

        final_sn = self.monthly_finals[month_key]
        m_metrics = final_sn["metrics"]

        gt_c = f"{self.gt_cargo:,}".replace(",", " ")
        gt_a = f"{self.gt_am:,}".replace(",", " ")

        p_f = f"{m_metrics.get(100, 0):,}".replace(",", " ")
        a_f = f"{m_metrics.get(15, 0):,}".replace(",", " ")
        i_f = f"{m_metrics.get(16, 0):,}".replace(",", " ")
        f_f = f"{m_metrics.get(17, 0):,}".replace(",", " ")
        w_f = f"{m_metrics.get(18, 0):,}".replace(",", " ")
        d_f = f"{m_metrics.get(19, 0):,}".replace(",", " ")

        lbl_date = t(self.langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")

        profil_desc = t(
            self.langue,
            "ev_prof_desc",
            p=self.player,
            a=self.alliance_name,
            defaut=f"**Joueur :** {self.player}\n**Alliance :** {self.alliance_name}",
        )

        embed = discord.Embed(
            title=t(
                self.langue,
                "ev_aqua_cumul_title",
                player=self.player,
                defaut=f"{{e_stats}} Historique Îles Orageuses de {self.player}",
            ),
            description=f"{lbl_date} <t:{int(self.actualisation_dt.timestamp())}:F> (<t:{int(self.actualisation_dt.timestamp())}:R>)\n\n{profil_desc}\n",
            color=self.clr,
        )

        stats_globales = t(
            self.langue,
            "ev_aqua_cumul_stats",
            gtc=gt_c,
            gta=gt_a,
            pf=p_f,
            defaut=(
                f"🏆 **Total Historique Cargo :** `{gt_c}` {{e_aquamarinedepenser}}\n"
                f"📈 **Total Historique Aigue-Marine :** `{gt_a}` {{e_aquamarinetotalcollectee}}"
            ),
        )

        if "`" not in stats_globales:
            stats_globales = stats_globales.replace(gt_c, f"`{gt_c}`").replace(gt_a, f"`{gt_a}`")

        embed.add_field(
            name=t(
                self.langue,
                "ev_aqua_cumul_overview",
                defaut="{e_icon_world} **Vue d'ensemble historique**",
            ),
            value=stats_globales,
            inline=False,
        )

        details_txt = t(
            self.langue,
            "ev_aqua_cumul_details",
            pf=p_f,
            af=a_f,
            i_f=i_f,
            f_f=f_f,
            wf=w_f,
            df=d_f,
            defaut=(
                f"**Score Final Cargo :** `{p_f}` {{e_pointscargo}}\n\n"
                f"{{e_aquamarinetotalcollectee}} **Aigue-marine collectées :** `{a_f}`\n"
                f" ↳ {{e_aquamarineiles}} *Dans les îles à ressources :* `{i_f}`\n"
                f" ↳ {{e_aquamarineforts}} *Dans les forts orageux :* `{f_f}`\n"
                f" ↳ {{e_aquamarinegagnerjcj}} *Gagné en JcJ :* `{w_f}`\n\n"
                f"{{e_aquamarinedepenser}} **Dépensé en points cargo :** `{d_f}`"
            ),
        )

        for val in [p_f, a_f, i_f, f_f, w_f, d_f]:
            if val and f"`{val}`" not in details_txt:
                details_txt = details_txt.replace(val, f"`{val}`")

        embed.add_field(
            name=t(
                self.langue, "ev_aqua_cumul_archive", m=nom_mois, defaut=f"**📜 Archives de l'édition ({nom_mois})**"
            ),
            value=details_txt,
            inline=False,
        )

        await setup_embed_footer(embed, self.interaction, self.langue)
        return embed


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.clr_joueur_dernier = discord.Color.from_rgb(192, 214, 228)
        self.clr_joueur_cumul = discord.Color.from_rgb(172, 192, 205)
        self.clr_alliance = discord.Color.from_rgb(214, 228, 192)
        self.clr_rival_list = discord.Color.from_rgb(228, 206, 192)
        self.clr_woa_historique = discord.Color.from_rgb(229, 148, 0)
        self.clr_woa_classement = discord.Color.from_rgb(234, 169, 50)
        self.clr_woa_bilan = discord.Color.from_rgb(240, 194, 111)
        self.clr_aqua = discord.Color.from_rgb(226, 235, 252)

    async def cog_load(self):
        if not self.rival_check_task.is_running():
            self.rival_check_task.start()

    async def cog_unload(self):
        self.rival_check_task.cancel()

    # =========================================
    # COMMANDE : EVENT PLAYER
    # ==========================================
    @app_commands.command(name="event_player", description="View a player's latest score or history")
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

        if (
            "storm islands" in event_name.lower()
            or "aquamarine" in event_name.lower()
            or "orageuses" in event_name.lower()
        ):
            try:
                async with self.bot.session.get(
                    f"{base_api}/aquamarine/player/{player_id}", headers=headers, timeout=10
                ) as resp:
                    if resp.status != 200:
                        return await interaction.followup.send(
                            t(
                                langue,
                                "ev_err_aqua_hist",
                                player=player,
                                defaut=f"{{e_error}} Aucun historique Aquamarine trouvé pour **{player}**.",
                            )
                        )
                    snapshots = (await resp.json()).get("snapshots", [])
            except Exception as e:
                logger.error(f"❌ ERREUR API Aquamarine : {str(e)}")
                return await interaction.followup.send(
                    t(
                        langue,
                        "ev_err_aqua_tech",
                        type=type(e).__name__,
                        defaut=f"{{e_error}} Erreur technique Aquamarine : {type(e).__name__}",
                    )
                )

            if not snapshots:
                return await interaction.followup.send(
                    t(
                        langue,
                        "ev_err_aqua_no_snap",
                        player=player,
                        defaut=f"{{e_error}} **{player}** ne possède aucun snapshot enregistré pour l'Aquamarine.",
                    )
                )

            snapshots.sort(key=lambda x: x.get("collected_at", ""), reverse=True)

            if alliance_name.isdigit():
                target_aid = alliance_name
                for _, info in local_data.items():
                    curr_aid = str(info.get("allianceId", info.get("alliance_id", "")))
                    if curr_aid == target_aid:
                        a_name = info.get("allianceName", info.get("alliance", ""))
                        if isinstance(a_name, dict):
                            a_name = a_name.get("name", "")
                        if a_name and not a_name.isdigit():
                            alliance_name = a_name
                            break

            if mode.value == "latest":
                live = snapshots[0]
                actualisation_dt = _get_api_timestamp(live)
                live_metrics = {m["metric_id"]: int(m["value"]) for m in live.get("metrics", [])}

                pts_cargo_str = f"{live_metrics.get(100, 0):,}".replace(",", " ")
                total_am_str = f"{live_metrics.get(15, 0):,}".replace(",", " ")
                cargo_iles_str = f"{live_metrics.get(16, 0):,}".replace(",", " ")
                cargo_forts_str = f"{live_metrics.get(17, 0):,}".replace(",", " ")
                cargo_pvp_win_str = f"{live_metrics.get(18, 0):,}".replace(",", " ")
                cargo_depense_str = f"{live_metrics.get(19, 0):,}".replace(",", " ")
                cargo_pvp_loss_str = f"{live_metrics.get(20, 0):,}".replace(",", " ")

                status_txt = t(
                    langue,
                    "ev_aqua_live_stats",
                    pts=pts_cargo_str,
                    am=total_am_str,
                    iles=cargo_iles_str,
                    forts=cargo_forts_str,
                    win=cargo_pvp_win_str,
                    loss=cargo_pvp_loss_str,
                    dep=cargo_depense_str,
                    defaut=(
                        f"{{e_pointscargo}} **Points cargo (Classement) :** {pts_cargo_str} pts\n\n"
                        f"{{e_aquamarinetotalcollectee}} **Total aigue-marine collectées :** {total_am_str}\n"
                        f" ↳ {{e_aquamarineiles}} *Dans les îles à ressources :* {cargo_iles_str}\n"
                        f" ↳ {{e_aquamarineforts}} *Dans les forts orageux :* {cargo_forts_str}\n"
                        f" ↳ {{e_aquamarinegagnerjcj}} *Gagné en JcJ :* {cargo_pvp_win_str}\n"
                        f" ↳ {{e_aquamarineperdujcj}} *Perdu en JcJ :* {cargo_pvp_loss_str}\n\n"
                        f"{{e_aquamarinedepenser}} **Dépensé en points cargo :** {cargo_depense_str}"
                    ),
                )

                maintenant_str = discord.utils.utcnow().strftime("%Y-%m")
                snapshot_month = live.get("collected_at", "")[:7]

                title_txt = t(
                    langue,
                    "ev_aqua_title_live",
                    player=player,
                    event=event_trad,
                    defaut=f"{{e_podium}} Score en direct de {player} pour : {event_trad}",
                )
                warning_desc = ""
                couleur_embed = self.clr_aqua

                if snapshot_month != maintenant_str:
                    try:
                        annee_s, mois_s = snapshot_month.split("-")
                        nom_mois_s = f"{get_month_name(mois_s, langue)} {annee_s}"
                        title_txt = t(
                            langue,
                            "ev_aqua_title_archive",
                            player=player,
                            defaut=f"⚓ Score Final Édition Précédente : {player}",
                        )
                        warning_desc = t(
                            langue,
                            "ev_aqua_warning_archive",
                            mois=nom_mois_s,
                            defaut=f"{{e_error}} **Attention :** Ce joueur n'a pas encore lancé l'édition actuelle. Affichage des archives de l'édition de **{nom_mois_s}**.\n\n",
                        )
                        couleur_embed = discord.Color.orange()
                    except:
                        pass

                embed = discord.Embed(title=title_txt, color=couleur_embed)
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
                    name=t(
                        langue,
                        "ev_aqua_reserves_title",
                        defaut="{e_stats} État des Réserves Live\n\n",
                    ),
                    value=f"{warning_desc}{status_txt}",
                    inline=False,
                )

                latest_date = live.get("collected_at", "")
                if latest_date:
                    ts_r = get_discord_timestamp(latest_date, "R", langue)
                    ts_t = get_discord_timestamp(latest_date, "t", langue)
                    embed.add_field(
                        name=t(langue, "ev_aqua_last_hit_title", defaut="⏱️ Dernière Frappe"),
                        value=t(
                            langue,
                            "ev_aqua_last_hit_desc",
                            r=ts_r,
                            t=ts_t,
                            defaut=f"Relevée par l'API {ts_r} (*{ts_t}*)",
                        ),
                        inline=False,
                    )

                await setup_embed_footer(embed, interaction, langue)
                await interaction.followup.send(embed=embed)
                await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)
                return

            elif mode.value == "history":
                from collections import defaultdict

                months_dict = defaultdict(list)

                for sn in snapshots:
                    try:
                        dt = datetime.fromisoformat(sn["collected_at"].replace("Z", "+00:00"))
                        month_key = dt.strftime("%Y-%m")
                        months_dict[month_key].append(
                            {
                                "dt": dt,
                                "collected_at": sn["collected_at"],
                                "metrics": {m["metric_id"]: int(m["value"]) for m in sn.get("metrics", [])},
                            }
                        )
                    except:
                        continue

                if not months_dict:
                    return await interaction.followup.send(
                        t(
                            langue,
                            "ev_err_monthly_hist",
                            defaut="{e_error} Impossible d'analyser l'historique mensuel.",
                        )
                    )

                sorted_months = sorted(months_dict.keys(), reverse=True)
                actualisation_dt = _get_api_timestamp(snapshots)

                grand_total_cargo = 0
                grand_total_am = 0
                monthly_finals = {}

                for m_key in sorted_months:
                    final_sn = max(months_dict[m_key], key=lambda x: x["metrics"].get(100, 0))
                    grand_total_cargo += final_sn["metrics"].get(100, 0)
                    grand_total_am += final_sn["metrics"].get(15, 0)
                    monthly_finals[m_key] = final_sn

                view = AquamarineSelectView(
                    interaction=interaction,
                    player=player,
                    alliance_name=alliance_name,
                    actualisation_dt=actualisation_dt,
                    langue=langue,
                    clr=self.clr_joueur_cumul,
                    sorted_months=sorted_months,
                    monthly_finals=monthly_finals,
                    gt_cargo=grand_total_cargo,
                    gt_am=grand_total_am,
                )

                embed = await view.generate_embed(sorted_months[0])
                view.message = await interaction.followup.send(embed=embed, view=view, wait=True)
                await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)
                return

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

    # =========================================
    # COMMANDE : EVENT ALLIANCE
    # =========================================
    @app_commands.command(name="event_alliance", description="Ranking and participation of an alliance in an event")
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

        embed, error_or_lignes, stats_text, global_latest_str = await generer_rapport_alliance_embed(
            self.bot,
            event_trad,
            event_keys,
            alliance_name,
            self.clr_alliance,
            interaction=interaction,
            custom_server=serveur,
        )

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

    # ==========================================
    # 🔗 LIAISON DU COMPTE DISCORD
    # ==========================================
    @app_commands.command(name="link_account", description="Link your Discord account to your GGE username")
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

    # ==========================================
    # 🕵️ GROUPE /RIVAL
    # ==========================================
    rival_group = app_commands.Group(
        name="rival",
        description="Competition Radar (MP only)",
        allowed_contexts=app_commands.AppCommandContext(guild=False, dm_channel=True, private_channel=True),
    )

    @rival_group.command(name="start", description="Turn on your competition radar")
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
    async def rival_stop(self, interaction: discord.Interaction):
        langue, _ = await get_server_config(interaction)
        data = await load_rivals_async()
        if str(interaction.user.id) in data:
            del data[str(interaction.user.id)]
            await save_rivals_async(data)
            await interaction.response.send_message(t(langue, "ev_rival_stopped", defaut="🛑 Radar désactivé."))

    # ==========================================
    # 🛰️ LE SATELLITE RIVAL
    # ==========================================
    @tasks.loop(minutes=1)
    async def rival_check_task(self):
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
                            except:
                                pass

                        last_scores[rival] = score_rival
                        changes_detected = True

                config["last_known_scores"] = last_scores

            if changes_detected:
                await save_rivals_async(data)

        except Exception as e:
            logger.error(f"❌ [RIVAL TASK CRASH] : {e}")

    # ========================================================
    # GROUPE DE COMMANDES : ROUE DE LA FORTUNE (WOA)
    # ========================================================
    woa = app_commands.Group(name="woa", description="Analysis and statistics of the Wheel of Affluence")

    @woa.command(name="history", description="View the history of tickets spent by a player")
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def woa_historique(self, interaction: discord.Interaction, player: str):
        try:
            await interaction.response.defer(thinking=True)
        except:
            return
        langue, _ = await get_server_config(interaction)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")

        headers = await get_api_headers(interaction)
        session = self.bot.session
        base_api = "https://api.gge-tracker.com/api/v1"
        try:
            async with session.get(f"{base_api}/players/{urllib.parse.quote(player)}", headers=headers, timeout=8) as r:
                if r.status != 200:
                    return await interaction.followup.send(
                        t(
                            langue,
                            "ev_woa_player_not_found",
                            p=player,
                            defaut=f"{{e_error}} Joueur **{player}** introuvable.",
                        )
                    )
                res_base = await r.json()
                if isinstance(res_base, list) and res_base:
                    res_base = res_base[0]
                player_id = str(res_base.get("player_id", res_base.get("id", "")))
                vrai_nom = res_base.get("player_name", player)

            async with session.get(f"{base_api}/woa/events/player/{player_id}", headers=headers, timeout=8) as r:
                if r.status != 200:
                    return await interaction.followup.send(
                        t(langue, "ev_woa_no_hist", defaut="{e_error} Aucun historique WoA trouvé.")
                    )
                events = (await r.json()).get("events", [])

            if not events:
                return await interaction.followup.send(
                    t(langue, "ev_woa_no_part", defaut="{e_error} Aucune participation enregistrée.")
                )

            actualisation_dt = _get_api_timestamp(events)

            from collections import defaultdict

            grand_total = sum(int(ev.get("point", 0)) for ev in events)
            months_data = defaultdict(list)
            for ev in events:
                try:
                    dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
                    months_data[dt.strftime("%Y-%m")].append(
                        {
                            "dt": dt,
                            "date_str": dt.strftime("%d/%m/%Y"),
                            "pts": int(ev.get("point", 0)),
                            "rank": str(ev.get("rank", "?")),
                        }
                    )
                except:
                    continue

            embeds = []
            sorted_months = sorted(months_data.keys(), reverse=True)

            for i, m_key in enumerate(sorted_months):
                month_events = months_data[m_key]
                annee, mois_num = m_key.split("-")
                month_total = sum(e["pts"] for e in month_events)
                best_ev = max(month_events, key=lambda x: x["pts"])
                nom_m = get_month_name(mois_num, langue)

                f_gt = f"{grand_total:,}".replace(",", " ")
                f_mt = f"{month_total:,}".replace(",", " ")
                f_mp = f"{best_ev['pts']:,}".replace(",", " ")
                f_md = best_ev["dt"].strftime("%d/%m")

                stats_txt = t(
                    langue,
                    "ev_woa_hist_stats",
                    gt=f_gt,
                    grand_total=f_gt,
                    m=nom_m,
                    mois=nom_m,
                    y=annee,
                    annee=annee,
                    mt=f_mt,
                    month_total=f_mt,
                    mp=f_mp,
                    max_points=f_mp,
                    md=f_md,
                    max_date=f_md,
                    defaut=(
                        f"🏆 **Total Historique :** {f_gt} {{e_woa_points}}\n"
                        f"{{e_stats}} **Total {nom_m} {annee} :** {f_mt} {{e_woa_points}}\n"
                        f"🚀 **Jour max :** {f_mp} {{e_woa_points}} *(le {f_md})*"
                    ),
                )

                lignes = []
                for ev in month_events:
                    pts_str = f"{ev['pts']:,}".replace(",", " ")
                    rank = ev["rank"]
                    medal = "🥇" if rank == "1" else "🥈" if rank == "2" else "🥉" if rank == "3" else f"**#{rank}**"
                    lignes.append(
                        t(
                            langue,
                            "ev_woa_hist_line",
                            d=ev["date_str"],
                            m=medal,
                            p=pts_str,
                            defaut=f"• **{ev['date_str']}** │ Rang {medal} ➔ **{pts_str} {{e_woa_points}}**",
                        )
                    )

                title = t(
                    langue,
                    "ev_woa_hist_title",
                    nom=vrai_nom,
                    defaut=f"{{e_woaicon}} Historique Roue de la Fortune : {vrai_nom}",
                )
                overv = t(langue, "ev_woa_hist_overview", defaut="**{e_stats} Vue d'ensemble**")
                det_title = t(langue, "ev_woa_hist_details", mois=nom_m, defaut=f"**📜 Détails ({nom_m})**")

                embed = discord.Embed(
                    title=title,
                    description=f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{overv}\n{stats_txt}\n\n{det_title}\n"
                    + "\n".join(lignes),
                    color=self.clr_woa_historique,
                )
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)
            view = PaginationView(embeds)
            view.message = await interaction.followup.send(embed=embeds[0], view=view, wait=True)
            await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)
        except Exception as e:
            await interaction.followup.send(
                t(langue, "ev_err_tech", e=str(e), defaut=f"{{e_error}} Erreur technique : {e}")
            )

    @woa.command(name="summary", description="Displays the ticket consumption summary")
    async def woa_bilan(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True)
        except:
            return
        langue, _ = await get_server_config(interaction)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")

        headers = await get_api_headers(interaction)
        session = self.bot.session
        base_api = "https://api.gge-tracker.com/api/v1"
        try:
            async with session.get(f"{base_api}/woa/events?page=1", headers=headers, timeout=8) as r:
                data = await r.json()
                all_events = data.get("events", [])
                total_pages = data.get("pagination", {}).get("total_pages", 1)
            if total_pages > 1:
                tasks_list = [
                    session.get(f"{base_api}/woa/events?page={p}", headers=headers, timeout=8)
                    for p in range(2, total_pages + 1)
                ]
                responses = await asyncio.gather(*tasks_list, return_exceptions=True)
                for resp in responses:
                    if isinstance(resp, aiohttp.ClientResponse) and resp.status == 200:
                        all_events.extend((await resp.json()).get("events", []))
            all_events.sort(key=lambda x: x.get("date", ""), reverse=True)

            actualisation_dt = _get_api_timestamp(all_events)

            t_editions = len(all_events)
            t_tickets = sum(int(ev.get("total_tickets", 0)) for ev in all_events)
            t_parts = sum(int(ev.get("participants", 0)) for ev in all_events)

            moy_tickets = t_tickets // t_editions if t_editions > 0 else 0
            moy_parts = t_parts // t_editions if t_editions > 0 else 0

            stats_globales = t(
                langue,
                "ev_woa_bilan_stats",
                ed=t_editions,
                tix=f"{t_tickets:,}".replace(",", " "),
                pts=f"{t_parts:,}".replace(",", " "),
                mt=f"{moy_tickets:,}".replace(",", " "),
                mp=f"{moy_parts:,}".replace(",", " "),
                defaut=(
                    f"{{e_stats}} **Éditions :** {t_editions}\n"
                    f"{{e_woa_points}} **Tickets :** {t_tickets:,}\n"
                    f"{{e_members}} **Participants :** {t_parts:,}\n"
                    f"⚖️ **Moyenne :** {moy_tickets:,} {{e_woa_points}} / {moy_parts:,} {{e_members}}"
                ).replace(",", " "),
            )

            lignes, j_vus = [], set()
            for ev in all_events:
                try:
                    d_str = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).strftime("%d/%m/%Y")
                except:
                    continue
                if len(j_vus) >= 31 and d_str not in j_vus:
                    break
                j_vus.add(d_str)
                parts = f"{int(ev.get('participants', 0)):,}".replace(",", " ")
                tix = f"{int(ev.get('total_tickets', 0)):,}".replace(",", " ")
                lignes.append(f"📅 **{d_str}** │ {{e_players}} {parts} │ {{e_woa_points}} **{tix}**")

            title = t(
                langue,
                "ev_woa_bilan_title",
                defaut="{e_woaicon} Bilan Économique : Roue d'Abondance",
            )
            glob = t(langue, "ev_woa_bilan_global", defaut="**{e_icon_world} Statistiques Globales**")
            det = t(langue, "ev_woa_bilan_detail", defaut="**📜 Détail des 31 dernières éditions**")

            embeds = []
            for i in range(0, len(lignes), 15):
                embed = discord.Embed(
                    title=title,
                    description=f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{glob}\n{stats_globales}\n\n{det}\n"
                    + "\n".join(lignes[i : i + 15]),
                    color=self.clr_woa_bilan,
                )
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)
            view = PaginationView(embeds)
            view.message = await interaction.followup.send(embed=embeds[0], view=view, wait=True)
            await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)
        except Exception as e:
            await interaction.followup.send(t(langue, "ev_err_tech", e=str(e), defaut=f"{{e_error}} Erreur : {e}"))

    # ========================================================
    # 🏆 GROUPE DE COMMANDES RACINE : LEADERBOARD
    # ========================================================
    leaderboard = app_commands.Group(name="leaderboard", description="General server rankings")

    @leaderboard.command(name="woa", description="Displays the Top 100 from the latest Wheel of Affluence")
    async def classement_woa(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True)
        except:
            return
        langue, _ = await get_server_config(interaction)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")

        headers = await get_api_headers(interaction)
        session = self.bot.session
        base_api = "https://api.gge-tracker.com/api/v1"
        try:
            async with session.get(f"{base_api}/woa/events", headers=headers, timeout=8) as r:
                if r.status != 200:
                    return await interaction.followup.send(
                        t(langue, "ev_err_api_unavail", defaut="{e_error} API indisponible.")
                    )

                woa_base_data = await r.json()
                latest_date_str = woa_base_data["events"][0]["date"]
                encoded_date = urllib.parse.quote(latest_date_str)

            async with session.get(
                f"{base_api}/woa/events/date/{encoded_date}?page=1", headers=headers, timeout=8
            ) as r:
                data_rank = await r.json()
                all_players = data_rank.get("players", [])
                total_pages = data_rank.get("pagination", {}).get("total_pages", 1)

            pages_to_fetch = min(total_pages, 7)
            if pages_to_fetch > 1:
                fetch_tasks = [
                    session.get(f"{base_api}/woa/events/date/{encoded_date}?page={p}", headers=headers, timeout=8)
                    for p in range(2, pages_to_fetch + 1)
                ]
                responses = await asyncio.gather(*fetch_tasks, return_exceptions=True)
                for resp in responses:
                    if isinstance(resp, aiohttp.ClientResponse) and resp.status == 200:
                        all_players.extend((await resp.json()).get("players", []))

            if not all_players:
                return await interaction.followup.send(
                    t(langue, "ev_err_no_players", defaut="{e_error} Aucun joueur trouvé.")
                )

            all_players = all_players[:100]

            lignes = []
            for idx, p in enumerate(all_players):
                rang = idx + 1
                nom = p.get("player_name", "???")
                pts = f"{int(p.get('point', 0)):,}".replace(",", " ")
                alli = p.get("alliance_name") or "Sans alliance"
                medal = "🥇" if rang == 1 else "🥈" if rang == 2 else "🥉" if rang == 3 else f"**{rang}.**"
                lignes.append(f"{medal} **{nom}** [{alli}] ➔ **{pts} {{e_woa_points}}**")

            actualisation_dt = _get_api_timestamp(data_rank, woa_base_data)

            embeds = []
            for i in range(0, len(lignes), 10):
                embed = discord.Embed(
                    title=t(langue, "ev_class_woa_title", defaut="Top 100 Serveur - Roue de la Fortune"),
                    color=self.clr_woa_classement,
                )
                embed.description = (
                    f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)"
                )
                embed.add_field(
                    name=t(langue, "ev_class_global_field", defaut="Classement global"),
                    value="\n".join(lignes[i : i + 10]),
                    inline=False,
                )
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)
            view = PaginationView(embeds)
            view.message = await interaction.followup.send(embed=embeds[0], view=view, wait=True)
            await prompt_vote_if_lucky(interaction, probability_percent=10, langue=langue)
        except Exception as e:
            await interaction.followup.send(t(langue, "ev_err_tech", e=str(e), defaut=f"{{e_error}} Erreur : {e}"))

    @leaderboard.command(name="storm_islands", description="Displays the Top 100 looters of Aquamarine")
    async def classement_iles(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True)
        except:
            return
        langue, _ = await get_server_config(interaction)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")

        headers = await get_api_headers(interaction)
        session = self.bot.session
        base_api = "https://api.gge-tracker.com/api/v1"
        try:
            async with session.get(
                f"{base_api}/aquamarine?page=1&order_by=100&order_dir=DESC", headers=headers, timeout=8
            ) as r:
                if r.status != 200:
                    return await interaction.followup.send(
                        t(langue, "ev_err_api_unavail", defaut="{e_error} API indisponible.")
                    )
                data_rank = await r.json()
                players = data_rank.get("players", [])
                total_pages = data_rank.get("pagination", {}).get("total_pages", 1)
                total_items = data_rank.get("pagination", {}).get("total_items_count", "?")

            pages_to_fetch = min(total_pages, 15)
            if pages_to_fetch > 1:
                fetch_tasks = [
                    session.get(
                        f"{base_api}/aquamarine?page={p}&order_by=100&order_dir=DESC", headers=headers, timeout=8
                    )
                    for p in range(2, pages_to_fetch + 1)
                ]
                responses = await asyncio.gather(*fetch_tasks, return_exceptions=True)
                for resp in responses:
                    if isinstance(resp, aiohttp.ClientResponse) and resp.status == 200:
                        players.extend((await resp.json()).get("players", []))

            if not players:
                return await interaction.followup.send(
                    t(langue, "ev_err_no_players", defaut="{e_error} Aucun joueur trouvé.")
                )

            mois_actif = ""
            for p in players:
                date_p = p.get("last_collected_at", "")
                if date_p and date_p[:7] > mois_actif:
                    mois_actif = date_p[:7]

            players_filtres = []
            for p in players:
                date_p = p.get("last_collected_at", "")
                if date_p and date_p.startswith(mois_actif):
                    players_filtres.append(p)

            players = players_filtres[:100]

            if not players:
                return await interaction.followup.send(
                    t(
                        langue,
                        "ev_err_aqua_not_started",
                        defaut="{e_error} Aucun joueur n'a encore débuté l'édition de ce mois-ci.",
                    )
                )

            lignes = []
            for r_idx, p in enumerate(players):
                rang = r_idx + 1
                nom = p.get("player_name", "???")
                metrics = p.get("metrics", {})
                pts = f"{int(metrics.get('100', 0)):,}".replace(",", " ")
                medal = "🥇" if rang == 1 else "🥈" if rang == 2 else "🥉" if rang == 3 else f"**{rang}.**"
                lignes.append(f"{medal} **{nom}** ➔ **{pts} {{e_pointscargo}}**")

            actualisation_dt = _get_api_timestamp(players)

            embeds = []
            annee_actuelle, mois_num_actuel = mois_actif.split("-")
            nom_mois_actif = f"{get_month_name(mois_num_actuel, langue)} {annee_actuelle}"

            titre = t(
                langue,
                "ev_class_aqua_title",
                m=nom_mois_actif,
                defaut=f"Top 100 Serveur - Îles Orageuses ({nom_mois_actif})",
            )
            desc = t(
                langue,
                "ev_class_aqua_desc",
                t=total_items,
                defaut=f"Classement basé sur les points cargo de l'édition en cours.\n*Total recensé sur le serveur : {total_items} joueurs*",
            )

            for i in range(0, len(lignes), 10):
                embed = discord.Embed(
                    title=titre,
                    description=f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{desc}",
                    color=self.clr_aqua,
                )
                embed.add_field(
                    name=t(langue, "ev_class_global_field", defaut="Classement global"),
                    value="\n".join(lignes[i : i + 10]),
                    inline=False,
                )
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)
            view = PaginationView(embeds)
            view.message = await interaction.followup.send(embed=embeds[0], view=view, wait=True)
            await prompt_vote_if_lucky(interaction, probability_percent=15, langue=langue)
        except Exception as e:
            await interaction.followup.send(
                t(langue, "ev_err_tech", e=str(e), defaut=f"{{e_error}} Erreur technique : {e}")
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))
