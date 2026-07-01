# -*- coding: utf-8 -*-
import os
import json
import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from pathlib import Path

from utils import CONFIG_DIR, t, get_server_config, MON_ID_DISCORD

# ==========================================
# 💾 SAUVEGARDE CONFIG SERVEURS
# ==========================================
async def load_serveurs_config():
    path = CONFIG_DIR / 'serveurs.json'
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {}

async def save_serveurs_config(data):
    path = CONFIG_DIR / 'serveurs.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ==========================================
# 💾 SAUVEGARDE CONFIG UTILISATEURS (DMs)
# ==========================================
async def load_users_config():
    path = CONFIG_DIR / 'users.json'
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {}

async def save_users_config(data):
    path = CONFIG_DIR / 'users.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# 🌍 LISTE DES SERVEURS GGE (Mise à jour via API officielle)

GGE_SERVERS = {

    # 📱 Serveurs E4K (Mobiles) Supportés
    "E4K_FR1": True, "E4K_DE1": False, "E4K_DE2": False, "E4K_US1": False,
    "E4K_INT2": False, "E4K_CN1": False, "E4K_GB1": False, "E4K_RU1": False,
    "E4K_HANT1": False, "E4K_BR1": False, "E4K_WORLD2": False,
    
    # 💻 Serveurs PC (Navigateur) Supportés
    "FR1": False, "DE1": False, "INT1": False, "INT2": False, "INT3": True,
    "WORLD1": False, "WORLD2": True, "GLOBAL": False, "ES1": False, "ES2": False,
    "IT1": False, "TR1": False, "NL1": False, "HU1": False, "HU2": False,
    "PL1": False, "PT1": False, "SK1": False, "SKN1": False, "RU1": False,
    "RO1": False, "BG1": False, "GB1": True, "BR1": False, "US1": True,
    "AU1": False, "JP1": False, "IN1": False, "CN1": False, "GR1": False,
    "SA1": False, "AE1": False, "EG1": False, "ARAB1": False, "ASIA": False,
    "HANT1": False, "PARTNER_SP3": False,
    
    # 🔴 Autres serveurs E4K (Non supportés par le tracker pour le moment)
    "E4K_ES1": False, "E4K_PL1": False, "E4K_IT1": False, "E4K_TR1": False,
    "E4K_NL1": False, "E4K_PT1": False, "E4K_GR1": False, "E4K_RO1": False,
    "E4K_HU1": False, "E4K_CZ1": False, "E4K_SK1": False, "E4K_BG1": False,
    "E4K_HR1": False, "E4K_SE1": False, "E4K_NO1": False, "E4K_FI1": False,
    "E4K_DK1": False, "E4K_JP1": False, "E4K_KR1": False, "E4K_TW1": False,
    "E4K_INT1": False

}

class ConfigCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ==========================================
    # 🔍 AUTOCOMPLÉTION DES SERVEURS GGE
    # ==========================================
    async def server_autocomplete(self, interaction: discord.Interaction, current: str):
        # 🟢 On récupère la langue pour garder l'affichage traduit
        langue, _ = await get_server_config(interaction)
        
        lbl_ok = t(langue, "config_supported", defaut="🟢 Pris en charge")
        lbl_ko = t(langue, "config_unsupported", defaut="🔴 Non pris en charge")
        lbl_unk = t(langue, "config_unknown", defaut="❓ Inconnu")

        # On crée deux paniers distincts
        choix_vert = []
        choix_rouge = []
        
        for srv, is_supported in GGE_SERVERS.items():
            if current.lower() in srv.lower():
                if is_supported:
                    choix_vert.append(app_commands.Choice(name=f"{srv} ({lbl_ok})", value=srv))
                else:
                    choix_rouge.append(app_commands.Choice(name=f"{srv} ({lbl_ko})", value=srv))
        
        choix = (choix_vert + choix_rouge)[:25]
        
        if not choix and current:
            choix.append(app_commands.Choice(name=f"{current.upper()} ({lbl_unk})", value=current.upper()))
            
        return choix

    # ==========================================
    # ⚙️ MOTEUR DE SCAN D'URGENCE GLOBAL
    # ==========================================
    def trigger_emergency_scan(self, serveur_upper: str) -> bool:
        """Déclenche un scan asynchrone si le dossier du serveur n'existe pas encore. Retourne True si déclenché."""
        dossier_serveur = Path(f"/app/data/server_scans/{serveur_upper}")
        if dossier_serveur.exists():
            return False # Le dossier existe déjà, pas besoin de scanner

        async def scan_urgence_background(srv):
            flag = Path('/app/data/scan.flag')
            flag.touch(exist_ok=True) # On pose le flag pour bloquer la RAM
            try:
                # 1. Scan serveur (On attend qu'il finisse avant de passer aux murs)
                proc1 = await asyncio.create_subprocess_exec("python3", "scanners/server_scanner.py", srv, cwd="/app")
                await proc1.wait()
                # 2. Scan murs 
                proc2 = await asyncio.create_subprocess_exec("python3", "scanners/murs_scanner.py", srv, cwd="/app")
                await proc2.wait()
            except Exception as e:
                print(f"❌ Erreur lors du scan d'urgence : {e}")
            finally:
                if flag.exists(): flag.unlink() # On retire le flag (ce qui va mettre à jour la RAM du bot)

        # On lance la tâche asynchrone en arrière-plan
        self.bot.loop.create_task(scan_urgence_background(serveur_upper))
        return True

    # ==========================================
    # 🌍 CONFIGURATION UNIFIÉE (SERVEUR & MP)
    # ==========================================
    @app_commands.command(name="setup", description="Configure the language and game server for this Discord or your profile")
    @app_commands.describe(
        scope="Do you want to configure the Discord server or just your profile?",
        language="The main language",
        server="The Goodgame Empire server"
    )
    @app_commands.choices(scope=[
        app_commands.Choice(name="🏢 For the entire Discord server (Admin)", value="server"),
        app_commands.Choice(name="👤 For me only (Personal)", value="personal")
    ])
    @app_commands.choices(language=[
        app_commands.Choice(name="🇫🇷 Français", value="fr"),
        app_commands.Choice(name="🇬🇧 English", value="en"),
        app_commands.Choice(name="🇩🇪 Deutsch", value="de")
    ])
    @app_commands.autocomplete(server=server_autocomplete)
    async def c_setup(self, interaction: discord.Interaction, scope: app_commands.Choice[str], language: app_commands.Choice[str], server: str):
        est_sur_serveur = interaction.guild is not None

        # Vérification si l'utilisateur veut configurer le serveur Discord
        if scope.value == "server":
            if not est_sur_serveur:
                msg_err = t(language.value, "config_err_not_guild", defaut="❌ **Erreur** : Impossible de configurer un serveur depuis les Messages Privés.")
                return await interaction.response.send_message(msg_err, ephemeral=True)
                
            # 💥 CORRECTION : Tu as désormais un passe-droit absolu si c'est toi qui lances la commande !
            if not interaction.user.guild_permissions.manage_guild and interaction.user.id != MON_ID_DISCORD:
                msg_perm = t(language.value, "config_err_perm", defaut="❌ **Erreur** : Tu dois posséder la permission 'Gérer le serveur' pour configurer le bot ici.")
                return await interaction.response.send_message(msg_perm, ephemeral=True)
                
        # On masque la réponse si c'est un réglage personnel, on l'affiche si c'est pour le serveur
        await interaction.response.defer(ephemeral=(scope.value == "personal"))
        
        serveur_upper = server.upper()
        is_supported = GGE_SERVERS.get(serveur_upper, False)
        
        if not is_supported:
            msg_erreur = t(language.value, "error_unsupported_server", serveur=serveur_upper, defaut=f"❌ **Erreur** : Le serveur `{serveur_upper}` n'est pas pris en charge par l'API pour le moment. Veuillez choisir un serveur avec la pastille 🟢.")
            return await interaction.followup.send(msg_erreur)
        
        # Traitement selon le choix
        if scope.value == "server":
            # 🏢 Configuration du Serveur Discord
            guild_id = str(interaction.guild_id)
            data = await load_serveurs_config()
            data[guild_id] = {
                "nom_serveur_discord": interaction.guild.name,
                "langue": language.value,     # ⚠️ On garde les clés originales pour ne pas casser les fichiers
                "gge_server": serveur_upper   # ⚠️ On garde les clés originales
            }
            await save_serveurs_config(data)
            
            titre = t(language.value, "setup_success_title", defaut="✅ Configuration réussie")
            desc = t(language.value, "setup_success_desc", defaut="Ce serveur Discord a bien été configuré !")
            couleur = discord.Color.green()
        else:
            # 👤 Configuration Personnelle
            user_id = str(interaction.user.id)
            data = await load_users_config()
            data[user_id] = {
                "nom_discord": interaction.user.name,
                "langue": language.value,     # ⚠️ On garde les clés originales
                "gge_server": serveur_upper   # ⚠️ On garde les clés originales
            }
            await save_users_config(data)
            
            titre = t(language.value, "config_dm_setup_title", defaut="✅ Profil Configuré")
            desc = t(language.value, "config_dm_setup_desc", defaut="Ton profil personnel a bien été configuré ! Le bot utilisera ces paramètres pour toi, peu importe le serveur Discord où tu te trouves.")
            couleur = discord.Color.blue()
        
        if self.trigger_emergency_scan(serveur_upper):
            desc += t(language.value, "config_scan_init", srv=serveur_upper, defaut=f"\n\n⏳ *Un scan d'initialisation pour {serveur_upper} vient d'être lancé en arrière-plan. Les autocomplétions et le radar seront disponibles d'ici 1 à 2 minutes.*")

        embed = discord.Embed(title=titre, description=desc, color=couleur)
        
        lbl_lang = t(language.value, "config_field_lang", defaut="Langue")
        lbl_srv = t(language.value, "config_field_server", defaut="Serveur GGE")
        
        embed.add_field(name=lbl_lang, value=language.name, inline=False)
        embed.add_field(name=lbl_srv, value=f"`{serveur_upper}` 🟢", inline=False)
        
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(ConfigCog(bot))