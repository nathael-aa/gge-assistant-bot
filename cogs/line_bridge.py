# -*- coding: utf-8 -*-
import json
import logging
import time
import asyncio
from aiohttp import web
import discord
from discord.ext import commands

# 🛠️ On importe nos outils depuis utils.py (Nouvelle Architecture)
from utils import (
    t, 
    get_server_config, 
    CONFIG_DIR
)

logger = logging.getLogger("GGE_Bot")

# ========================================================
# ⚙️ CONFIGURATION DES SALONS ET RÔLES
# ========================================================
LOG_CHANNEL_ID = 1507879126172831744      # Salon "Tous les Logs"
INFO_CHANNEL_ID = 1507438409684090921     # Salon "Infos & @everyone"
ATTACK_CHANNEL_ID = 1507438545063776407   # Salon "Alerte Attaque"

NOTIFICATION_ROLE_ID = 1507436182575780050 
WEBHOOK_PORT = 8089

# ========================================================
# 🔘 VUES POUR LES BOUTONS (Persistantes & Sécurisées)
# ========================================================
class AlertButtonView(discord.ui.View):
    def __init__(self, role_id: int, langue: str = "fr"):
        super().__init__(timeout=None)
        self.role_id = role_id
        # 🌍 Traduction dynamique du bouton à la création
        self.trigger_alert.label = t(langue, "line_btn_alert_init", defaut="🚨 Déclencher l'Alerte")

    @discord.ui.button(style=discord.ButtonStyle.danger, custom_id="trigger_attack_alert")
    async def trigger_alert(self, interaction: discord.Interaction, button: discord.ui.Button):
        langue, _ = await get_server_config(interaction)
        button.disabled = True
        button.label = t(langue, "line_btn_alert_clicked", defaut="🔒 Alerte signalée")
        await interaction.response.edit_message(view=self)
        
        msg = t(langue, "line_msg_attack_confirm", role=self.role_id, user=interaction.user.mention, defaut=f"<:attaque:1512570903886692474> **Notification d'Attaque !** <@&{self.role_id}> — Confirmé par {interaction.user.mention} !")
        await interaction.channel.send(msg)

class EveryoneButtonView(discord.ui.View):
    def __init__(self, langue: str = "fr"):
        super().__init__(timeout=None)
        # 🌍 Traduction dynamique du bouton à la création
        self.trigger_everyone.label = t(langue, "line_btn_every_init", defaut="🔔 Mentionner tout le monde")

    @discord.ui.button(style=discord.ButtonStyle.primary, custom_id="trigger_everyone_alert")
    async def trigger_everyone(self, interaction: discord.Interaction, button: discord.ui.Button):
        langue, _ = await get_server_config(interaction)
        button.disabled = True
        button.label = t(langue, "line_btn_every_clicked", defaut="🔔 Tout le monde notifié")
        await interaction.response.edit_message(view=self)
        
        msg = t(langue, "line_msg_every_confirm", user=interaction.user.mention, defaut=f"📢 **Communication entrante** @everyone — {interaction.user.mention} a déclenché une notification.")
        await interaction.channel.send(msg)


# ========================================================
# 🛰️ COG DE LA PASSERELLE
# ========================================================
class LineBridgeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.web_server = None
        self.runner = None

    async def cog_load(self):
        # 🔄 Enregistrement des vues pour qu'elles restent actives même après un redémarrage du bot
        # (On utilise la langue FR par défaut au démarrage pour le cache interne de Discord)
        self.bot.add_view(AlertButtonView(role_id=NOTIFICATION_ROLE_ID, langue="fr"))
        self.bot.add_view(EveryoneButtonView(langue="fr"))
        
        # Lancement du serveur HTTP
        await self.start_http_server()

    async def cog_unload(self):
        """🔒 Nettoyage critique : Libération immédiate du port au déchargement du module"""
        try:
            if self.web_server:
                await self.web_server.stop()
            if self.runner:
                await self.runner.cleanup()
            logger.info("🛰️ [LineBridge] Serveur HTTP arrêté proprement et port libéré.")
        except Exception as e:
            logger.error(f"❌ Erreur lors de l'arrêt du serveur LineBridge : {e}")

    async def start_http_server(self):
        app = web.Application()
        app.router.add_post('/line-webhook', self.handle_line_webhook)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.web_server = web.TCPSite(self.runner, '0.0.0.0', WEBHOOK_PORT)
        await self.web_server.start()
        logger.info(f"🛰️ [LineBridge] Serveur HTTP démarré sur le port {WEBHOOK_PORT}")

    async def handle_line_webhook(self, request):
        try:
            body = await request.json()
            for event in body.get("events", []):
                if event.get("type") == "message" and event["message"]["type"] == "text":
                    line_msg = event["message"]["text"]
                    msg_lower = line_msg.lower()
                    
                    # 🌍 DÉTECTION DE LA LANGUE DU SERVEUR
                    langue = "fr"
                    log_chan = self.bot.get_channel(LOG_CHANNEL_ID)
                    if log_chan and hasattr(log_chan, "guild"):
                        try:
                            path_srv = CONFIG_DIR / 'serveurs.json'
                            if path_srv.exists():
                                with open(path_srv, 'r', encoding='utf-8') as f:
                                    data_srv = json.load(f)
                                    langue = data_srv.get(str(log_chan.guild.id), {}).get("langue", "fr")
                        except: pass
                    
                    # 1. SALON LOGS : Tout envoyer ici (brut)
                    if log_chan:
                        msg_log = t(langue, "line_log_msg", msg=line_msg, defaut=f"📥 **Log Line** : {line_msg}")
                        await log_chan.send(msg_log)

                    # 2. SALON INFOS : Si @all ET 🔥
                    if "@all" in msg_lower and "🔥" in msg_lower:
                        info_chan = self.bot.get_channel(INFO_CHANNEL_ID)
                        if info_chan:
                            titre = t(langue, "line_info_title", defaut="📢 Annonce Générale")
                            embed = discord.Embed(title=titre, description=line_msg, color=discord.Color.blue())
                            view = EveryoneButtonView(langue=langue)
                            await info_chan.send(embed=embed, view=view)

                    # 3. SALON ATTAQUE : Si mot-clé alerte (off, attaque, attack, angriff, attacco)
                    if any(mot in msg_lower for mot in ["off","raid","attaque","attack", "attacke", "angriff", "attacco"]):
                        atk_chan = self.bot.get_channel(ATTACK_CHANNEL_ID)
                        if atk_chan:
                            titre = t(langue, "line_atk_title", defaut="<:attaque:1512570903886692474> ALERTE ATTAQUE")
                            embed = discord.Embed(title=titre, description=f"> {line_msg}", color=discord.Color.red())
                            view = AlertButtonView(role_id=NOTIFICATION_ROLE_ID, langue=langue)
                            await atk_chan.send(embed=embed, view=view)

            return web.Response(text="OK", status=200)
        except Exception as e:
            logger.error(f"❌ Erreur LineBridge : {e}")
            return web.Response(text="Error", status=500)


async def setup(bot: commands.Bot):
    await bot.add_cog(LineBridgeCog(bot))