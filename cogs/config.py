import asyncio
import json
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from utils import CONFIG_DIR, clear_config_cache, get_server_config, load_configuration_async, t


# ==========================================
# 💾 SAUVEGARDE CONFIG UTILISATEURS (DMs)
# ==========================================
async def load_users_config():
    path = CONFIG_DIR / 'users.json'
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {}

async def save_users_config(data):
    path = CONFIG_DIR / 'users.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class ConfigCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ==========================================
    # 🔍 AUTOCOMPLÉTION DES SERVEURS GGE
    # ==========================================
    async def server_autocomplete(self, interaction: discord.Interaction, current: str):
        langue, _ = await get_server_config(interaction)
        
        lbl_ok = t(langue, "config_supported", defaut="🟢 Pris en charge")
        lbl_ko = t(langue, "config_unsupported", defaut="🔴 Non pris en charge")
        lbl_unk = t(langue, "config_unknown", defaut="🟠 Inconnu")
        
        # 💡 Ajout des labels de plateforme pour l'affichage
        lbl_mobile = t(langue, "platform_mobile", defaut="Mobile")
        lbl_pc = t(langue, "platform_pc", defaut="Computer")

        choix_vert = []
        choix_rouge = []
        
        # 🔄 Lecture dynamique depuis configuration.json
        config_data = await load_configuration_async()
        active_servers = config_data.get("active_servers", {})
        
        for srv, is_supported in active_servers.items():
            if current.lower() in srv.lower():
                
                # 💡 Logique d'affichage dynamique (E4K = Mobile)
                platform_tag = lbl_mobile if srv.startswith("E4K_") else lbl_pc
                display_name = f"{srv} - {platform_tag}"
                
                if is_supported:
                    # ✅ AJOUT DE [:100] ICI
                    full_name = f"{display_name} ({lbl_ok})"[:100]
                    choix_vert.append(app_commands.Choice(name=full_name, value=srv))
                else:
                    # ✅ AJOUT DE [:100] ICI
                    full_name = f"{display_name} ({lbl_ko})"[:100]
                    choix_rouge.append(app_commands.Choice(name=full_name, value=srv))
        
        choix = (choix_vert + choix_rouge)[:25]
        
        if not choix and current:
            # ✅ AJOUT DE [:100] SUR LE NAME ET LA VALUE ICI AUSSI
            full_name = f"{current.upper()} ({lbl_unk})"[:100]
            safe_value = current.upper()[:100]
            choix.append(app_commands.Choice(name=full_name, value=safe_value))
            
        return choix

    # ==========================================
    # ⚙️ MOTEUR DE SCAN D'URGENCE GLOBAL
    # ==========================================
    def trigger_emergency_scan(self, serveur_upper: str) -> bool:
        """Déclenche un scan asynchrone si le dossier du serveur n'existe pas encore. Retourne True si déclenché."""
        dossier_serveur = Path(f"/app/data/server_scans/{serveur_upper}")
        if dossier_serveur.exists():
            return False

        async def scan_urgence_background(srv):
            flag = Path('/app/data/scan.flag')
            flag.touch(exist_ok=True)
            try:
                proc1 = await asyncio.create_subprocess_exec("python3", "scanners/server_scanner.py", srv, cwd="/app")
                await proc1.wait()
                proc2 = await asyncio.create_subprocess_exec("python3", "scanners/murs_scanner.py", srv, cwd="/app")
                await proc2.wait()
            except Exception as e:
                print(f"❌ Erreur lors du scan d'urgence : {e}")
            finally:
                if flag.exists(): flag.unlink()

        self.bot.loop.create_task(scan_urgence_background(serveur_upper))
        return True

    # ==========================================
    # 👤 CONFIGURATION PERSONNELLE UNIQUE
    # ==========================================
    @app_commands.command(name="setup", description="Configure your language and your primary GGE server.")
    @app_commands.describe(
        language="Your primary language",
        server="Your Goodgame Empire server"
    )
    @app_commands.choices(language=[
        app_commands.Choice(name="🇫🇷 Français", value="fr"),
        app_commands.Choice(name="🇬🇧 English", value="en"),
        app_commands.Choice(name="🇩🇪 Deutsch", value="de")
    ])
    @app_commands.autocomplete(server=server_autocomplete)
    async def c_setup(self, interaction: discord.Interaction, language: app_commands.Choice[str], server: str):

        await interaction.response.defer(ephemeral=True)
        
        serveur_upper = server.upper()

        config_data = await load_configuration_async()
        active_servers = config_data.get("active_servers", {})
        is_supported = active_servers.get(serveur_upper, False)
        
        if not is_supported:
            msg_erreur = t(language.value, "error_unsupported_server", serveur=serveur_upper, defaut=f"❌ **Erreur** : Le serveur `{serveur_upper}` n'est pas pris en charge par l'API pour le moment. Veuillez choisir un serveur avec la pastille 🟢.")
            return await interaction.followup.send(msg_erreur)
        
        # 👤 Sauvegarde Personnelle
        user_id = str(interaction.user.id)
        data = await load_users_config()
        data[user_id] = {
            "nom_discord": interaction.user.name,
            "langue": language.value,
            "gge_server": serveur_upper
        }
        await save_users_config(data)
        clear_config_cache()
        titre = t(language.value, "config_dm_setup_title", defaut="✅ Profil Configuré")
        desc = t(language.value, "config_dm_setup_desc", defaut="Ton profil personnel a bien été configuré !\nLe bot utilisera ces paramètres pour toi, peu importe le serveur Discord où tu te trouves.")
        
        if self.trigger_emergency_scan(serveur_upper):
            desc += t(language.value, "config_scan_init", srv=serveur_upper, defaut=f"\n\n⏳ *Un scan d'initialisation pour {serveur_upper} vient d'être lancé en arrière-plan. Le radar sera disponible d'ici 1 à 2 minutes.*")

        embed = discord.Embed(title=titre, description=desc, color=discord.Color.blue())
        
        lbl_lang = t(language.value, "config_field_lang", defaut="Langue")
        lbl_srv = t(language.value, "config_field_server", defaut="Serveur GGE")
        
        embed.add_field(name=lbl_lang, value=language.name, inline=False)
        embed.add_field(name=lbl_srv, value=f"`{serveur_upper}` 🟢", inline=False)
        
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(ConfigCog(bot))