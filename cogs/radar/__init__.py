import logging
import urllib.parse

import discord
from discord import app_commands
from discord.ext import commands

from utils import (
    DICT_EMOJIS,
    PaginationView,
    alliance_autocomplete,
    find_player_in_cache,
    get_api_headers,
    get_server_config,
    joueur_autocomplete,
    load_surveillance_async,
    prompt_vote_if_lucky,
    save_surveillance_async,
    setup_embed_footer,
    t,
)

from .spy import RadarSpyMixin
from .views import RadarAllianceSettingsView, RadarSettingsView

logger = logging.getLogger("GGE_Bot")


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
class RadarCog(commands.GroupCog, RadarSpyMixin, group_name="radar", group_description="Personal and Server War Radar"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_scan_hour = None
        self.users_to_purge = set()
        self.guilds_to_purge = set()

        self.clr_add_joueur = discord.Color.from_rgb(255, 246, 143)
        self.clr_add_alliance = discord.Color.from_rgb(229, 221, 128)
        self.clr_list_alliances = discord.Color.from_rgb(183, 176, 102)
        self.clr_list_joueurs = discord.Color.from_rgb(128, 123, 71)

        self.users_lang_cache = {}
        self.users_cache_mtime = 0

        self.etags_cache = {}
        self.next_scan = {}
        self._serveur_courant = ""

        commands.GroupCog.__init__(self)

    async def cog_load(self):
        if not self.radar_spy_task.is_running():
            self.radar_spy_task.start()

    async def cog_unload(self):
        self.radar_spy_task.cancel()

    # ==========================================
    # 🏢 COMMANDES : RADAR SERVEUR (ADMINS)
    # ==========================================
    server_group = app_commands.Group(name="server", description="Configure the public server radar")

    @server_group.command(name="setup", description="Define the room where the radar will send public alerts")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(channel="Le salon cible", role="Role to mention (Optional)")
    async def srv_setup(
        self, interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role = None
    ):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            logger.warning(f"❌ Defer failed sur /{interaction.command.name} : {e}")
            return
        langue, serveur = await get_server_config(interaction)

        if not interaction.guild:
            msg = t(langue, "cmd_guild_only", defaut="❌ Cette commande ne peut être utilisée que sur un serveur.")
            return await interaction.followup.send(msg)

        data = await load_surveillance_async()
        guild_id = str(interaction.guild.id)

        if "guild_configs" not in data:
            data["guild_configs"] = {}

        ping_val = None
        if role:
            ping_val = "@everyone" if role.is_default() else role.mention

        data["guild_configs"][str(interaction.guild.id)] = {
            "channel_id": channel.id,
            "ping_role": ping_val,
            "langue": langue,
            "serveur": serveur,
        }
        await save_surveillance_async(data)

        e_check = DICT_EMOJIS.get("e_check", "✅")
        msg_success = t(
            langue,
            "rad_srv_setup_success",
            channel=channel.mention,
            defaut=f"{e_check} **Radar Serveur configuré !** Les alertes arriveront dans {channel.mention}.",
        )
        await interaction.followup.send(msg_success)

    @server_group.command(name="add_player", description="Add a player to the server radar (Limit: 15 followed)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def srv_add_player(self, interaction: discord.Interaction, player: str, reason: str = "Player monitoring"):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            logger.warning(f"❌ Defer failed sur /{interaction.command.name} : {e}")
            return
        langue, serveur = await get_server_config(interaction)

        if not interaction.guild:
            msg = t(langue, "cmd_guild_only", defaut="❌ Cette commande ne peut être utilisée que sur un serveur.")
            return await interaction.followup.send(msg)

        data = await load_surveillance_async()
        guild_id = str(interaction.guild.id)

        if guild_id not in data.get("guild_configs", {}):
            msg_err = t(langue, "rad_err_srv_not_setup", defaut="❌ Utilisez d'abord `/radar server setup`.")
            return await interaction.followup.send(msg_err)

        p_name_exact, p_info = await find_player_in_cache(serveur, player)
        if p_info:
            p_id = str(p_info.get("player_id", p_info.get("id", "")))
            p_might = int(p_info.get("main_points", 0))
            player = p_name_exact
        else:
            p_id, p_might = None, 0

        e_warning = DICT_EMOJIS.get("e_warning", "⚠️")
        e_nocheck = DICT_EMOJIS.get("e_nocheck", "❌")
        e_bullseye = DICT_EMOJIS.get("e_std_bullseye", "🎯")
        e_wink = DICT_EMOJIS.get("e_std_winking_face", "😉")

        if not p_id:
            msg_not_found = t(
                langue,
                "rad_err_not_found_cache",
                p=player,
                defaut=f"{e_warning} Joueur **{player}** introuvable dans le cache local.",
            )
            return await interaction.followup.send(msg_not_found)

        already_tracking = guild_id in data.get("players", {}).get(p_id, {}).get("guild_abonnes", {})
        if not already_tracking:
            count_tracked = sum(
                1 for p_info in data.get("players", {}).values() if guild_id in p_info.get("guild_abonnes", {})
            )
            if count_tracked >= 15:
                msg_limite = t(
                    langue,
                    "rad_err_srv_limit_player",
                    cnt=count_tracked,
                    defaut=f"{e_nocheck} **Limite atteinte** : Le serveur surveille déjà le maximum autorisé (`{count_tracked}/15` joueurs).",
                )
                return await interaction.followup.send(msg_limite)

        if p_id not in data.get("players", {}):
            if "players" not in data:
                data["players"] = {}
            now_str = discord.utils.utcnow().isoformat().replace("+00:00", "Z")
            data["players"][p_id] = {
                "name": player,
                "last_alliance": now_str,
                "last_name": now_str,
                "last_pos": now_str,
                "last_might": p_might,
                "is_protected": False,
                "abonnes": {},
                "guild_abonnes": {},
                "serveur": serveur,
            }

        if "guild_abonnes" not in data["players"][p_id]:
            data["players"][p_id]["guild_abonnes"] = {}

        if guild_id not in data["players"][p_id]["guild_abonnes"]:
            data["players"][p_id]["guild_abonnes"][guild_id] = {
                "raison": reason,
                "pseudo": False,
                "position": False,
                "alliance": False,
                "puissance": False,
                "colombe": False,
            }
            await save_surveillance_async(data)

            title_str = t(langue, "rad_srv_add_p_title", p=player, defaut=f"{e_bullseye} Cible Serveur : {player}")
            desc_str = t(
                langue,
                "rad_srv_add_p_desc",
                defaut="Ajouté au radar public.\n\n**Configure les alertes pour le serveur :**",
            )
            embed = discord.Embed(title=title_str, description=desc_str, color=self.clr_add_joueur)
            view = RadarSettingsView(
                p_id, guild_id, "guild", player, data["players"][p_id]["guild_abonnes"][guild_id], langue
            )
            await setup_embed_footer(embed, interaction, langue)
            await interaction.followup.send(embed=embed, view=view)
        else:
            msg_already = t(
                langue,
                "rad_err_srv_already_track",
                p=player,
                defaut=f"Le serveur surveille DÉJÀ **{player}** ! {e_wink}",
            )
            await interaction.followup.send(msg_already)

    @server_group.command(name="add_alliance", description="Add an alliance to the server radar (Limit: 3 followed)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def srv_add_alliance(
        self, interaction: discord.Interaction, alliance_name: str, reason: str = "Alliance monitoring"
    ):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            logger.warning(f"❌ Defer failed sur /{interaction.command.name} : {e}")
            return
        langue, serveur = await get_server_config(interaction)

        if not interaction.guild:
            msg = t(langue, "cmd_guild_only", defaut="❌ Cette commande ne peut être utilisée que sur un serveur.")
            return await interaction.followup.send(msg)

        data = await load_surveillance_async()
        guild_id = str(interaction.guild.id)

        if guild_id not in data.get("guild_configs", {}):
            msg_err = t(langue, "rad_err_srv_not_setup", defaut="❌ Utilisez d'abord `/radar server setup`.")
            return await interaction.followup.send(msg_err)

        headers = await get_api_headers(interaction)
        a_id, a_name_real, members_dict = None, alliance_name, {}

        e_warning = DICT_EMOJIS.get("e_warning", "⚠️")
        e_nocheck = DICT_EMOJIS.get("e_nocheck", "❌")
        e_alli = DICT_EMOJIS.get("e_alliance_icon", "🛡️")
        e_wink = DICT_EMOJIS.get("e_std_winking_face", "😉")

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
                        if isinstance(alli_data, list) and alli_data:
                            alli_data = alli_data[0]
                        members = alli_data.get("players", alli_data.get("members", alli_data.get("playerList", [])))
                        for m in members:
                            p_id = str(m.get("player_id", m.get("playerId", "")))
                            if p_id:
                                members_dict[p_id] = {
                                    "name": m.get("player_name", m.get("playerName", "Inconnu")),
                                    "rank": int(m.get("alliance_rank", 8)),
                                }
        except Exception as e:
            msg_api_err = t(
                langue,
                "rad_err_api_join",
                defaut=f"{e_nocheck} Impossible de joindre GGE-Tracker pour trouver cette alliance.",
            )
            return await interaction.followup.send(msg_api_err)

        if not a_id:
            msg_not_found = t(
                langue,
                "rad_err_alli_not_found",
                a=alliance_name,
                defaut=f"{e_warning} Alliance **{alliance_name}** introuvable.",
            )
            return await interaction.followup.send(msg_not_found)

        already_tracking = guild_id in data.get("alliances", {}).get(a_id, {}).get("guild_abonnes", {})
        if not already_tracking:
            count_tracked = sum(
                1 for a_info in data.get("alliances", {}).values() if guild_id in a_info.get("guild_abonnes", {})
            )
            if count_tracked >= 3:
                msg_limite = t(
                    langue,
                    "rad_err_srv_limit_alli",
                    cnt=count_tracked,
                    defaut=f"{e_nocheck} **Limite atteinte** : Le serveur surveille déjà le maximum autorisé (`{count_tracked}/3` alliances).",
                )
                return await interaction.followup.send(msg_limite)

        if a_id not in data.get("alliances", {}):
            if "alliances" not in data:
                data["alliances"] = {}
            data["alliances"][a_id] = {
                "name": a_name_real,
                "members": members_dict,
                "abonnes": {},
                "guild_abonnes": {},
                "serveur": serveur,
            }

        if "guild_abonnes" not in data["alliances"][a_id]:
            data["alliances"][a_id]["guild_abonnes"] = {}

        if guild_id not in data["alliances"][a_id]["guild_abonnes"]:
            data["alliances"][a_id]["guild_abonnes"][guild_id] = {
                "raison": reason,
                "mouvements": False,
                "rangs": False,
                "infos": False,
            }
            await save_surveillance_async(data)

            title_str = t(
                langue,
                "rad_srv_add_a_title",
                a=a_name_real,
                defaut=f"{e_alli} Cible Serveur : {a_name_real}",
            )
            desc_str = t(
                langue,
                "rad_srv_add_a_desc",
                defaut="Ajoutée au radar public.\n\n**Configure les alertes pour le serveur :**",
            )
            embed = discord.Embed(title=title_str, description=desc_str, color=self.clr_add_alliance)
            view = RadarAllianceSettingsView(
                a_id, guild_id, "guild", a_name_real, data["alliances"][a_id]["guild_abonnes"][guild_id], langue
            )
            await setup_embed_footer(embed, interaction, langue)
            await interaction.followup.send(embed=embed, view=view)
        else:
            msg_already = t(
                langue,
                "rad_err_srv_already_track_alli",
                a=a_name_real,
                defaut=f"Le serveur surveille DÉJÀ **{a_name_real}** ! {e_wink}",
            )
            await interaction.followup.send(msg_already)

    @server_group.command(name="remove_player", description="Remove a player from the server radar")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def srv_remove_player(self, interaction: discord.Interaction, player: str):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            logger.warning(f"❌ Defer failed sur /{interaction.command.name} : {e}")
            return
        langue, _ = await get_server_config(interaction)

        if not interaction.guild:
            msg = t(langue, "cmd_guild_only", defaut="❌ Cette commande ne peut être utilisée que sur un serveur.")
            return await interaction.followup.send(msg)

        data = await load_surveillance_async()
        guild_id = str(interaction.guild.id)
        cible_trouvee = False

        for pid, info in list(data.get("players", {}).items()):
            if info["name"].lower() == player.lower() and guild_id in info.get("guild_abonnes", {}):
                cible_trouvee = True
                del data["players"][pid]["guild_abonnes"][guild_id]
                if not data["players"][pid].get("abonnes", {}) and not data["players"][pid].get("guild_abonnes", {}):
                    del data["players"][pid]
                break

        e_check = DICT_EMOJIS.get("e_check", "✅")
        e_warning = DICT_EMOJIS.get("e_warning", "⚠️")

        if cible_trouvee:
            await save_surveillance_async(data)
            msg_succ = t(
                langue, "rad_srv_rem_p_succ", p=player, defaut=f"{e_check} **{player}** retiré du radar du serveur."
            )
            await interaction.followup.send(msg_succ)
        else:
            msg_fail = t(
                langue,
                "rad_srv_rem_p_fail",
                p=player,
                defaut=f"{e_warning} **{player}** n'est pas surveillé par le serveur.",
            )
            await interaction.followup.send(msg_fail)

    @server_group.command(name="remove_alliance", description="Remove an alliance from the server radar")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def srv_remove_alliance(self, interaction: discord.Interaction, alliance_name: str):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            logger.warning(f"❌ Defer failed sur /{interaction.command.name} : {e}")
            return
        langue, _ = await get_server_config(interaction)

        if not interaction.guild:
            msg = t(langue, "cmd_guild_only", defaut="❌ Cette commande ne peut être utilisée que sur un serveur.")
            return await interaction.followup.send(msg)

        data = await load_surveillance_async()
        guild_id = str(interaction.guild.id)
        cible_trouvee = False

        for aid, info in list(data.get("alliances", {}).items()):
            if info["name"].lower() == alliance_name.lower() and guild_id in info.get("guild_abonnes", {}):
                cible_trouvee = True
                del data["alliances"][aid]["guild_abonnes"][guild_id]
                if not data["alliances"][aid].get("abonnes", {}) and not data["alliances"][aid].get(
                    "guild_abonnes", {}
                ):
                    del data["alliances"][aid]
                break

        e_check = DICT_EMOJIS.get("e_check", "✅")
        e_warning = DICT_EMOJIS.get("e_warning", "⚠️")

        if cible_trouvee:
            await save_surveillance_async(data)
            msg_succ = t(
                langue,
                "rad_srv_rem_a_succ",
                a=alliance_name,
                defaut=f"{e_check} L'alliance **{alliance_name}** a été retirée du radar du serveur.",
            )
            await interaction.followup.send(msg_succ)
        else:
            msg_fail = t(
                langue,
                "rad_srv_rem_a_fail",
                a=alliance_name,
                defaut=f"{e_warning} **{alliance_name}** n'est pas surveillée par le serveur.",
            )
            await interaction.followup.send(msg_fail)

    @server_group.command(name="list", description="Display the server's radar targets")
    @app_commands.default_permissions(manage_guild=True)
    async def srv_list(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            logger.warning(f"❌ Defer failed sur /{interaction.command.name} : {e}")
            return
        langue, _ = await get_server_config(interaction)

        if not interaction.guild:
            msg = t(langue, "cmd_guild_only", defaut="❌ Cette commande ne peut être utilisée que sur un serveur.")
            return await interaction.followup.send(msg)

        data = await load_surveillance_async()
        guild_id = str(interaction.guild.id)

        if guild_id not in data.get("guild_configs", {}):
            msg_err = t(langue, "rad_err_srv_not_setup", defaut="❌ Ce serveur n'a pas configuré de radar.")
            return await interaction.followup.send(msg_err)

        mes_joueurs, mes_alliances = [], []
        lbl_sr = t(langue, "rad_list_no_reason", defaut="Sans raison")
        e_check = DICT_EMOJIS.get("e_check", "✅")
        e_nocheck = DICT_EMOJIS.get("e_nocheck", "❌")
        e_bullseye = DICT_EMOJIS.get("e_std_bullseye", "🎯")
        e_alli = DICT_EMOJIS.get("e_alliance_icon", "🛡️")

        for pid, info in data.get("players", {}).items():
            if guild_id in info.get("guild_abonnes", {}):
                prefs = info["guild_abonnes"][guild_id]
                filtres_actifs = [k.capitalize() for k, v in prefs.items() if v and k != "raison"]
                str_f = (
                    f"{e_check} {', '.join(filtres_actifs)}" if filtres_actifs else f"{e_nocheck} Aucun filtre actif"
                )
                mes_joueurs.append(f"{e_bullseye} **{info['name']}** ➔ *{prefs.get('raison', lbl_sr)}*\n└ {str_f}")

        for aid, info in data.get("alliances", {}).items():
            if guild_id in info.get("guild_abonnes", {}):
                prefs = info["guild_abonnes"][guild_id]
                filtres_actifs = [k.capitalize() for k, v in prefs.items() if v and k != "raison"]
                str_f = (
                    f"{e_check} {', '.join(filtres_actifs)}" if filtres_actifs else f"{e_nocheck} Aucun filtre actif"
                )
                mes_alliances.append(f"{e_alli} **{info['name']}** ➔ *{prefs.get('raison', lbl_sr)}*\n└ {str_f}")

        if not mes_joueurs and not mes_alliances:
            e_info = DICT_EMOJIS.get("e_information", "ℹ️")
            msg_empty = t(langue, "rad_srv_list_empty", defaut=f"{e_info} Le radar de ce serveur est vide !")
            return await interaction.followup.send(msg_empty)

        e_detective = DICT_EMOJIS.get("e_std_man_detective", "🕵️‍♂️")
        embeds = []
        if mes_alliances:
            title_alli = t(langue, "rad_srv_list_title_alli", defaut=f"{e_detective} Radar Serveur - Alliances")
            embed = discord.Embed(
                title=title_alli, description="\n\n".join(mes_alliances), color=self.clr_list_alliances
            )
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        if mes_joueurs:
            title_play = t(langue, "rad_srv_list_title_player", defaut=f"{e_detective} Radar Serveur - Joueurs")
            for i in range(0, len(mes_joueurs), 10):
                embed = discord.Embed(
                    title=title_play, description="\n\n".join(mes_joueurs[i : i + 10]), color=self.clr_list_joueurs
                )
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            await interaction.followup.send(embed=embeds[0], view=PaginationView(embeds))

    # ==========================================
    # 🕵️‍♂️ COMMANDES : RADAR PERSONNEL (MP)
    # ==========================================
    private_group = app_commands.Group(name="private", description="Manage your personal war radar (DMs)")

    @private_group.command(name="add_player", description="Add a player to your personal radar (Limit: 25 followed)")
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.describe(reason="Reason for surveillance")
    async def private_add_player(self, interaction: discord.Interaction, player: str, reason: str = "Monitoring"):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception as e:
            logger.warning(f"❌ Defer failed sur /{interaction.command.name} : {e}")
            return

        langue, serveur = await get_server_config(interaction)

        p_name_exact, p_info = await find_player_in_cache(serveur, player)
        if p_info:
            p_id = str(p_info.get("player_id", p_info.get("id", "")))
            p_might = int(p_info.get("main_points", 0))
            player = p_name_exact
        else:
            p_id, p_might = None, 0

        e_warning = DICT_EMOJIS.get("e_warning", "⚠️")
        e_nocheck = DICT_EMOJIS.get("e_nocheck", "❌")
        e_bullseye = DICT_EMOJIS.get("e_std_bullseye", "🎯")
        e_wink = DICT_EMOJIS.get("e_std_winking_face", "😉")
        e_down = DICT_EMOJIS.get("e_std_backhand_index_pointing_down", "👇")

        if not p_id:
            msg = t(
                langue,
                "rad_err_not_found_cache",
                p=player,
                defaut=f"{e_warning} Joueur **{player}** introuvable dans le cache local.",
            )
            return await interaction.followup.send(msg)

        data = await load_surveillance_async()
        user_id = str(interaction.user.id)
        now_str = discord.utils.utcnow().isoformat().replace("+00:00", "Z")

        already_tracking = user_id in data.get("players", {}).get(p_id, {}).get("abonnes", {})
        if not already_tracking:
            count_tracked = sum(
                1 for p_info in data.get("players", {}).values() if user_id in p_info.get("abonnes", {})
            )
            if count_tracked >= 25:
                msg = t(
                    langue,
                    "rad_err_limit_player",
                    cnt=count_tracked,
                    defaut=f"{e_nocheck} **Limite atteinte** : Suivi maximal `{count_tracked}/25` joueurs.",
                )
                return await interaction.followup.send(msg)

        if p_id not in data.get("players", {}):
            if "players" not in data:
                data["players"] = {}
            data["players"][p_id] = {
                "name": player,
                "last_alliance": now_str,
                "last_name": now_str,
                "last_pos": now_str,
                "last_might": p_might,
                "peace_disabled_at": None,
                "is_protected": False,
                "abonnes": {},
                "guild_abonnes": {},
                "serveur": serveur,
            }

        if "abonnes" not in data["players"][p_id]:
            data["players"][p_id]["abonnes"] = {}

        if user_id not in data["players"][p_id]["abonnes"]:
            data["players"][p_id]["abonnes"][user_id] = {
                "raison": reason,
                "pseudo": False,
                "position": False,
                "alliance": False,
                "puissance": False,
                "colombe": False,
            }
        else:
            msg = t(
                langue,
                "rad_err_already_track",
                p=player,
                defaut=f"Tu surveilles DÉJÀ **{player}** ! {e_wink}",
            )
            return await interaction.followup.send(msg)

        await save_surveillance_async(data)

        embed = discord.Embed(
            title=t(langue, "rad_add_title", j=player, defaut=f"{e_bullseye} Cible verrouillée : {player}"),
            description=t(
                langue,
                "rad_add_desc",
                j=player,
                r=reason,
                defaut=f"**{player}** a bien été ajouté à ton radar personnel.\n*(Raison: {reason})*\n\n{e_down} **Configure tes alertes (Rouge = OFF, Vert = ON) :**",
            ),
            color=self.clr_add_joueur,
        )
        view = RadarSettingsView(p_id, user_id, "user", player, data["players"][p_id]["abonnes"][user_id], langue)
        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed, view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=20, langue=langue)

    @private_group.command(name="add_alliance", description="Add an entire alliance to your radar (Limit: 3 followed)")
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    @app_commands.describe(reason="Why are you monitoring this alliance?")
    async def private_add_alliance(
        self, interaction: discord.Interaction, alliance_name: str, reason: str = "Monitoring"
    ):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception as e:
            logger.warning(f"❌ Defer failed sur /{interaction.command.name} : {e}")
            return

        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

        a_id, a_name_real, members_dict = None, alliance_name, {}

        e_warning = DICT_EMOJIS.get("e_warning", "⚠️")
        e_nocheck = DICT_EMOJIS.get("e_nocheck", "❌")
        e_alli = DICT_EMOJIS.get("e_alliance_icon", "🛡️")
        e_wink = DICT_EMOJIS.get("e_std_winking_face", "😉")
        e_down = DICT_EMOJIS.get("e_std_backhand_index_pointing_down", "👇")

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
                        if isinstance(alli_data, list) and alli_data:
                            alli_data = alli_data[0]
                        members = alli_data.get("players", alli_data.get("members", alli_data.get("playerList", [])))

                        for m in members:
                            p_id = str(m.get("player_id", m.get("playerId", "")))
                            if p_id:
                                members_dict[p_id] = {
                                    "name": m.get("player_name", m.get("playerName", "Inconnu")),
                                    "rank": int(m.get("alliance_rank", 8)),
                                }
        except Exception as e:
            logger.error(f"❌ [Radar Alli Add] Erreur API : {e}")
            msg = t(
                langue,
                "rad_err_api_join",
                defaut=f"{e_nocheck} Impossible de joindre GGE-Tracker pour trouver cette alliance.",
            )
            return await interaction.followup.send(msg)

        if not a_id:
            msg = t(
                langue,
                "rad_err_alli_not_found",
                a=alliance_name,
                defaut=f"{e_warning} Alliance **{alliance_name}** introuvable.",
            )
            return await interaction.followup.send(msg)

        data = await load_surveillance_async()
        user_id = str(interaction.user.id)

        already_tracking = user_id in data.get("alliances", {}).get(a_id, {}).get("abonnes", {})
        if not already_tracking:
            count_tracked = sum(
                1 for a_info in data.get("alliances", {}).values() if user_id in a_info.get("abonnes", {})
            )
            if count_tracked >= 3:
                msg = t(
                    langue,
                    "rad_err_limit_alli",
                    cnt=count_tracked,
                    defaut=f"{e_nocheck} **Limite atteinte** : Surveillance maximale `{count_tracked}/3` alliances.",
                )
                return await interaction.followup.send(msg)

        if a_id not in data.get("alliances", {}):
            if "alliances" not in data:
                data["alliances"] = {}
            data["alliances"][a_id] = {
                "name": a_name_real,
                "members": members_dict,
                "abonnes": {},
                "guild_abonnes": {},
                "serveur": serveur,
            }

        if "abonnes" not in data["alliances"][a_id]:
            data["alliances"][a_id]["abonnes"] = {}

        if user_id not in data["alliances"][a_id]["abonnes"]:
            data["alliances"][a_id]["abonnes"][user_id] = {
                "raison": reason,
                "mouvements": False,
                "rangs": False,
                "infos": False,
            }
        else:
            msg = t(
                langue,
                "rad_err_already_track_alli",
                a=a_name_real,
                defaut=f"Tu surveilles DÉJÀ l'alliance **{a_name_real}** ! {e_wink}",
            )
            return await interaction.followup.send(msg)

        await save_surveillance_async(data)

        embed = discord.Embed(
            title=t(
                langue,
                "rad_alli_add_title",
                a=a_name_real,
                defaut=f"{e_alli} Alliance verrouillée : {a_name_real}",
            ),
            description=t(
                langue,
                "rad_alli_add_desc",
                a=a_name_real,
                cnt=len(members_dict),
                r=reason,
                defaut=f"Le radar personnel est activé sur **{a_name_real}** ({len(members_dict)} membres).\n*(Raison: {reason})*\n\n{e_down} **Configure tes alertes d'alliance (Rouge = OFF, Vert = ON) :**",
            ),
            color=self.clr_add_alliance,
        )
        view = RadarAllianceSettingsView(
            a_id, user_id, "user", a_name_real, data["alliances"][a_id]["abonnes"][user_id], langue
        )
        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed, view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=20, langue=langue)

    @private_group.command(name="remove_player", description="Remove a player from your personal radar")
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def private_remove_player(self, interaction: discord.Interaction, player: str):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception as e:
            logger.warning(f"❌ Defer failed sur /{interaction.command.name} : {e}")
            return

        langue, _ = await get_server_config(interaction)
        data = await load_surveillance_async()
        user_id = str(interaction.user.id)
        cible_trouvee = False

        for pid, info in list(data.get("players", {}).items()):
            if info["name"].lower() == player.lower():
                if user_id in info.get("abonnes", {}):
                    cible_trouvee = True
                    del data["players"][pid]["abonnes"][user_id]
                    if not data["players"][pid].get("abonnes", {}) and not data["players"][pid].get(
                        "guild_abonnes", {}
                    ):
                        del data["players"][pid]
                    break

        e_check = DICT_EMOJIS.get("e_check", "✅")
        e_warning = DICT_EMOJIS.get("e_warning", "⚠️")

        if cible_trouvee:
            await save_surveillance_async(data)
            msg = t(
                langue,
                "rad_rem_succ",
                j=player,
                defaut=f"{e_check} **{player}** a bien été retiré de ton radar personnel.",
            )
            await interaction.followup.send(msg)
        else:
            msg = t(langue, "rad_rem_fail", j=player, defaut=f"{e_warning} **{player}** n'est pas dans ton radar.")
            await interaction.followup.send(msg)

    @private_group.command(name="remove_alliance", description="Remove an alliance from your personal radar")
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def private_remove_alliance(self, interaction: discord.Interaction, alliance_name: str):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception as e:
            logger.warning(f"❌ Defer failed sur /{interaction.command.name} : {e}")
            return

        langue, _ = await get_server_config(interaction)
        data = await load_surveillance_async()
        user_id = str(interaction.user.id)
        cible_trouvee = False

        for aid, info in list(data.get("alliances", {}).items()):
            if info["name"].lower() == alliance_name.lower():
                if user_id in info.get("abonnes", {}):
                    cible_trouvee = True
                    del data["alliances"][aid]["abonnes"][user_id]
                    if not data["alliances"][aid].get("abonnes", {}) and not data["alliances"][aid].get(
                        "guild_abonnes", {}
                    ):
                        del data["alliances"][aid]
                    break

        e_check = DICT_EMOJIS.get("e_check", "✅")
        e_warning = DICT_EMOJIS.get("e_warning", "⚠️")

        if cible_trouvee:
            await save_surveillance_async(data)
            msg = t(
                langue,
                "rad_alli_rem_succ",
                a=alliance_name,
                defaut=f"{e_check} L'alliance **{alliance_name}** a bien été retirée de ton radar.",
            )
            await interaction.followup.send(msg)
        else:
            msg = t(
                langue,
                "rad_alli_rem_fail",
                a=alliance_name,
                defaut=f"{e_warning} L'alliance **{alliance_name}** n'est pas dans ton radar.",
            )
            await interaction.followup.send(msg)

    @private_group.command(name="list", description="Display your personal radar (Players & Alliances)")
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
    async def private_list(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception as e:
            logger.warning(f"❌ Defer failed sur /{interaction.command.name} : {e}")
            return

        langue, _ = await get_server_config(interaction)
        data = await load_surveillance_async()
        user_id = str(interaction.user.id)

        lbl_sr = t(langue, "rad_list_no_reason", defaut="Sans raison")
        e_bullseye = DICT_EMOJIS.get("e_std_bullseye", "🎯")
        e_alli = DICT_EMOJIS.get("e_alliance_icon", "🛡️")
        e_check = DICT_EMOJIS.get("e_check", "✅")
        e_nocheck = DICT_EMOJIS.get("e_nocheck", "❌")

        mes_joueurs = []
        for pid, info in data.get("players", {}).items():
            abonnes = info.get("abonnes", {})
            if user_id in abonnes:
                prefs = abonnes[user_id]
                nom = info["name"]
                raison = prefs.get("raison", lbl_sr)

                filtres_actifs = []
                if prefs.get("pseudo"):
                    filtres_actifs.append(t(langue, "rad_btn_pseudo", defaut="Pseudo"))
                if prefs.get("position"):
                    filtres_actifs.append(t(langue, "rad_btn_pos", defaut="Position"))
                if prefs.get("alliance"):
                    filtres_actifs.append(t(langue, "rad_btn_alli", defaut="Alliance"))
                if prefs.get("puissance"):
                    filtres_actifs.append(t(langue, "rad_btn_pp", defaut="Puissance"))
                if prefs.get("colombe"):
                    filtres_actifs.append(t(langue, "rad_btn_dove", defaut="Colombe"))

                str_filtres = (
                    t(
                        langue,
                        "rad_list_filter_active",
                        f=", ".join(filtres_actifs),
                        defaut=f"{e_check} {', '.join(filtres_actifs)}",
                    )
                    if filtres_actifs
                    else t(langue, "rad_list_no_filter", defaut=f"{e_nocheck} Aucun filtre actif")
                )
                mes_joueurs.append(f"{e_bullseye} **{nom}** ➔ *{raison}*\n└ {str_filtres}")

        mes_alliances = []
        for aid, info in data.get("alliances", {}).items():
            abonnes = info.get("abonnes", {})
            if user_id in abonnes:
                prefs = abonnes[user_id]
                nom = info["name"]
                raison = prefs.get("raison", lbl_sr)

                filtres_actifs = []
                if prefs.get("mouvements"):
                    filtres_actifs.append(t(langue, "rad_alli_btn_mouv", defaut="Mouvements"))
                if prefs.get("rangs"):
                    filtres_actifs.append(t(langue, "rad_alli_btn_rangs", defaut="Rangs"))
                if prefs.get("infos"):
                    filtres_actifs.append(t(langue, "rad_alli_btn_infos", defaut="Infos"))

                str_filtres = (
                    t(
                        langue,
                        "rad_list_filter_active",
                        f=", ".join(filtres_actifs),
                        defaut=f"{e_check} {', '.join(filtres_actifs)}",
                    )
                    if filtres_actifs
                    else t(langue, "rad_list_no_filter", defaut=f"{e_nocheck} Aucun filtre actif")
                )
                mes_alliances.append(f"{e_alli} **{nom}** ➔ *{raison}*\n└ {str_filtres}")

        if not mes_joueurs and not mes_alliances:
            e_info = DICT_EMOJIS.get("e_information", "ℹ️")
            msg = t(
                langue,
                "rad_list_empty",
                defaut=f"{e_info} Ton radar est vide ! Utilise `/radar private add_player` ou `/radar private add_alliance`.",
            )
            return await interaction.followup.send(msg)

        e_detective = DICT_EMOJIS.get("e_std_man_detective", "🕵️‍♂️")
        embeds = []
        if mes_alliances:
            embed = discord.Embed(
                title=t(langue, "rad_list_title_alli", defaut=f"{e_detective} Mon Radar - Alliances"),
                color=self.clr_list_alliances,
            )
            embed.description = "\n\n".join(mes_alliances)
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        if mes_joueurs:
            lignes_par_page = 10
            for i in range(0, len(mes_joueurs), lignes_par_page):
                chunk = mes_joueurs[i : i + lignes_par_page]
                embed = discord.Embed(
                    title=t(langue, "rad_list_title_player", defaut=f"{e_detective} Mon Radar - Joueurs"),
                    color=self.clr_list_joueurs,
                )
                embed.description = "\n\n".join(chunk)
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)


async def setup(bot: commands.Bot):
    await bot.add_cog(RadarCog(bot))
