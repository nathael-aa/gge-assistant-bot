import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

import observability as obs
from utils import (
    BASE_DATA_PATH,
    BASE_DIR,
    CONFIG_DIR,
    async_json_dump,
    async_json_load,
    clear_config_cache,
    get_file_lock,
    get_server_config,
    load_configuration_async,
    t,
)

logger = logging.getLogger("GGE_Bot")


# ===========================================
# 💾 SAUVEGARDE CONFIG UTILISATEURS (DMs)
# ===========================================
async def load_users_config():
    path = CONFIG_DIR / "users.json"
    async with get_file_lock(path):
        if path.exists():
            try:
                return await async_json_load(path)
            except Exception:
                pass
    return {}


async def save_users_config(data):
    path = CONFIG_DIR / "users.json"
    async with get_file_lock(path):
        await async_json_dump(path, data, indent=2, ensure_ascii=False)


class ConfigurationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ==========================================
    # 🔍 AUTOCOMPLÉTION DES SERVEURS GGE
    # ==========================================
    async def server_autocomplete(self, interaction: discord.Interaction, current: str):
        langue, _ = await get_server_config(interaction)

        lbl_mobile = t(langue, "platform_mobile", defaut="Mobile")
        lbl_pc = t(langue, "platform_pc", defaut="PC")
        lbl_ko = t(langue, "config_unsupported", defaut="Non pris en charge")
        lbl_unk = t(langue, "config_unknown", defaut="Inconnu")

        choix_valides = []
        choix_invalides = []

        # 🔄 Lecture depuis la structure unifiée
        config_data = await load_configuration_async()
        servers_info = config_data.get("servers_info", {})

        for srv, data in servers_info.items():
            if current.lower() in srv.lower():
                is_mobile = srv.startswith("E4K_")
                emoji_platform = "📱" if is_mobile else "💻"
                platform_tag = lbl_mobile if is_mobile else lbl_pc

                is_enabled = data.get("enabled", False)

                if is_enabled:
                    full_name = f"{emoji_platform} {srv} ({platform_tag})"[:100]
                    choix_valides.append(app_commands.Choice(name=full_name, value=srv))
                else:
                    full_name = f"🔴 {srv} ({lbl_ko})"[:100]
                    choix_invalides.append(app_commands.Choice(name=full_name, value=srv))

        choix_valides.sort(key=lambda x: x.name)
        choix_invalides.sort(key=lambda x: x.name)

        choix = (choix_valides + choix_invalides)[:25]

        if not choix and current:
            safe_value = current.upper()[:100]
            full_name = f"❓ {safe_value} ({lbl_unk})"[:100]
            choix.append(app_commands.Choice(name=full_name, value=safe_value))

        return choix

    # ==========================================
    # ⚙️ MOTEUR DE SCAN D'URGENCE GLOBAL
    # ==========================================
    def trigger_emergency_scan(self, serveur_upper: str) -> bool:
        """Déclenche un scan asynchrone si le dossier du serveur n'existe pas encore. Retourne True si déclenché."""
        dossier_serveur = BASE_DATA_PATH / "server_scans" / serveur_upper
        if dossier_serveur.exists():
            return False

        async def scan_urgence_background(srv):
            flag = BASE_DATA_PATH / "scan.flag"

            def _create_flag():
                flag.touch(exist_ok=True)

            def _remove_flag():
                if flag.exists():
                    flag.unlink()

            await asyncio.to_thread(_create_flag)
            try:
                proc1 = await asyncio.create_subprocess_exec(
                    "python3", "scanners/server_scanner.py", srv, cwd=str(BASE_DIR)
                )
                await proc1.wait()
                proc2 = await asyncio.create_subprocess_exec(
                    "python3", "scanners/murs_scanner.py", srv, cwd=str(BASE_DIR)
                )
                await proc2.wait()
            except Exception as e:
                print(f"❌ Erreur lors du scan d'urgence : {e}")
            finally:
                await asyncio.to_thread(_remove_flag)

        self.bot.loop.create_task(scan_urgence_background(serveur_upper))
        return True

    # ==========================================
    # 👤 CONFIGURATION PERSONNELLE UNIQUE
    # ==========================================
    @app_commands.command(name="setup", description="Configure your language and your primary GGE server.")
    @app_commands.describe(language="Your primary language", server="Your Goodgame Empire server")
    @app_commands.choices(
        language=[
            app_commands.Choice(name="🇫🇷 Français", value="fr"),
            app_commands.Choice(name="🇬🇧 English", value="en"),
            app_commands.Choice(name="🇩🇪 Deutsch", value="de"),
        ]
    )
    @app_commands.autocomplete(server=server_autocomplete)
    async def c_setup(self, interaction: discord.Interaction, language: app_commands.Choice[str], server: str):

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            logger.warning(f"❌ Defer failed sur /{interaction.command.name} : {e}")
            return

        serveur_upper = server.upper()

        # 🔄 Lecture depuis la structure unifiée
        config_data = await load_configuration_async()
        servers_info = config_data.get("servers_info", {})
        srv_data = servers_info.get(serveur_upper, {})

        is_supported = srv_data.get("enabled", False)

        if not is_supported:
            msg_erreur = t(
                language.value,
                "error_unsupported_server",
                serveur=serveur_upper,
                defaut="{e_error} **Erreur** : Le serveur `{serveur}` n'est pas pris en charge par l'API pour le moment.",
            )
            return await interaction.followup.send(msg_erreur)

        # 👤 Sauvegarde Personnelle
        user_id = str(interaction.user.id)
        data = await load_users_config()
        old_profile = data.get(user_id) or {}
        data[user_id] = {"nom_discord": interaction.user.name, "langue": language.value, "gge_server": serveur_upper}
        await save_users_config(data)
        clear_config_cache()

        # Enregistrement de l'événement et mise à jour de la dimension utilisateur
        obs.record_guild_event(
            "setup",
            guild=interaction.guild,
            user_id=interaction.user.id,
            lang=language.value,
            gge_server=serveur_upper,
            old_value=f"{old_profile.get('langue', '')}/{old_profile.get('gge_server', '')}".strip("/"),
            new_value=f"{language.value}/{serveur_upper}",
        )
        obs.upsert_user(
            interaction.user.id,
            user_name=interaction.user.name,
            lang=language.value,
            gge_server=serveur_upper,
        )

        titre = t(language.value, "config_dm_setup_title", defaut="{e_check} Profil Configuré")
        desc = t(
            language.value,
            "config_dm_setup_desc",
            defaut="Ton profil personnel a bien été configuré !\nLe bot utilisera ces paramètres pour toi, peu importe le serveur Discord où tu te trouves.",
        )

        if self.trigger_emergency_scan(serveur_upper):
            desc += t(
                language.value,
                "config_scan_init",
                srv=serveur_upper,
                defaut="\n\n{e_hourglass} *Un scan d'initialisation pour {srv} vient d'être lancé en arrière-plan. Le radar sera disponible d'ici 1 à 2 minutes.*",
            )

        embed = discord.Embed(title=titre, description=desc, color=discord.Color.blue())

        lbl_lang = t(language.value, "config_field_lang", defaut="Langue")
        lbl_srv = t(language.value, "config_field_server", defaut="Serveur GGE")

        is_mobile = serveur_upper.startswith("E4K_")
        pastille = "📱" if is_mobile else "💻"

        embed.add_field(name=lbl_lang, value=language.name, inline=False)
        embed.add_field(name=lbl_srv, value=f"{pastille} `{serveur_upper}`", inline=False)

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ConfigurationCog(bot))
