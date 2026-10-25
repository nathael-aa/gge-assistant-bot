import logging
import os
from datetime import datetime

import discord
from discord.ext import commands

from utils import (
    ADMINS_DIR,
    async_json_dump,
    async_json_load,
    load_blocks_async,
    save_blocks_async,
    t,
)

logger = logging.getLogger("GGE_Bot")


class AdminModeration(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.admin_lang = getattr(bot, "admin_lang", "fr")

    @commands.command(name="ban_cmd", hidden=True)
    async def ban_cmd(self, ctx, command: str, *, reason: str = "🚧 Maintenance."):
        cmd_clean = command.replace("/", "").strip()
        valid_commands = set()
        for cmd in self.bot.tree.walk_commands():
            valid_commands.add(cmd.qualified_name)
            if getattr(cmd, "parent", None):
                valid_commands.add(cmd.parent.qualified_name)

        if cmd_clean not in valid_commands:
            msg = t(
                self.admin_lang,
                "admin_ban_cmd_not_found",
                cmd=cmd_clean,
                defaut=f"❌ **Erreur** : La commande `/{cmd_clean}` n'existe pas.",
            )
            return await ctx.send(msg)

        data = await load_blocks_async()
        if "global_commands" not in data:
            data["global_commands"] = {}

        data["global_commands"][cmd_clean] = reason
        await save_blocks_async(data)

        msg = t(
            self.admin_lang,
            "admin_ban_cmd",
            cmd=cmd_clean,
            reason=reason,
            defaut=f"⛔ **Videur** : La commande `/{cmd_clean}` est bloquée.",
        )
        await ctx.send(msg)

    @commands.command(name="unban_cmd", hidden=True)
    async def unban_cmd(self, ctx, command: str):
        data = await load_blocks_async()
        cmd_clean = command.replace("/", "").strip()

        if cmd_clean in data.get("global_commands", {}):
            del data["global_commands"][cmd_clean]
            await save_blocks_async(data)
            await ctx.send(f"✅ **Videur** : La commande `/{cmd_clean}` est de nouveau disponible.")
        else:
            await ctx.send(f"⚠️ La commande `/{cmd_clean}` n'était pas bloquée.")

    @commands.command(name="vigi_add", hidden=True)
    async def vigi_add(self, ctx, *, cible: str):
        vigi_file = ADMINS_DIR / "vigilance.json"
        cibles = []
        if vigi_file.exists():
            try:
                cibles_data = await async_json_load(vigi_file)
                cibles = cibles_data if isinstance(cibles_data, list) else cibles_data.get("cibles", [])
            except Exception:
                cibles = []

        cible_clean = cible.lower().strip()
        if cible_clean not in cibles:
            cibles.append(cible_clean)
            await async_json_dump(vigi_file, cibles, indent=2, ensure_ascii=True)
            await ctx.send("🕵️ **Surveillance activée.**")
        else:
            await ctx.send(f"⚠️ `{cible}` est déjà sous surveillance.")

    # --- LE MOTEUR D'INTERCEPTION INVISIBLE ---
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.application_command:
            return

        webhook_url = os.getenv("WEBHOOK_VIGILANCE")
        if not webhook_url or not webhook_url.startswith("http"):
            return

        vigi_file = ADMINS_DIR / "vigilance.json"
        if not vigi_file.exists():
            return

        try:
            cibles = await async_json_load(vigi_file)
        except Exception:
            return

        if not cibles:
            return

        def extract_values(options):
            vals = []
            for opt in options:
                if "value" in opt:
                    vals.append(str(opt["value"]).lower())
                if "options" in opt:
                    vals.extend(extract_values(opt["options"]))
            return vals

        inputs = extract_values(interaction.data.get("options", []))
        match_trouve = next((cible for cible in cibles for val in inputs if cible in val), None)

        if match_trouve:
            self.bot.loop.create_task(self._send_vigilance_alert(interaction, match_trouve, webhook_url))

    async def _send_vigilance_alert(self, interaction: discord.Interaction, match_trouve: str, webhook_url: str):
        cmd_name = interaction.data.get("name", "inconnue")
        timestamp_discord = int(datetime.now().timestamp())

        embed = discord.Embed(
            title="🚨 DÉTECTION VIGILANCE",
            description="Une cible placée sous écoute vient d'être utilisée dans une commande.",
            color=0xFCB329,
        )
        embed.add_field(
            name="👤 Exécuté par", value=f"{interaction.user.mention}\n(`{interaction.user.id}`)", inline=True
        )
        embed.add_field(name="🛡️ Serveur", value=f"{interaction.guild.name if interaction.guild else 'MP'}", inline=True)
        embed.add_field(name="🎯 Compte surveillé", value=f"`{match_trouve}`", inline=False)
        embed.add_field(name="💻 Commande", value=f"`/{cmd_name}`", inline=False)

        try:
            # Utilisation de la session globale (BPA-09)
            webhook = discord.Webhook.from_url(webhook_url, session=self.bot.session)
            await webhook.send(embed=embed, username="GGE Videur 🕵️", avatar_url=self.bot.user.display_avatar.url)
        except Exception as e:
            logger.error(f"❌ Erreur d'envoi Webhook Vigilance : {e}")
