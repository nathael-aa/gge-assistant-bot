import asyncio
import json
import logging
import urllib.parse

import discord
from discord import app_commands
from discord.ext import commands

import observability as obs
from utils import (
    BRACKETS,
    CONFIG_DIR,
    DICT_EMOJIS,
    EVENT_IDS,
    alliance_autocomplete,
    get_server_config,
    joueur_autocomplete,
    setup_embed_footer,
    t,
)


def get_emo(langue, default_emo):
    k = "emo_ignore"
    return t(langue, k, defaut=default_emo)


def extract_p_name(p) -> str:
    if isinstance(p, dict):
        return str(p.get("P", p.get("N", ""))).strip()
    info = next((item for item in p if isinstance(item, dict)), {})
    name = info.get("N", "")
    if name:
        return str(name).strip()
    if len(p) >= 4 and isinstance(p[3], str):
        return p[3].strip()
    if len(p) >= 3 and isinstance(p[2], list) and len(p[2]) >= 2 and isinstance(p[2][1], str):
        return p[2][1].strip()
    return ""


def extract_p_rank(p) -> int:
    if isinstance(p, dict):
        return int(p.get("R", 999999))
    if len(p) >= 5 and isinstance(p[4], (int, float)) and not isinstance(p[2], dict):
        return int(p[4])
    return int(p[0]) if len(p) > 0 and isinstance(p[0], (int, float)) else 999999


class RankJumpModal(discord.ui.Modal):
    def __init__(self, parent_view: "LiveRankingView", langue: str):
        title_txt = t(langue, "rank_modal_title", defaut="Aller à un rang")[:45]
        super().__init__(title=title_txt)
        self.parent_view = parent_view
        self.langue = langue

        label_txt = t(langue, "rank_modal_label", defaut="Numéro du rang (ex: 50, 250...)")[:45]
        self.rank_input = discord.ui.TextInput(
            label=label_txt,
            placeholder="1 - 99999",
            min_length=1,
            max_length=7,
            required=True,
        )
        self.add_item(self.rank_input)

    async def on_submit(self, interaction: discord.Interaction):
        val = self.rank_input.value.strip()
        if not val.isdigit() or int(val) < 1:
            msg_err = t(self.langue, "rank_modal_invalid", defaut="❌ Veuillez entrer un numéro de rang valide (>= 1).")
            return await interaction.response.send_message(msg_err, ephemeral=True)

        target_rank = int(val)
        if self.parent_view.total_ranks and target_rank > self.parent_view.total_ranks:
            target_rank = max(1, self.parent_view.total_ranks - 4)

        await self.parent_view.navigate_to_rank(interaction, target_rank, force_refresh=False)


class LiveRankingView(discord.ui.View):
    def __init__(
        self,
        cog: "ClassementCog",
        interaction: discord.Interaction,
        langue: str,
        serveur_api: str,
        event_id: int,
        lid: int,
        titre: str,
        embed_color: discord.Color,
        event_name: str,
        is_alliance: bool = False,
        api_command: str = "hgh",
        pagination_param: str = "SV",
        is_string_val: bool = True,
        page_step: int = None,
        initial_sv: int = 1,
        total_ranks: int = None,
        highlight_player: str = None,
        initial_embed: discord.Embed = None,
        berimond_both_camps: bool = False,
        timeout: int = 3600,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.user_id = interaction.user.id
        self.ctx_int = interaction
        self.langue = langue
        self.serveur_api = serveur_api
        self.event_id = event_id
        self.lid = lid
        self.titre = titre
        self.embed_color = embed_color
        self.event_name = event_name
        self.is_alliance = is_alliance
        self.api_command = api_command
        self.pagination_param = pagination_param
        self.is_string_val = is_string_val
        self.page_step = page_step or (10 if api_command == "llsp" else 8)
        self.current_sv = max(1, initial_sv)
        self.total_ranks = total_ranks
        self.highlight_player = highlight_player
        self.berimond_both_camps = berimond_both_camps
        self.message = None

        self._page_cache = {}
        if initial_embed is not None:
            norm_sv = self._normalize_sv(self.current_sv)
            self._page_cache[norm_sv] = (initial_embed, self.total_ranks)

        self._setup_buttons()
        self.update_buttons()

    def _normalize_sv(self, rank_val: int) -> int:
        if rank_val <= 1:
            return 1
        bucket = (rank_val - 1) // self.page_step
        return (bucket * self.page_step) + 1

    def _compute_api_sv(self, norm_sv: int) -> int:
        if self.api_command == "llsp":
            return norm_sv
        if norm_sv <= 1:
            return 1
        offset = (self.page_step - 1) // 2
        return norm_sv + offset

    def _setup_buttons(self):
        self.clear_items()

        # Ligne 1 (row=0) : Précédent | Refresh | Suivant
        self.btn_prev = discord.ui.Button(
            emoji=DICT_EMOJIS.get("e_last", "◀️"),
            style=discord.ButtonStyle.primary,
            row=0,
            custom_id="rank_btn_prev",
        )
        self.btn_prev.callback = self.on_prev

        self.btn_refresh = discord.ui.Button(
            label="Refresh",
            emoji=DICT_EMOJIS.get("e_refresh", "🔁"),
            style=discord.ButtonStyle.secondary,
            row=0,
            custom_id="rank_btn_refresh",
        )
        self.btn_refresh.callback = self.on_refresh

        self.btn_next = discord.ui.Button(
            emoji=DICT_EMOJIS.get("e_next", "▶️"),
            style=discord.ButtonStyle.primary,
            row=0,
            custom_id="rank_btn_next",
        )
        self.btn_next.callback = self.on_next

        # Ligne 2 (row=1) : Page 1 | Aller au Rang #
        self.btn_first = discord.ui.Button(
            emoji=DICT_EMOJIS.get("e_one", "1️⃣"),
            style=discord.ButtonStyle.secondary,
            row=1,
            custom_id="rank_btn_first",
        )
        self.btn_first.callback = self.on_first

        self.btn_jump = discord.ui.Button(
            label=t(self.langue, "rank_btn_jump", defaut="Rang"),
            emoji=DICT_EMOJIS.get("e_ranking", "🔢"),
            style=discord.ButtonStyle.secondary,
            row=1,
            custom_id="rank_btn_jump",
        )
        self.btn_jump.callback = self.on_jump

        self.add_item(self.btn_prev)
        self.add_item(self.btn_refresh)
        self.add_item(self.btn_next)
        self.add_item(self.btn_first)
        self.add_item(self.btn_jump)

    def update_buttons(self):
        norm_sv = self._normalize_sv(self.current_sv)
        at_start = norm_sv <= 1
        self.btn_first.disabled = at_start
        self.btn_prev.disabled = at_start

        if self.total_ranks and self.total_ranks > 0:
            self.btn_next.disabled = (norm_sv + self.page_step) > self.total_ranks
        else:
            self.btn_next.disabled = False

    async def _check_author(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            msg_err = t(self.langue, "err_not_author", defaut="❌ Only the command author can use these buttons.")
            await interaction.response.send_message(msg_err, ephemeral=True)
            return False
        return True

    async def navigate_to_rank(self, interaction: discord.Interaction, target_sv: int, force_refresh: bool = False):
        if not await self._check_author(interaction):
            return

        try:
            await interaction.response.defer()
        except Exception as e:
            self.cog.logger.error(f"❌ [Classement] Erreur defer navigation : {e}")
            return

        norm_sv = self._normalize_sv(max(1, target_sv))

        if not force_refresh and norm_sv in self._page_cache:
            cached_embed, cached_total = self._page_cache[norm_sv]
            self.current_sv = norm_sv
            if cached_total:
                self.total_ranks = cached_total
            self.update_buttons()
            try:
                await interaction.edit_original_response(embed=cached_embed, view=self)
            except Exception as e:
                self.cog.logger.error(f"❌ [Classement] Erreur edit cache : {e}")
            return

        api_sv_val = self._compute_api_sv(norm_sv)

        if self.berimond_both_camps:
            res3 = await self.cog.fetch_single_chunk(
                serveur_api=self.serveur_api, event_id=self.event_id, lid=3, sv_val=api_sv_val
            )
            res4 = await self.cog.fetch_single_chunk(
                serveur_api=self.serveur_api, event_id=self.event_id, lid=4, sv_val=api_sv_val
            )
            l_chunk = []
            lr_val = 0
            for r_camp in (res3, res4):
                if r_camp and str(r_camp.get("return_code")) == "0":
                    c_data = r_camp.get("content", {})
                    l_chunk.extend(c_data.get("L", []))
                    if isinstance(c_data.get("LR"), int):
                        lr_val = max(lr_val, c_data.get("LR"))
            res_ok = bool(l_chunk)
        else:
            res = await self.cog.fetch_single_chunk(
                serveur_api=self.serveur_api,
                event_id=self.event_id,
                lid=self.lid,
                sv_val=api_sv_val,
                is_alliance=self.is_alliance,
                api_command=self.api_command,
                pagination_param=self.pagination_param,
                is_string_val=self.is_string_val,
            )
            res_ok = bool(res and str(res.get("return_code")) == "0")
            content = res.get("content", {}) if res_ok else {}
            l_chunk = content.get("L", [])
            lr_val = content.get("LR")

        if not res_ok:
            msg_err = t(
                self.langue,
                "api_unavailable_error",
                defaut="⚠️ **L'API de classement est momentanément indisponible ou la fin du classement est atteinte.**",
            )
            await interaction.followup.send(msg_err, ephemeral=True)
            return

        if not l_chunk:
            msg_empty = t(self.langue, "rank_end_reached", defaut="📭 Aucune donnée supplémentaire à ce rang.")
            self.btn_next.disabled = True
            try:
                await interaction.edit_original_response(view=self)
            except Exception:
                pass
            await interaction.followup.send(msg_empty, ephemeral=True)
            return

        if isinstance(lr_val, int) and lr_val > 0:
            self.total_ranks = lr_val

        self.current_sv = norm_sv

        embed, detected_step = await self.cog.build_single_page_embed(
            ctx_int=interaction,
            raw_list=l_chunk,
            langue=self.langue,
            titre=self.titre,
            color=self.embed_color,
            is_alliance=self.is_alliance,
            highlight_player=self.highlight_player,
            event_name=self.event_name,
            total_ranks=self.total_ranks,
            fallback_step=self.page_step,
        )

        if detected_step and detected_step >= 5:
            self.page_step = detected_step

        self._page_cache[norm_sv] = (embed, self.total_ranks)
        self.update_buttons()

        if not self.message:
            self.message = interaction.message
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            self.cog.logger.error(f"❌ [Classement] Erreur edition message live : {e}")

    async def on_first(self, interaction: discord.Interaction):
        await self.navigate_to_rank(interaction, 1, force_refresh=False)

    async def on_prev(self, interaction: discord.Interaction):
        norm_sv = self._normalize_sv(self.current_sv)
        await self.navigate_to_rank(interaction, max(1, norm_sv - self.page_step), force_refresh=False)

    async def on_next(self, interaction: discord.Interaction):
        norm_sv = self._normalize_sv(self.current_sv)
        await self.navigate_to_rank(interaction, norm_sv + self.page_step, force_refresh=False)

    async def on_refresh(self, interaction: discord.Interaction):
        self._page_cache.clear()
        await self.navigate_to_rank(interaction, self.current_sv, force_refresh=True)

    async def on_jump(self, interaction: discord.Interaction):
        if not await self._check_author(interaction):
            return
        await interaction.response.send_modal(RankJumpModal(self, self.langue))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class ClassementCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger("GGE_Bot")
        self.ranking_api_url = "https://empire-api.fly.dev"
        self._server_locks: dict[str, asyncio.Semaphore] = {}

    def _get_server_sem(self, serveur_api: str) -> asyncio.Semaphore:
        if serveur_api not in self._server_locks:
            self._server_locks[serveur_api] = asyncio.Semaphore(2)
        return self._server_locks[serveur_api]

    async def _http_get_json(self, url: str, headers: dict = None, timeout: int = 10):
        r = await self.bot.session.get(url, headers=headers, timeout=timeout)
        try:
            try:
                data = await r.json()
            except Exception:
                data = None
            return r.status, data
        finally:
            r.close()

    async def fetch_single_chunk(
        self,
        serveur_api: str,
        event_id: int,
        lid: int,
        sv_val,
        is_alliance: bool = False,
        api_command: str = "hgh",
        pagination_param: str = "SV",
        is_string_val: bool = True,
    ):
        sv_encoded = urllib.parse.quote(str(sv_val))
        effective_lid = 1 if is_alliance else lid

        if is_string_val:
            url = (
                f"{self.ranking_api_url}/{serveur_api}/{api_command}/"
                f"%22LT%22:{event_id},%22LID%22:{effective_lid},%22{pagination_param}%22:%22{sv_encoded}%22"
            )
        else:
            url = (
                f"{self.ranking_api_url}/{serveur_api}/{api_command}/"
                f"%22LT%22:{event_id},%22LID%22:{effective_lid},%22{pagination_param}%22:{sv_encoded}"
            )

        sem = self._get_server_sem(serveur_api)
        async with sem:
            for attempt in range(3):
                try:
                    status, data = await self._http_get_json(url, timeout=10)
                    if status == 200 and data is not None:
                        return data
                    if status in [400, 404]:
                        return data if isinstance(data, dict) else {"return_code": "-1", "http_status": status}

                    self.logger.warning(f"⚠️ [API] Code {status} reçu. URL: {url}")
                    if status in [429, 500, 502, 503, 504]:
                        await asyncio.sleep(1.2)
                except TimeoutError:
                    self.logger.warning(f"⏳ [API] Timeout (Tentative {attempt + 1}/3) - URL: {url}")
                except Exception as e:
                    self.logger.error(f"❌ [API] Erreur de connexion : {e}")
                await asyncio.sleep(0.3)
        return None

    def get_serveur_api(self, serveur_local: str) -> str:
        from utils import FLYDEV_MAPPING

        serveur_lower = serveur_local.lower()

        if serveur_lower in FLYDEV_MAPPING:
            return FLYDEV_MAPPING[serveur_lower]

        cache_file = CONFIG_DIR / "servers_cache.json"
        if cache_file.exists():
            try:
                with open(cache_file, encoding="utf-8") as f:
                    data = json.load(f).get("servers_info", {})
                    for srv_name, srv_data in data.items():
                        if srv_name.lower() == serveur_lower:
                            return srv_data.get("api_name", serveur_local)
            except Exception as e:
                self.logger.error(f"❌ [Classement] Erreur lecture servers_cache.json : {e}")

        return serveur_local

    async def tranche_autocomplete(self, interaction: discord.Interaction, current: str):
        ns = interaction.namespace
        choix = []

        if hasattr(ns, "statistic") and ns.statistic:
            if ns.statistic in ["plunder", "legendary"]:
                choix = [app_commands.Choice(name="Global Ranking", value=1)]
            else:
                choix = [
                    app_commands.Choice(name="Levels 70+", value=6),
                    app_commands.Choice(name="Levels 50-69", value=5),
                    app_commands.Choice(name="Levels 40-49", value=4),
                    app_commands.Choice(name="Levels 30-39", value=3),
                    app_commands.Choice(name="Levels 20-29", value=2),
                    app_commands.Choice(name="Levels 1-19", value=1),
                ]
        elif hasattr(ns, "evenement") and ns.evenement:
            ev = ns.evenement
            if ev == "berimond":
                choix = [
                    app_commands.Choice(name="Ursidae (Lvl 70+)", value=3),
                    app_commands.Choice(name="Gerbrandt (Lvl 70+)", value=4),
                    app_commands.Choice(name="Ursidae (Lvl 40-69)", value=1),
                    app_commands.Choice(name="Gerbrandt (Lvl 40-69)", value=2),
                ]
            elif ev == "nobility":
                choix = [
                    app_commands.Choice(name="Legendary (70+)", value=7),
                    app_commands.Choice(name="Levels 50-69", value=6),
                    app_commands.Choice(name="Levels 30-49", value=5),
                    app_commands.Choice(name="Levels 25-29", value=4),
                    app_commands.Choice(name="Levels 20-24", value=3),
                    app_commands.Choice(name="Levels 15-19", value=2),
                    app_commands.Choice(name="Levels 10-14", value=1),
                ]
            elif ev == "season":
                choix = [
                    app_commands.Choice(name="Legendary 950+", value=4),
                    app_commands.Choice(name="Legendary 650-949", value=3),
                    app_commands.Choice(name="Legendary 200-649", value=2),
                    app_commands.Choice(name="Legendary 1-199", value=1),
                ]
            elif ev in [
                "flora",
                "snowglobe",
                "hollowmoon",
                "sandfortune",
                "banquet",
                "midnight",
                "realms_current",
                "realms_finished",
                "horizon",
                "league",
                "woa",
                "patronage",
            ]:
                choix = [app_commands.Choice(name="Global Ranking", value=1)]
            else:
                choix = [
                    app_commands.Choice(name="Legendary 950+", value=5),
                    app_commands.Choice(name="Legendary 650-949", value=4),
                    app_commands.Choice(name="Legendary 300-649", value=3),
                    app_commands.Choice(name="Legendary 1-299", value=2),
                    app_commands.Choice(name="Levels < 70", value=1),
                ]
        else:
            choix = [app_commands.Choice(name="Global Ranking", value=1)]

        return [c for c in choix if current.lower() in c.name.lower()][:25]

    classement = app_commands.Group(name="rank", description="Live event analysis and rankings")

    def get_league_title_and_level(self, rank):
        if rank > 21:
            rank = 21

        if rank == 21:
            return "{e_title_5}", "{e_level_title_3}"

        tier = (rank - 1) // 4
        sub_level = (rank - 1) % 4

        titles = ["{e_title_0}", "{e_title_1}", "{e_title_2}", "{e_title_3}", "{e_title_4}"]
        levels = ["", "{e_level_title_1}", "{e_level_title_2}", "{e_level_title_3}"]

        return titles[tier], levels[sub_level]

    def format_page(
        self,
        chunk,
        langue,
        color=discord.Color.gold(),
        is_alliance=False,
        highlight_player=None,
        event_name=None,
    ):
        embed = discord.Embed(color=color)
        description = ""

        txt_unknown = t(langue, "prof_unknown", defaut="Unknown")
        txt_none = t(langue, "prof_none", defaut="None")

        MEDAILLES = {
            1: get_emo(langue, "{e_medal_gold}"),
            2: get_emo(langue, "{e_medal_silver}"),
            3: get_emo(langue, "{e_medal_bronze}"),
        }

        for p in chunk:
            info = {}
            if isinstance(p, dict):
                rank = int(p.get("R", 0))
                score = p.get("S", 0)
                name = p.get("P", txt_unknown)
                alliance = p.get("A", txt_none)
            else:
                if len(p) < 2:
                    continue
                score = p[1]

                if len(p) >= 5 and isinstance(p[3], str) and not isinstance(p[2], dict):
                    rank = int(p[4])
                    name = p[3]
                    alliance = txt_none
                elif len(p) >= 3 and isinstance(p[2], list) and len(p[2]) >= 2:
                    rank = int(p[0])
                    name = p[2][1]
                    alliance = txt_none
                else:
                    rank = int(p[0])
                    info = next((item for item in p if isinstance(item, dict)), {})
                    name = info.get("N", txt_unknown)
                    alliance = info.get("AN", txt_none)

            league_str = ""
            if event_name == "league" and not is_alliance:
                klmo = info.get("KLMO", [])
                klrid = info.get("KLRID", 1)

                MEDAL_VALUES = {1: 1000, 2: 950, 3: 850, 4: 700, 5: 500, 6: 300, 7: 100}
                real_score = 0
                if klmo:
                    for med in klmo:
                        if len(med) == 2:
                            real_score += MEDAL_VALUES.get(med[0], 0) * med[1]
                score = real_score

            pts_str = f"{score:,}".replace(",", " ")
            medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"**#{rank}**"

            if highlight_player and (
                name.lower() == highlight_player.lower() or name.lower().startswith(f"{highlight_player.lower()}_")
            ):
                display_name = f"__**{name}**__ 🎯"
            else:
                display_name = f"**{name}**"

            if event_name == "league" and not is_alliance:
                title_raw, level_raw = self.get_league_title_and_level(klrid)
                title_emo = get_emo(langue, title_raw) if title_raw else ""
                level_emo = get_emo(langue, level_raw) if level_raw else ""
                titre_str = f"{title_emo}{level_emo}".strip()

                medailles_str = ""
                if klmo:
                    m_list = []
                    for med in klmo:
                        if len(med) == 2 and med[0] in [1, 2, 3] and med[1] > 0:
                            m_raw = MEDAILLES.get(med[0], f"`[M{med[0]}]`")
                            m_emo = get_emo(langue, m_raw)
                            m_list.append(f"{m_emo}x{med[1]}")
                    if m_list:
                        medailles_str = f" {' '.join(m_list)}"

                combo = f"{titre_str}{medailles_str}".strip()
                if combo:
                    league_str = f" | {combo}"

            if is_alliance:
                if event_name == "alliance_league":
                    description += f"{medal} {display_name}{league_str}\n"
                else:
                    description += f"{medal} {display_name}{league_str} | `{pts_str}` pts\n"
            else:
                all_str = f" ({alliance})" if alliance and alliance != txt_none else ""
                if event_name == "league":
                    valeur_txt = t(langue, "cal_league_value", defaut="Valeur")
                    description += f"{medal} {display_name}{all_str}{league_str} | `{valeur_txt} : {pts_str}`\n"
                else:
                    description += f"{medal} {display_name}{all_str}{league_str} | `{pts_str}` pts\n"

        embed.description = description
        return embed

    async def build_single_page_embed(
        self,
        ctx_int: discord.Interaction,
        raw_list: list,
        langue: str,
        titre: str,
        color: discord.Color,
        is_alliance: bool,
        highlight_player: str = None,
        event_name: str = None,
        total_ranks: int = None,
        warning_msg: str = "",
        fallback_step: int = None,
    ) -> tuple[discord.Embed, int]:
        seen = set()
        clean_list = []
        for p in raw_list:
            name = extract_p_name(p)
            if name and name not in seen:
                seen.add(name)
                clean_list.append(p)

        sorted_list = sorted(clean_list, key=extract_p_rank)
        default_step = fallback_step or (10 if event_name == "season" else 8)
        step = len(sorted_list) if 5 <= len(sorted_list) <= 12 else default_step
        sorted_list = sorted_list[:step]

        embed = self.format_page(
            sorted_list,
            langue=langue,
            color=color,
            is_alliance=is_alliance,
            highlight_player=highlight_player,
            event_name=event_name,
        )
        embed.title = titre
        if warning_msg:
            embed.description = (embed.description or "") + warning_msg

        if sorted_list:
            r_min = extract_p_rank(sorted_list[0])
            r_max = extract_p_rank(sorted_list[-1])
            cur_page = ((max(1, r_min) - 1) // step) + 1
            if total_ranks and total_ranks > 0:
                tot_pages = ((total_ranks - 1) // step) + 1
                footer_text = t(
                    langue,
                    "ev_page_footer_live",
                    cur=cur_page,
                    tot=tot_pages,
                    rmin=r_min,
                    rmax=r_max,
                    total=total_ranks,
                    defaut=f"Page {cur_page}/{tot_pages} • Rangs #{r_min}–#{r_max} (Total : {total_ranks:,})".replace(
                        ",", " "
                    ),
                )
            else:
                footer_text = f"Page {cur_page} • Rangs #{r_min}–#{r_max}"
            embed.set_footer(text=footer_text)

        await setup_embed_footer(embed, ctx_int, langue)
        return embed, step

    async def resolve_initial_ranking(
        self,
        serveur_api: str,
        event_id: int,
        search_lids: list[int],
        default_lid: int,
        search_target: str = None,
        target_rank: int = None,
        cross_server_suffix: str = None,
        is_alliance: bool = False,
        api_command: str = "hgh",
        pagination_param: str = "SV",
        is_string_val: bool = True,
        berimond_both_camps: bool = False,
    ):
        search_mode = bool(search_target)
        any_response = False

        def matches_target(p_item) -> bool:
            api_name = extract_p_name(p_item).lower()
            if not api_name or not search_target:
                return False
            t_low = search_target.lower().strip()
            if api_name == t_low or api_name.startswith(f"{t_low}_"):
                return True
            if cross_server_suffix and api_name == f"{t_low}_{cross_server_suffix.lower()}":
                return True
            return False

        if search_mode and not target_rank and api_command == "hgh":
            queries = [search_target.strip()]
            if cross_server_suffix:
                suffixed = f"{search_target.strip()}_{cross_server_suffix}"
                if suffixed.lower() != search_target.strip().lower():
                    queries.insert(0, suffixed)

            first_valid_chunk = None
            first_valid_lid = default_lid
            first_valid_lr = None

            for lid in search_lids:
                for q_str in queries:
                    res = await self.fetch_single_chunk(
                        serveur_api=serveur_api,
                        event_id=event_id,
                        lid=lid,
                        sv_val=q_str,
                        is_alliance=is_alliance,
                        api_command=api_command,
                        pagination_param=pagination_param,
                        is_string_val=True,
                    )
                    if res is not None:
                        any_response = True
                    if res and str(res.get("return_code")) == "0":
                        content = res.get("content", {})
                        l_chunk = content.get("L", [])
                        lr_val = content.get("LR")
                        if l_chunk:
                            if first_valid_chunk is None:
                                first_valid_chunk = l_chunk
                                first_valid_lid = lid
                                first_valid_lr = lr_val
                            for p in l_chunk:
                                if matches_target(p):
                                    p_rank = extract_p_rank(p)
                                    return l_chunk, lid, True, p_rank, lr_val, False

            if first_valid_chunk:
                r_anchor = extract_p_rank(first_valid_chunk[0])
                return first_valid_chunk, first_valid_lid, True, r_anchor, first_valid_lr, False

        if search_mode and not target_rank and api_command == "llsp":
            for r_start in (1, 11, 21, 31, 41):
                res = await self.fetch_single_chunk(
                    serveur_api=serveur_api,
                    event_id=event_id,
                    lid=default_lid,
                    sv_val=r_start,
                    is_alliance=False,
                    api_command="llsp",
                    pagination_param="R",
                    is_string_val=False,
                )
                if res is not None:
                    any_response = True
                if res and str(res.get("return_code")) == "0":
                    content = res.get("content", {})
                    l_chunk = content.get("L", [])
                    lr_val = content.get("LR")
                    if l_chunk:
                        for p in l_chunk:
                            if matches_target(p):
                                return l_chunk, default_lid, True, r_start, lr_val, False
                    else:
                        break

        requested_rank = max(1, target_rank) if target_rank else 1
        if api_command == "llsp":
            norm_sv = ((requested_rank - 1) // 10) * 10 + 1
            api_sv = norm_sv
        else:
            api_sv = target_rank if target_rank else 1
            norm_sv = requested_rank

        if berimond_both_camps:
            res3 = await self.fetch_single_chunk(serveur_api=serveur_api, event_id=event_id, lid=3, sv_val=api_sv)
            res4 = await self.fetch_single_chunk(serveur_api=serveur_api, event_id=event_id, lid=4, sv_val=api_sv)
            l_chunk = []
            lr_val = 0
            for r_camp in (res3, res4):
                if r_camp is not None:
                    any_response = True
                if r_camp and str(r_camp.get("return_code")) == "0":
                    c_data = r_camp.get("content", {})
                    l_chunk.extend(c_data.get("L", []))
                    if isinstance(c_data.get("LR"), int):
                        lr_val = max(lr_val, c_data.get("LR"))
            return l_chunk, 3, False, norm_sv, lr_val or None, not any_response

        res = await self.fetch_single_chunk(
            serveur_api=serveur_api,
            event_id=event_id,
            lid=default_lid,
            sv_val=api_sv,
            is_alliance=is_alliance,
            api_command=api_command,
            pagination_param=pagination_param,
            is_string_val=is_string_val,
        )

        if res is None:
            return [], default_lid, False, norm_sv, None, not any_response

        if str(res.get("return_code")) == "0":
            content = res.get("content", {})
            l_chunk = content.get("L", [])
            lr_val = content.get("LR")
            found_in_fallback = any(matches_target(p) for p in l_chunk) if search_mode else False
            first_r = extract_p_rank(l_chunk[0]) if l_chunk else norm_sv
            return l_chunk, default_lid, found_in_fallback, first_r, lr_val, False

        return [], default_lid, False, norm_sv, None, False

    def _build_not_found_warning(
        self, langue: str, target_name: str, target_rank: int = None, is_alliance: bool = False
    ) -> str:
        if is_alliance:
            if target_rank:
                msg = t(
                    langue,
                    "info_alliance_not_found_rank",
                    alliance=target_name,
                    rang=target_rank,
                    defaut="*💡 Info: Alliance **{alliance}** not found. Showing rank {rang} instead.*",
                )
            else:
                msg = t(
                    langue,
                    "info_alliance_not_found_top",
                    alliance=target_name,
                    top=8,
                    defaut="*💡 Info: Alliance **{alliance}** not found in this ranking. Showing first page instead.*",
                )
        else:
            if target_rank:
                msg = t(
                    langue,
                    "info_player_not_found_rank",
                    joueur=target_name,
                    rang=target_rank,
                    defaut="*💡 Info: Player **{joueur}** not found. Showing rank {rang} instead.*",
                )
            else:
                msg = t(
                    langue,
                    "info_player_not_found_top",
                    joueur=target_name,
                    top=8,
                    defaut="*💡 Info: Player **{joueur}** not found in this bracket. Showing first page instead.*",
                )
        return f"\n\n{msg}"

    @classement.command(name="statistics", description="Displays a live ranking for player statistics.")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.describe(
        statistic="Category", player="Player (Optional)", rank="Rank (Optional)", tranche="Bracket (Optional)"
    )
    @app_commands.autocomplete(player=joueur_autocomplete, tranche=tranche_autocomplete)
    @app_commands.choices(
        statistic=[
            app_commands.Choice(name="Achievement points", value="achievements"),
            app_commands.Choice(name="Plunder points", value="plunder"),
            app_commands.Choice(name="Honor points", value="honor"),
            app_commands.Choice(name="Might points", value="might"),
            app_commands.Choice(name="Building Might", value="master"),
            app_commands.Choice(name="Legendary level", value="legendary"),
        ]
    )
    async def classement_statistics(
        self,
        interaction: discord.Interaction,
        statistic: str,
        player: str = None,
        rank: int = None,
        tranche: int = None,
    ):
        try:
            await interaction.response.defer(thinking=True)
        except Exception as e:
            self.logger.warning(f"❌ [Classement] Defer failed sur /{interaction.command.name}: {e}")
            return

        try:
            langue, serveur_local = await get_server_config(interaction)
            serveur_api = self.get_serveur_api(serveur_local)
            event_id = EVENT_IDS.get(statistic, 1)

            STAT_MAP = {
                "achievements": {
                    "name": t(langue, "cal_stat_achievements", defaut="Points de Succès"),
                    "emoji": get_emo(langue, "{e_std_trophy}"),
                    "color": discord.Color(0xF1C40F),
                },
                "plunder": {
                    "name": t(langue, "cal_stat_plunder", defaut="Points de Pillage"),
                    "emoji": get_emo(langue, "{e_loot}"),
                    "color": discord.Color(0xD35400),
                },
                "honor": {
                    "name": t(langue, "cal_stat_honor", defaut="Points d'Honneur"),
                    "emoji": get_emo(langue, "{e_honor}"),
                    "color": discord.Color(0xF8F9FA),
                },
                "might": {
                    "name": t(langue, "cal_stat_might", defaut="Points de Puissance"),
                    "emoji": get_emo(langue, "{e_pp1}"),
                    "color": discord.Color(0xC0392B),
                },
                "master": {
                    "name": t(langue, "cal_stat_master", defaut="Puissance de Construction"),
                    "emoji": get_emo(langue, "{e_dungeon1}"),
                    "color": discord.Color(0xE74C3C),
                },
                "legendary": {
                    "name": t(langue, "cal_stat_legendary", defaut="Niveau Légendaire"),
                    "emoji": get_emo(langue, "{e_lvl}"),
                    "color": discord.Color(0x5BC0BE),
                },
            }
            stat_info = STAT_MAP.get(
                statistic,
                {"name": statistic.capitalize(), "emoji": get_emo(langue, "{e_stats}"), "color": discord.Color.gold()},
            )

            if statistic in ["plunder", "legendary"]:
                search_lids = [1]
                default_lid = 1
            elif tranche is not None:
                search_lids = [tranche]
                default_lid = tranche
            else:
                search_lids = [6, 5, 4, 3, 2, 1]
                default_lid = 6

            l_chunk, found_lid, player_found, anchor_sv, total_ranks, api_err = await self.resolve_initial_ranking(
                serveur_api=serveur_api,
                event_id=event_id,
                search_lids=search_lids,
                default_lid=default_lid,
                search_target=player,
                target_rank=rank,
            )

            if api_err:
                msg_err = t(
                    langue,
                    "api_unavailable_error",
                    defaut="⚠️ **L'API de classement est actuellement indisponible ou trop lente.**\nVeuillez réessayer dans quelques instants.",
                )
                return await interaction.followup.send(msg_err)

            if not l_chunk:
                return await interaction.followup.send(
                    t(
                        langue,
                        "ev_rank_empty",
                        tranche="Tranche requise",
                        ev=stat_info["name"],
                        defaut="{e_error} Aucun classement.",
                    )
                )

            bracket_icon = (
                get_emo(langue, "{e_icon_points}")
                if statistic in ["plunder", "legendary"]
                else get_emo(langue, "{e_lvl}")
            )
            if statistic in ["plunder", "legendary"]:
                nom_tranche = t(langue, "ev_bracket_all", defaut="Global Ranking")
            else:
                STAT_BRACKET_NAMES = {
                    6: "Levels 70+",
                    5: "Levels 50-69",
                    4: "Levels 40-49",
                    3: "Levels 30-39",
                    2: "Levels 20-29",
                    1: "Levels 1-19",
                }
                nom_tranche_defaut = STAT_BRACKET_NAMES.get(found_lid, f"Bracket {found_lid}")
                nom_tranche = t(langue, f"stat_bracket_{found_lid}", defaut=nom_tranche_defaut)

            mot_classement = t(langue, "ev_word_rank", defaut="Ranking")
            titre = f"{stat_info['emoji']} {mot_classement} {stat_info['name']}\n{bracket_icon} {nom_tranche}"

            warning_msg = ""
            if rank and not tranche and not player and statistic not in ["plunder", "legendary"]:
                warning_msg += "\n" + t(
                    langue,
                    "info_max_bracket",
                    defaut="*💡 Info: Maximum bracket displayed by default. Use the `bracket` option to change.*",
                )
            if player and not player_found:
                warning_msg += self._build_not_found_warning(langue, player, rank, is_alliance=False)

            embed, step = await self.build_single_page_embed(
                ctx_int=interaction,
                raw_list=l_chunk,
                langue=langue,
                titre=titre,
                color=stat_info["color"],
                is_alliance=False,
                highlight_player=player if player_found else None,
                event_name=statistic,
                total_ranks=total_ranks,
                warning_msg=warning_msg,
            )

            view = LiveRankingView(
                cog=self,
                interaction=interaction,
                langue=langue,
                serveur_api=serveur_api,
                event_id=event_id,
                lid=found_lid,
                titre=titre,
                embed_color=stat_info["color"],
                event_name=statistic,
                is_alliance=False,
                page_step=step,
                initial_sv=anchor_sv,
                total_ranks=total_ranks,
                highlight_player=player if player_found else None,
                initial_embed=embed,
            )
            view.message = await interaction.followup.send(embed=embed, view=view, wait=True)

        except Exception as e:
            self.logger.error(f"❌ [Classement] Erreur sur /rank statistics : {e}")
            obs.record_error(source="command", scope="rank_statistics", exception=e, cog="classement")

    @classement.command(name="event", description="Displays an event's live ranking.")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.describe(
        evenement="Event", player="Player (Optional)", rank="Rank (Optional)", tranche="Bracket (Optional)"
    )
    @app_commands.autocomplete(player=joueur_autocomplete, tranche=tranche_autocomplete)
    @app_commands.choices(
        evenement=[
            app_commands.Choice(name="Nomad Invasion", value="nomads"),
            app_commands.Choice(name="War of the Realms", value="foreigners"),
            app_commands.Choice(name="Samurai Invasion", value="samurais"),
            app_commands.Choice(name="Bloodcrow Invasion", value="bloodcrows"),
            app_commands.Choice(name="Battle of Berimond", value="berimond"),
        ]
    )
    async def classement_joueur(
        self,
        interaction: discord.Interaction,
        evenement: str,
        player: str = None,
        rank: int = None,
        tranche: int = None,
    ):
        try:
            await interaction.response.defer(thinking=True)
        except Exception as e:
            self.logger.warning(f"❌ [Classement] Defer failed sur /{interaction.command.name}: {e}")
            return

        try:
            langue, serveur_local = await get_server_config(interaction)
            serveur_api = self.get_serveur_api(serveur_local)
            event_id = EVENT_IDS.get(evenement, 30 if evenement == "berimond" else 44)

            EVENT_MAP = {
                "nomads": {
                    "name": t(langue, "cal_ev_nomad", defaut="Invasion des Nomades"),
                    "emoji": get_emo(langue, "{e_nomads}"),
                    "color": discord.Color(0xEDCB5A),
                },
                "foreigners": {
                    "name": t(langue, "cal_ev_realms", defaut="Guerre des Royaumes"),
                    "emoji": get_emo(langue, "{e_war_realms}"),
                    "color": discord.Color(0x6C5CE7),
                },
                "samurais": {
                    "name": t(langue, "cal_ev_samurai", defaut="Invasion des Samouraïs"),
                    "emoji": get_emo(langue, "{e_samurai}"),
                    "color": discord.Color(0xD43C27),
                },
                "bloodcrows": {
                    "name": t(langue, "cal_ev_bloodcrow", defaut="Corbeaux de Sang"),
                    "emoji": get_emo(langue, "{e_bloodcrow}"),
                    "color": discord.Color(0x8B001A),
                },
                "berimond": {
                    "name": t(langue, "cal_ev_berimond", defaut="Bataille de Bérimond"),
                    "emoji": get_emo(langue, "{e_berimond}"),
                    "color": discord.Color(0x0094FF),
                },
            }
            ev_info = EVENT_MAP.get(
                evenement,
                {
                    "name": evenement.capitalize(),
                    "emoji": get_emo(langue, "{e_events4}"),
                    "color": discord.Color(0x6C5CE7),
                },
            )

            berimond_both = False
            if tranche is not None:
                search_lids = [tranche]
                default_lid = tranche
            elif evenement == "berimond":
                search_lids = [3, 4, 1, 2]
                default_lid = 3
                if not player:
                    berimond_both = True
            else:
                search_lids = [5, 4, 3, 2, 1]
                default_lid = 5

            l_chunk, found_lid, player_found, anchor_sv, total_ranks, api_err = await self.resolve_initial_ranking(
                serveur_api=serveur_api,
                event_id=event_id,
                search_lids=search_lids,
                default_lid=default_lid,
                search_target=player,
                target_rank=rank,
                berimond_both_camps=berimond_both,
            )

            if api_err:
                msg_err = t(
                    langue,
                    "api_unavailable_error",
                    defaut="⚠️ **L'API de classement est actuellement indisponible ou trop lente.**\nVeuillez réessayer dans quelques instants.",
                )
                return await interaction.followup.send(msg_err)

            if not l_chunk:
                return await interaction.followup.send(
                    t(
                        langue,
                        "ev_rank_empty",
                        tranche="Tranche requise",
                        ev=ev_info["name"],
                        defaut="{e_error} Aucun classement.",
                    )
                )

            mot_classement = t(langue, "ev_word_rank", defaut="Ranking")
            if evenement == "berimond":
                c1 = t(langue, "camp_ursidae", defaut="Ursidae Camp")
                c2 = t(langue, "camp_gerbrandt", defaut="Gerbrandt Camp")
                l1 = t(langue, "lvl_40_69", defaut="Levels 40-69")
                l2 = t(langue, "lvl_70_plus", defaut="Legendary (70+)")
                BERIMOND_DETAILS = {1: (c1, l1), 2: (c2, l1), 3: (c1, l2), 4: (c2, l2)}
                camp_txt, lvl_txt = BERIMOND_DETAILS.get(found_lid, (c1, l2))
                if berimond_both:
                    camp_txt = t(langue, "camp_both", defaut="Both camps")
                    embed_color = discord.Color(0x5865F2)
                else:
                    embed_color = discord.Color(0x0094FF) if found_lid in [1, 3] else discord.Color(0xE74C3C)
                titre = f"{get_emo(langue, '{e_berimond}')} {t(langue, 'cal_ev_berimond', defaut='Battle of Berimond')} — {camp_txt}\n{get_emo(langue, '{e_lvl}')} {lvl_txt}"
            else:
                nom_tranche_defaut = BRACKETS.get(str(found_lid), f"Bracket {found_lid}")
                nom_tranche = t(langue, f"ev_bracket_{found_lid}", defaut=nom_tranche_defaut)
                titre = (
                    f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n{get_emo(langue, '{e_lvl}')} {nom_tranche}"
                )
                embed_color = ev_info["color"]

            warning_msg = ""
            if rank and not tranche and not player:
                warning_msg += "\n" + t(
                    langue,
                    "info_max_bracket",
                    defaut="*💡 Info: Maximum bracket displayed by default. Use the `bracket` option to change.*",
                )
            if player and not player_found:
                warning_msg += self._build_not_found_warning(langue, player, rank, is_alliance=False)

            embed, step = await self.build_single_page_embed(
                ctx_int=interaction,
                raw_list=l_chunk,
                langue=langue,
                titre=titre,
                color=embed_color,
                is_alliance=False,
                highlight_player=player if player_found else None,
                event_name=evenement,
                total_ranks=total_ranks,
                warning_msg=warning_msg,
            )

            view = LiveRankingView(
                cog=self,
                interaction=interaction,
                langue=langue,
                serveur_api=serveur_api,
                event_id=event_id,
                lid=found_lid,
                titre=titre,
                embed_color=embed_color,
                event_name=evenement,
                is_alliance=False,
                page_step=step,
                initial_sv=anchor_sv,
                total_ranks=total_ranks,
                highlight_player=player if player_found else None,
                initial_embed=embed,
                berimond_both_camps=berimond_both,
            )
            view.message = await interaction.followup.send(embed=embed, view=view, wait=True)

        except Exception as e:
            self.logger.error(f"❌ [Classement] Erreur sur /rank event : {e}")
            obs.record_error(source="command", scope="rank_event", exception=e, cog="classement")

    @classement.command(name="gacha", description="Displays a live ranking for Gacha events")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.describe(evenement="Event", player="Player (Optional)", rank="Rank (Optional)")
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.choices(
        evenement=[
            app_commands.Choice(name="Fatal Flora Trap", value="flora"),
            app_commands.Choice(name="The Enchanted Snowglobe", value="snowglobe"),
            app_commands.Choice(name="Summoning of the Hollow Moon", value="hollowmoon"),
            app_commands.Choice(name="Sand of Fortune", value="sandfortune"),
            app_commands.Choice(name="King's Banquet", value="banquet"),
            app_commands.Choice(name="Midnight Market", value="midnight"),
        ]
    )
    async def classement_gacha(
        self, interaction: discord.Interaction, evenement: str, player: str = None, rank: int = None
    ):
        try:
            await interaction.response.defer(thinking=True)
        except Exception as e:
            self.logger.warning(f"❌ [Classement] Defer failed sur /{interaction.command.name}: {e}")
            return

        try:
            langue, serveur_local = await get_server_config(interaction)
            serveur_api = self.get_serveur_api(serveur_local)
            event_id = EVENT_IDS.get(evenement, 80)

            EVENT_MAP = {
                "flora": {
                    "name": t(langue, "cal_ev_flora", defaut="Piège de la Flore Fatale"),
                    "emoji": DICT_EMOJIS.get("e_FloraToken", "{e_floratoken}"),
                    "color": discord.Color(0x81C24A),
                },
                "snowglobe": {
                    "name": t(langue, "cal_ev_snowglobe", defaut="La Boule à Neige Enchantée"),
                    "emoji": DICT_EMOJIS.get("e_FrozenCarrot", "{e_frozencarrot}"),
                    "color": discord.Color(0xA5F3FC),
                },
                "hollowmoon": {
                    "name": t(langue, "cal_ev_hollowmoon", defaut="Invocation de Lune"),
                    "emoji": DICT_EMOJIS.get("e_Moonegg", "{e_moonegg}"),
                    "color": discord.Color(0xFF8D00),
                },
                "sandfortune": {
                    "name": t(langue, "cal_ev_sandfortune", defaut="Sables de Fortune"),
                    "emoji": DICT_EMOJIS.get("e_Orange", "{e_orange}"),
                    "color": discord.Color(0xFCD34D),
                },
                "banquet": {
                    "name": t(langue, "cal_ev_banquet", defaut="Banquet du Roi"),
                    "emoji": DICT_EMOJIS.get("e_Cake", "{e_cake}"),
                    "color": discord.Color(0xF59E0B),
                },
                "midnight": {
                    "name": t(langue, "cal_ev_midnight", defaut="Marché de Minuit"),
                    "emoji": DICT_EMOJIS.get("e_Midnight_key", "{e_midnight_key}"),
                    "color": discord.Color(0x4F46E5),
                },
            }
            ev_info = EVENT_MAP.get(
                evenement,
                {
                    "name": evenement.capitalize(),
                    "emoji": DICT_EMOJIS.get("e_gacha_currency", "🪙"),
                    "color": discord.Color.gold(),
                },
            )

            l_chunk, found_lid, player_found, anchor_sv, total_ranks, api_err = await self.resolve_initial_ranking(
                serveur_api=serveur_api,
                event_id=event_id,
                search_lids=[1],
                default_lid=1,
                search_target=player,
                target_rank=rank,
            )

            if api_err:
                msg_err = t(
                    langue,
                    "api_unavailable_error",
                    defaut="⚠️ **L'API de classement est actuellement indisponible ou trop lente.**\nVeuillez réessayer dans quelques instants.",
                )
                return await interaction.followup.send(msg_err)

            if not l_chunk:
                return await interaction.followup.send(
                    t(
                        langue,
                        "ev_rank_empty",
                        tranche="Global",
                        ev=ev_info["name"],
                        defaut="{e_error} Aucun classement.",
                    )
                )

            nom_tranche = t(langue, "ev_bracket_all", defaut="Global Ranking")
            mot_classement = t(langue, "ev_word_rank", defaut="Ranking")
            bracket_icon = get_emo(langue, "{e_icon_points}")
            titre = f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n{bracket_icon} {nom_tranche}"

            warning_msg = ""
            if player and not player_found:
                warning_msg += self._build_not_found_warning(langue, player, rank, is_alliance=False)

            embed, step = await self.build_single_page_embed(
                ctx_int=interaction,
                raw_list=l_chunk,
                langue=langue,
                titre=titre,
                color=ev_info["color"],
                is_alliance=False,
                highlight_player=player if player_found else None,
                event_name=evenement,
                total_ranks=total_ranks,
                warning_msg=warning_msg,
            )

            view = LiveRankingView(
                cog=self,
                interaction=interaction,
                langue=langue,
                serveur_api=serveur_api,
                event_id=event_id,
                lid=1,
                titre=titre,
                embed_color=ev_info["color"],
                event_name=evenement,
                is_alliance=False,
                page_step=step,
                initial_sv=anchor_sv,
                total_ranks=total_ranks,
                highlight_player=player if player_found else None,
                initial_embed=embed,
            )
            view.message = await interaction.followup.send(embed=embed, view=view, wait=True)

        except Exception as e:
            self.logger.error(f"❌ [Classement] Erreur sur /rank gacha : {e}")
            obs.record_error(source="command", scope="rank_gacha", exception=e, cog="classement")

    @classement.command(name="realms", description="Displays a ranking for cross-server events")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.describe(evenement="Event", player="Player (Optional)", rank="Rank (Optional)")
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.choices(
        evenement=[
            app_commands.Choice(name="Outer Realms (Current)", value="realms_current"),
            app_commands.Choice(name="Outer Realms (Finished)", value="realms_finished"),
            app_commands.Choice(name="Beyond the Horizon", value="horizon"),
        ]
    )
    async def classement_realms(
        self, interaction: discord.Interaction, evenement: str, player: str = None, rank: int = None
    ):
        try:
            await interaction.response.defer(thinking=True)
        except Exception as e:
            self.logger.warning(f"❌ [Classement] Defer failed sur /{interaction.command.name}: {e}")
            return

        try:
            langue, serveur_local = await get_server_config(interaction)
            serveur_api = self.get_serveur_api(serveur_local)

            if evenement == "realms_current":
                event_id = 62
            elif evenement == "realms_finished":
                event_id = EVENT_IDS.get("realms", 76)
            else:
                event_id = EVENT_IDS.get("horizon", 78)

            EVENT_MAP = {
                "realms_current": {
                    "name": t(langue, "cal_ev_realms_current", defaut="Royaumes Extérieurs"),
                    "emoji": get_emo(langue, "{e_war_realms}"),
                    "color": discord.Color(0xF25500),
                },
                "realms_finished": {
                    "name": t(langue, "cal_ev_realms_finished", defaut="Royaumes Extérieurs"),
                    "emoji": get_emo(langue, "{e_war_realms}"),
                    "color": discord.Color(0xF25500),
                },
                "horizon": {
                    "name": t(langue, "cal_ev_horizon", defaut="Au-delà de l'Horizon"),
                    "emoji": get_emo(langue, "{e_std_world_map}"),
                    "color": discord.Color(0x3B8EA5),
                },
            }
            ev_info = EVENT_MAP.get(
                evenement,
                {
                    "name": evenement.capitalize(),
                    "emoji": DICT_EMOJIS.get("e_events4", "🛡️"),
                    "color": discord.Color(0xF25500),
                },
            )

            suffixe = serveur_local.replace("E4K_", "").replace("e4k_", "").upper()

            l_chunk, found_lid, player_found, anchor_sv, total_ranks, api_err = await self.resolve_initial_ranking(
                serveur_api=serveur_api,
                event_id=event_id,
                search_lids=[1],
                default_lid=1,
                search_target=player,
                target_rank=rank,
                cross_server_suffix=suffixe,
            )

            if api_err:
                msg_err = t(
                    langue,
                    "api_unavailable_error",
                    defaut="⚠️ **L'API de classement est actuellement indisponible ou trop lente.**\nVeuillez réessayer dans quelques instants.",
                )
                return await interaction.followup.send(msg_err)

            if not l_chunk:
                return await interaction.followup.send(
                    t(
                        langue,
                        "ev_rank_empty",
                        tranche="Inter-Serveurs",
                        ev=ev_info["name"],
                        defaut="{e_error} Aucun classement.",
                    )
                )

            nom_tranche = t(langue, "ev_bracket_cross_server", defaut="Cross-Server Ranking")
            mot_classement = t(langue, "ev_word_rank", defaut="Ranking")
            bracket_icon = get_emo(langue, "{e_icon_points}")
            titre = f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n{bracket_icon} {nom_tranche}"

            warning_msg = ""
            if player and not player_found:
                warning_msg += self._build_not_found_warning(langue, player, rank, is_alliance=False)

            embed, step = await self.build_single_page_embed(
                ctx_int=interaction,
                raw_list=l_chunk,
                langue=langue,
                titre=titre,
                color=ev_info["color"],
                is_alliance=False,
                highlight_player=player if player_found else None,
                event_name=evenement,
                total_ranks=total_ranks,
                warning_msg=warning_msg,
            )

            view = LiveRankingView(
                cog=self,
                interaction=interaction,
                langue=langue,
                serveur_api=serveur_api,
                event_id=event_id,
                lid=1,
                titre=titre,
                embed_color=ev_info["color"],
                event_name=evenement,
                is_alliance=False,
                page_step=step,
                initial_sv=anchor_sv,
                total_ranks=total_ranks,
                highlight_player=player if player_found else None,
                initial_embed=embed,
            )
            view.message = await interaction.followup.send(embed=embed, view=view, wait=True)

        except Exception as e:
            self.logger.error(f"❌ [Classement] Erreur sur /rank realms : {e}")
            obs.record_error(source="command", scope="rank_realms", exception=e, cog="classement")

    @classement.command(name="league", description="Displays a ranking for Season and League events")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.describe(
        evenement="Event", player="Player (Optional)", rank="Rank (Optional)", tranche="Bracket (Optional)"
    )
    @app_commands.autocomplete(player=joueur_autocomplete, tranche=tranche_autocomplete)
    @app_commands.choices(
        evenement=[
            app_commands.Choice(name="Season / Festival", value="season"),
            app_commands.Choice(name="Kingdom League", value="league"),
        ]
    )
    async def classement_league(
        self,
        interaction: discord.Interaction,
        evenement: str,
        player: str = None,
        rank: int = None,
        tranche: int = None,
    ):
        try:
            await interaction.response.defer(thinking=True)
        except Exception as e:
            self.logger.warning(f"❌ [Classement] Defer failed sur /{interaction.command.name}: {e}")
            return

        try:
            langue, serveur_local = await get_server_config(interaction)
            serveur_api = self.get_serveur_api(serveur_local)
            event_id = EVENT_IDS.get(evenement, 53)

            EVENT_MAP = {
                "season": {
                    "name": t(langue, "cal_ev_season", defaut="Saison / Festival"),
                    "emoji": get_emo(langue, "{e_std_trophy}"),
                    "color": discord.Color(0xC084FC),
                },
                "league": {
                    "name": t(langue, "cal_ev_league", defaut="Ligue du Royaume"),
                    "emoji": get_emo(langue, "{e_empirerankings}"),
                    "color": discord.Color(0x008B45),
                },
            }
            ev_info = EVENT_MAP.get(
                evenement,
                {
                    "name": evenement.capitalize(),
                    "emoji": DICT_EMOJIS.get("e_leagueicon", "🛡️"),
                    "color": discord.Color(0x008B45),
                },
            )

            api_command = "llsp" if evenement == "season" else "hgh"
            pagination_param = "R" if evenement == "season" else "SV"
            is_string_val = evenement != "season"

            if evenement == "league":
                search_lids = [1]
                default_lid = 1
            elif tranche is not None:
                search_lids = [tranche]
                default_lid = tranche
            else:
                search_lids = [4, 3, 2, 1]
                default_lid = 4

            l_chunk, found_lid, player_found, anchor_sv, total_ranks, api_err = await self.resolve_initial_ranking(
                serveur_api=serveur_api,
                event_id=event_id,
                search_lids=search_lids,
                default_lid=default_lid,
                search_target=player,
                target_rank=rank,
                api_command=api_command,
                pagination_param=pagination_param,
                is_string_val=is_string_val,
            )

            if api_err:
                msg_err = t(
                    langue,
                    "api_unavailable_error",
                    defaut="⚠️ **L'API de classement est actuellement indisponible ou trop lente.**\nVeuillez réessayer dans quelques instants.",
                )
                return await interaction.followup.send(msg_err)

            if not l_chunk:
                return await interaction.followup.send(
                    t(
                        langue,
                        "ev_rank_empty",
                        tranche="Tranche requise",
                        ev=ev_info["name"],
                        defaut="{e_error} Aucun classement.",
                    )
                )

            if evenement == "league":
                nom_tranche = t(langue, "ev_bracket_all", defaut="Global Ranking")
            else:
                SEASON_BRACKET_NAMES = {
                    4: "Legendary 950+",
                    3: "Legendary 650-949",
                    2: "Legendary 200-649",
                    1: "Legendary 1-199",
                }
                nom_tranche_defaut = SEASON_BRACKET_NAMES.get(found_lid, f"Bracket {found_lid}")
                nom_tranche = t(langue, f"season_bracket_{found_lid}", defaut=nom_tranche_defaut)

            bracket_icon = get_emo(langue, "{e_lvl}")
            mot_classement = t(langue, "ev_word_rank", defaut="Ranking")
            titre = f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n{bracket_icon} {nom_tranche}"

            warning_msg = ""
            if rank and not tranche and not player and evenement == "season":
                warning_msg += "\n" + t(
                    langue,
                    "info_max_bracket",
                    defaut="*💡 Info: Maximum bracket displayed by default. Use the `bracket` option to change.*",
                )
            if player and not player_found:
                warning_msg += self._build_not_found_warning(langue, player, rank, is_alliance=False)

            embed, step = await self.build_single_page_embed(
                ctx_int=interaction,
                raw_list=l_chunk,
                langue=langue,
                titre=titre,
                color=ev_info["color"],
                is_alliance=False,
                highlight_player=player if player_found else None,
                event_name=evenement,
                total_ranks=total_ranks,
                warning_msg=warning_msg,
            )

            view = LiveRankingView(
                cog=self,
                interaction=interaction,
                langue=langue,
                serveur_api=serveur_api,
                event_id=event_id,
                lid=found_lid,
                titre=titre,
                embed_color=ev_info["color"],
                event_name=evenement,
                is_alliance=False,
                api_command=api_command,
                pagination_param=pagination_param,
                is_string_val=is_string_val,
                page_step=step,
                initial_sv=anchor_sv,
                total_ranks=total_ranks,
                highlight_player=player if player_found else None,
                initial_embed=embed,
            )
            view.message = await interaction.followup.send(embed=embed, view=view, wait=True)

        except Exception as e:
            self.logger.error(f"❌ [Classement] Erreur sur /rank league : {e}")
            obs.record_error(source="command", scope="rank_league", exception=e, cog="classement")

    @classement.command(name="contests", description="Displays a ranking for specific contests")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.describe(
        evenement="Event", player="Player (Optional)", rank="Rank (Optional)", tranche="Bracket (Optional)"
    )
    @app_commands.autocomplete(player=joueur_autocomplete, tranche=tranche_autocomplete)
    @app_commands.choices(
        evenement=[
            app_commands.Choice(name="Shapeshifters", value="shapeshifters"),
            app_commands.Choice(name="Nobility Contest", value="nobility"),
            app_commands.Choice(name="Wheel of Unimaginable Affluence", value="woa"),
            app_commands.Choice(name="Imperial patronage", value="patronage"),
        ]
    )
    async def classement_contests(
        self,
        interaction: discord.Interaction,
        evenement: str,
        player: str = None,
        rank: int = None,
        tranche: int = None,
    ):
        try:
            await interaction.response.defer(thinking=True)
        except Exception as e:
            self.logger.warning(f"❌ [Classement] Defer failed sur /{interaction.command.name}: {e}")
            return

        try:
            langue, serveur_local = await get_server_config(interaction)
            serveur_api = self.get_serveur_api(serveur_local)
            event_id = EVENT_IDS.get(evenement, 60)

            EVENT_MAP = {
                "shapeshifters": {
                    "name": t(langue, "cal_ev_shape", defaut="Les Métamorphes"),
                    "emoji": DICT_EMOJIS.get("e_shapeshifter", "👹"),
                    "color": discord.Color(0x8E44AD),
                },
                "nobility": {
                    "name": t(langue, "cal_ev_nobility", defaut="Concours de Noblesse"),
                    "emoji": get_emo(langue, "{e_std_crown}"),
                    "color": discord.Color(0xF1C40F),
                },
                "woa": {
                    "name": t(langue, "cal_ev_woa", defaut="Guerre des Alliances"),
                    "emoji": get_emo(langue, "{e_woa_points}"),
                    "color": discord.Color(0x2ECC71),
                },
                "patronage": {
                    "name": t(langue, "cal_ev_patronage", defaut="Patronage"),
                    "emoji": DICT_EMOJIS.get("e_patronage", "🪙"),
                    "color": discord.Color(0xE67E22),
                },
            }
            ev_info = EVENT_MAP.get(
                evenement,
                {
                    "name": evenement.capitalize(),
                    "emoji": DICT_EMOJIS.get("e_events4", "🏆"),
                    "color": discord.Color.gold(),
                },
            )

            if evenement in ["woa", "patronage"]:
                search_lids = [1]
                default_lid = 1
            elif tranche is not None:
                search_lids = [tranche]
                default_lid = tranche
            elif evenement == "shapeshifters":
                search_lids = [5, 4, 3, 2, 1]
                default_lid = 5
            else:
                search_lids = [7, 6, 5, 4, 3, 2, 1]
                default_lid = 7

            l_chunk, found_lid, player_found, anchor_sv, total_ranks, api_err = await self.resolve_initial_ranking(
                serveur_api=serveur_api,
                event_id=event_id,
                search_lids=search_lids,
                default_lid=default_lid,
                search_target=player,
                target_rank=rank,
            )

            if api_err:
                msg_err = t(
                    langue,
                    "api_unavailable_error",
                    defaut="⚠️ **L'API de classement est actuellement indisponible ou trop lente.**\nVeuillez réessayer dans quelques instants.",
                )
                return await interaction.followup.send(msg_err)

            if not l_chunk:
                return await interaction.followup.send(
                    t(
                        langue,
                        "ev_rank_empty",
                        tranche="Tranche requise",
                        ev=ev_info["name"],
                        defaut="{e_error} Aucun classement.",
                    )
                )

            if evenement in ["woa", "patronage"]:
                nom_tranche = t(langue, "ev_bracket_all", defaut="Global Ranking")
                bracket_icon = get_emo(langue, "{e_events4}")
            elif evenement == "shapeshifters":
                nom_tranche_defaut = BRACKETS.get(str(found_lid), f"Bracket {found_lid}")
                nom_tranche = t(langue, f"ev_bracket_{found_lid}", defaut=nom_tranche_defaut)
                bracket_icon = get_emo(langue, "{e_lvl}")
            else:
                NOBILITY_BRACKET_NAMES = {
                    7: "Legendary (70+)",
                    6: "Levels 50-69",
                    5: "Levels 30-49",
                    4: "Levels 25-29",
                    3: "Levels 20-24",
                    2: "Levels 15-19",
                    1: "Levels 10-14",
                }
                nom_tranche_defaut = NOBILITY_BRACKET_NAMES.get(found_lid, f"Bracket {found_lid}")
                nom_tranche = t(langue, f"nobility_bracket_{found_lid}", defaut=nom_tranche_defaut)
                bracket_icon = get_emo(langue, "{e_lvl}")

            mot_classement = t(langue, "ev_word_rank", defaut="Ranking")
            titre = f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n{bracket_icon} {nom_tranche}"

            warning_msg = ""
            if rank and not tranche and not player and evenement not in ["woa", "patronage"]:
                warning_msg += "\n" + t(
                    langue,
                    "info_max_bracket",
                    defaut="*💡 Info: Maximum bracket displayed by default. Use the `bracket` option to change.*",
                )
            if player and not player_found:
                warning_msg += self._build_not_found_warning(langue, player, rank, is_alliance=False)

            embed_color = ev_info["color"]
            embed, step = await self.build_single_page_embed(
                ctx_int=interaction,
                raw_list=l_chunk,
                langue=langue,
                titre=titre,
                color=embed_color,
                is_alliance=False,
                highlight_player=player if player_found else None,
                event_name=evenement,
                total_ranks=total_ranks,
                warning_msg=warning_msg,
            )

            view = LiveRankingView(
                cog=self,
                interaction=interaction,
                langue=langue,
                serveur_api=serveur_api,
                event_id=event_id,
                lid=found_lid,
                titre=titre,
                embed_color=embed_color,
                event_name=evenement,
                is_alliance=False,
                page_step=step,
                initial_sv=anchor_sv,
                total_ranks=total_ranks,
                highlight_player=player if player_found else None,
                initial_embed=embed,
            )
            view.message = await interaction.followup.send(embed=embed, view=view, wait=True)

        except Exception as e:
            self.logger.error(f"❌ [Classement] Erreur sur /rank contests : {e}")
            obs.record_error(source="command", scope="rank_contests", exception=e, cog="classement")

    @classement.command(name="alliance", description="Displays live rankings and statistics for alliances")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.describe(categorie="Category", alliance_name="Alliance name (Optional)", rank="Rank (Optional)")
    @app_commands.choices(
        categorie=[
            app_commands.Choice(name="Honor Points", value="alliance_honor"),
            app_commands.Choice(name="Might Points", value="alliance_might"),
            app_commands.Choice(name="Command Points", value="alliance_command"),
            app_commands.Choice(name="Cargo Points", value="alliance_cargo"),
            app_commands.Choice(name="Nomad Invasion", value="alliance_nomad"),
            app_commands.Choice(name="War of the Realms", value="alliance_foreigners"),
            app_commands.Choice(name="Samurai Invasion", value="alliance_samurais"),
            app_commands.Choice(name="Battle of Berimond", value="alliance_berimond"),
            app_commands.Choice(name="Bloodcrow Invasion", value="alliance_bloodcrows"),
            app_commands.Choice(name="Kingdom League", value="alliance_league"),
            app_commands.Choice(name="Beyond the Horizon", value="alliance_horizon"),
        ]
    )
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def classement_alliance(
        self, interaction: discord.Interaction, categorie: str, alliance_name: str = None, rank: int = None
    ):
        try:
            await interaction.response.defer(thinking=True)
        except Exception as e:
            self.logger.warning(f"❌ [Classement] Defer failed sur /{interaction.command.name}: {e}")
            return

        try:
            langue, serveur_local = await get_server_config(interaction)
            serveur_api = self.get_serveur_api(serveur_local)

            ALLIANCE_LT_MAP = {
                "alliance_honor": 10,
                "alliance_might": 11,
                "alliance_command": 12,
                "alliance_cargo": 13,
                "alliance_foreigners": 45,
                "alliance_nomad": 47,
                "alliance_samurais": 52,
                "alliance_berimond": 56,
                "alliance_bloodcrows": 59,
                "alliance_league": 67,
                "alliance_horizon": 77,
            }
            event_id = ALLIANCE_LT_MAP.get(categorie, EVENT_IDS.get(categorie, 10))

            CAT_MAP = {
                "alliance_honor": {
                    "name": t(langue, "cal_stat_honor", defaut="Points d'Honneur"),
                    "emoji": get_emo(langue, "{e_honor}"),
                    "color": discord.Color(0xF8F9FA),
                },
                "alliance_might": {
                    "name": t(langue, "cal_stat_might", defaut="Points de Puissance"),
                    "emoji": get_emo(langue, "{e_pp2}"),
                    "color": discord.Color(0xC0392B),
                },
                "alliance_command": {
                    "name": t(langue, "cal_stat_command", defaut="Points de Commandement"),
                    "emoji": DICT_EMOJIS.get("e_alliance_icon", "🛡️"),
                    "color": discord.Color(0x95A5A6),
                },
                "alliance_cargo": {
                    "name": t(langue, "cal_stat_cargo", defaut="Points de Fret"),
                    "emoji": get_emo(langue, "{e_pointscargo}"),
                    "color": discord.Color(0x38BDF8),
                },
                "alliance_nomad": {
                    "name": t(langue, "cal_ev_nomad", defaut="Invasion des Nomades"),
                    "emoji": get_emo(langue, "{e_nomads}"),
                    "color": discord.Color(0xEDCB5A),
                },
                "alliance_foreigners": {
                    "name": t(langue, "cal_ev_realms", defaut="Guerre des Royaumes"),
                    "emoji": get_emo(langue, "{e_war_realms}"),
                    "color": discord.Color(0x6C5CE7),
                },
                "alliance_samurais": {
                    "name": t(langue, "cal_ev_samurai", defaut="Invasion des Samouraïs"),
                    "emoji": get_emo(langue, "{e_samurai}"),
                    "color": discord.Color(0xD43C27),
                },
                "alliance_berimond": {
                    "name": t(langue, "cal_ev_berimond", defaut="Bataille de Bérimond"),
                    "emoji": get_emo(langue, "{e_berimond}"),
                    "color": discord.Color(0x0094FF),
                },
                "alliance_bloodcrows": {
                    "name": t(langue, "cal_ev_bloodcrow", defaut="Corbeaux de Sang"),
                    "emoji": get_emo(langue, "{e_bloodcrow}"),
                    "color": discord.Color(0x8B001A),
                },
                "alliance_league": {
                    "name": t(langue, "cal_ev_league", defaut="Ligue du Royaume"),
                    "emoji": get_emo(langue, "{e_std_trophy}"),
                    "color": discord.Color(0x008B45),
                },
                "alliance_horizon": {
                    "name": t(langue, "cal_ev_horizon", defaut="Au-delà de l'Horizon"),
                    "emoji": get_emo(langue, "{e_std_world_map}"),
                    "color": discord.Color(0x3B8EA5),
                },
            }
            cat_info = CAT_MAP.get(
                categorie,
                {
                    "name": categorie.capitalize(),
                    "emoji": get_emo(langue, "{e_alliance_icon}"),
                    "color": discord.Color.gold(),
                },
            )

            l_chunk, found_lid, alliance_found, anchor_sv, total_ranks, api_err = await self.resolve_initial_ranking(
                serveur_api=serveur_api,
                event_id=event_id,
                search_lids=[1],
                default_lid=1,
                search_target=alliance_name,
                target_rank=rank,
                is_alliance=True,
            )

            if api_err:
                msg_err = t(
                    langue,
                    "api_unavailable_error",
                    defaut="⚠️ **L'API de classement est actuellement indisponible ou trop lente.**\nVeuillez réessayer dans quelques instants.",
                )
                return await interaction.followup.send(msg_err)

            if not l_chunk:
                embed_empty = discord.Embed(
                    title=t(langue, "ev_rank_empty_title", defaut="📭 Ranking unavailable"),
                    description=t(
                        langue,
                        "ev_rank_empty",
                        ev=cat_info["name"],
                        defaut=f"No ranking found for **{cat_info['name']}**.",
                    ),
                    color=discord.Color.light_grey(),
                )
                return await interaction.followup.send(embed_empty)

            nom_tranche = t(langue, "ev_bracket_alliance", defaut="Global Alliance Ranking")
            mot_classement = t(langue, "ev_word_rank", defaut="Ranking")
            titre = f"{cat_info['emoji']} {mot_classement} {cat_info['name']}\n🛡️ {nom_tranche}"

            warning_msg = ""
            if alliance_name and not alliance_found:
                warning_msg += self._build_not_found_warning(langue, alliance_name, rank, is_alliance=True)

            embed, step = await self.build_single_page_embed(
                ctx_int=interaction,
                raw_list=l_chunk,
                langue=langue,
                titre=titre,
                color=cat_info["color"],
                is_alliance=True,
                highlight_player=alliance_name if alliance_found else None,
                event_name=categorie,
                total_ranks=total_ranks,
                warning_msg=warning_msg,
            )

            view = LiveRankingView(
                cog=self,
                interaction=interaction,
                langue=langue,
                serveur_api=serveur_api,
                event_id=event_id,
                lid=1,
                titre=titre,
                embed_color=cat_info["color"],
                event_name=categorie,
                is_alliance=True,
                page_step=step,
                initial_sv=anchor_sv,
                total_ranks=total_ranks,
                highlight_player=alliance_name if alliance_found else None,
                initial_embed=embed,
            )
            view.message = await interaction.followup.send(embed=embed, view=view, wait=True)

        except Exception as e:
            self.logger.error(f"❌ [Classement] Erreur sur /rank alliance : {e}")
            obs.record_error(source="command", scope="rank_alliance", exception=e, cog="classement")


async def setup(bot):
    await bot.add_cog(ClassementCog(bot))
