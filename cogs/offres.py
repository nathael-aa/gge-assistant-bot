import asyncio
import html
import json
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import (
    CONFIG_DIR,
    DICT_EMOJIS,
    async_json_dump,
    async_json_load,
    format_num,
    get_api_headers,
    get_server_config,
    t,
)

logger = logging.getLogger("GGE_Bot")

CAT_MAPPING = {
    "promos": ("🎁", "Gifts & Promos (Free)"),
    "supersale": ("🛍️", "Super Sales"),
    "eventoffers": ("⚔️", "Event Offers"),
    "dailybundles": ("📅", "Daily Bundles"),
    "hcbundles": ("💰", "Ruby Bundles"),
    "hardCurrency": ("💎", "Direct Ruby Purchases"),
    "cashoffers": ("🛒", "Classic Offers"),
    "subscriptions": ("📜", "Subscriptions"),
}

CAT_COLORS = {
    "promos": discord.Color(0x2ECC71),
    "supersale": discord.Color(0xFF4757),
    "eventoffers": discord.Color(0xFF9F43),
    "dailybundles": discord.Color(0x1E90FF),
    "hcbundles": discord.Color(0xFFD700),
    "hardCurrency": discord.Color(0x8E44AD),
    "cashoffers": discord.Color(0xA4B0BD),
    "subscriptions": discord.Color(0x00CEC9),
}


class OfferCategorySelect(discord.ui.Select):
    def __init__(self, cog, guild_id: str, default_cats: list, alerts_file, base_desc: str):
        self.cog = cog
        self.guild_id = guild_id
        self.langue = "EN"  # Forcé en anglais
        self.alerts_file = alerts_file
        self.base_desc = base_desc

        options = []
        for key, (emoji, name_en) in CAT_MAPPING.items():
            name_local = t(self.langue, f"offres_cat_{key}", defaut=name_en)
            options.append(
                discord.SelectOption(label=name_local, value=key, emoji=emoji, default=(key in default_cats))
            )

        super().__init__(
            placeholder=t(self.langue, "offres_select_cat", defaut="Select categories to track..."),
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        data = await async_json_load(self.alerts_file) or {"guilds": {}}
        if "guilds" not in data:
            data["guilds"] = {}

        if self.guild_id in data["guilds"]:
            data["guilds"][self.guild_id]["categories"] = self.values
            prefs = data["guilds"][self.guild_id]
            await async_json_dump(self.alerts_file, data, indent=4)

            e_check = DICT_EMOJIS.get("e_check", "✅")
            cats_localized = [t(self.langue, f"offres_cat_{v}", defaut=CAT_MAPPING[v][1]) for v in self.values]
            cats_str = ", ".join(cats_localized)

            success_msg = t(
                self.langue,
                "offres_cat_saved",
                cats=cats_str,
                defaut=f"{e_check} **Categories saved!** Initializing the first scan...",
            )

            embed = interaction.message.embeds[0]
            embed.description = f"{self.base_desc}\n\n{success_msg}"
            embed.color = discord.Color.green()

            await interaction.response.edit_message(content=None, embed=embed, view=None)

            channel = interaction.guild.get_channel(prefs["channel_id"])
            if channel:
                asyncio.create_task(self.cog.trigger_immediate_scan(channel, self.guild_id, prefs))


class OffersCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.servers_dir = CONFIG_DIR.parent / "serveurs"
        self.alerts_file = self.servers_dir / "offers_alerts.json"
        self.cache_file = self.servers_dir / "offers_cache.json"

    async def cog_load(self):
        os.makedirs(self.servers_dir, exist_ok=True)

        for file_path in (self.alerts_file, self.cache_file):
            if not os.path.exists(file_path):
                with open(file_path, "w", encoding="utf-8") as f:
                    if file_path == self.alerts_file:
                        json.dump({"guilds": {}}, f)
                    else:
                        json.dump({}, f)

        if not self.offers_tracker_task.is_running():
            self.offers_tracker_task.start()

    async def cog_unload(self):
        self.offers_tracker_task.cancel()

    offers_group = app_commands.Group(
        name="offers",
        description="GGE offers and gifts tracker configuration",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @offers_group.command(name="setup", description="Configure automatic alerts for new offers and gifts.")
    @app_commands.describe(
        channel="The channel where alerts will be sent",
        profile="Select the player profile (Legendary or Normal)",
        ping_role="Optional role to ping when new offers arrive",
    )
    @app_commands.choices(
        profile=[
            app_commands.Choice(name="Legendary (Level 70 / LL 950+)", value="legendary"),
            app_commands.Choice(name="Normal (Level 70 / LL 0)", value="normal"),
        ]
    )
    @app_commands.checks.cooldown(1, 300, key=lambda i: i.guild_id)
    async def offers_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        profile: app_commands.Choice[str],
        ping_role: discord.Role = None,
    ):
        _, gge_server = await get_server_config(interaction)
        langue = "EN"  # Forcé en anglais
        guild_id = str(interaction.guild.id)

        legendary_level = 950 if profile.value == "legendary" else 0

        data = await async_json_load(self.alerts_file) or {"guilds": {}}
        if "guilds" not in data:
            data["guilds"] = {}

        default_cats = data["guilds"].get(guild_id, {}).get("categories", ["promos"])

        data["guilds"][guild_id] = {
            "gge_server": gge_server,
            "channel_id": channel.id,
            "lang": langue,
            "legendary": legendary_level,
            "categories": default_cats,
            "ping_role": ping_role.id if ping_role else None,
        }
        await async_json_dump(self.alerts_file, data, indent=4)

        prof_str = "Lvl 70 | LL 950+" if legendary_level == 950 else "Lvl 70 | LL 0"

        base_desc = t(
            langue,
            "offres_setup_base_desc_opti",
            prof=prof_str,
            channel=channel.mention,
            defaut=f"New offers for profile `{prof_str}` will be sent to {channel.mention} (Prices in EUR).",
        )

        if ping_role:
            base_desc += f"\n🔔 **Ping:** {ping_role.mention}"

        prompt_desc = t(langue, "offres_setup_prompt", defaut="\n\n👇 **Select the categories you want to monitor:**")

        view = discord.ui.View()
        view.add_item(OfferCategorySelect(self, guild_id, default_cats, self.alerts_file, base_desc))

        embed = discord.Embed(
            title=t(langue, "offres_setup_title", defaut="🛒 Offers Tracker Configuration"),
            description=base_desc + prompt_desc,
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @offers_setup.error
    async def offers_setup_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            msg = t(
                "EN",
                "cooldown_msg",
                sec=int(error.retry_after),
                defaut=f"⏳ Please wait **{int(error.retry_after)}s** before using this command again.",
            )
            await interaction.response.send_message(msg, ephemeral=True)

    @offers_group.command(name="stop", description="Disable the offers tracker for this server.")
    async def offers_stop(self, interaction: discord.Interaction):
        langue = "EN"
        guild_id = str(interaction.guild.id)
        data = await async_json_load(self.alerts_file) or {"guilds": {}}

        if "guilds" in data and guild_id in data["guilds"]:
            del data["guilds"][guild_id]
            await async_json_dump(self.alerts_file, data, indent=4)
            msg = t(langue, "offres_stop_success", defaut="🛑 The offers tracker has been disabled on this server.")
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            msg = t(langue, "offres_stop_fail", defaut="⚠️ The tracker was not enabled on this server.")
            await interaction.response.send_message(msg, ephemeral=True)

    def clean_gge_text(self, text: str) -> str:
        """Nettoie proprement les accents corrompus et les retours à la ligne."""
        if not text:
            return ""

        cleaned = text.replace("\n", " ").strip()
        cleaned = cleaned.replace("embl?me", "emblème").replace("B?atrice", "Béatrice").replace("?norme", "énorme")
        return html.unescape(cleaned)

    def build_offer_embed(self, offer: dict, category_key: str) -> discord.Embed:
        langue = "EN"  # Forcé en anglais
        price_raw = offer.get("price", 0)
        curr = offer.get("currencyCode", "EUR")

        cat_emoji, cat_name_en = CAT_MAPPING.get(category_key, ("🛒", "New Offer"))
        cat_name = t(langue, f"offres_cat_{category_key}", defaut=cat_name_en)

        name_raw = offer.get("name") or offer.get("title")
        if not name_raw or name_raw.strip() == "":
            name = cat_name
        else:
            name = self.clean_gge_text(name_raw)

        if price_raw == 0 or curr == "GGS-mock":
            color = discord.Color.green()
            if "gift" in name.lower() or "free" in name.lower():
                price_str = None
            else:
                price_str = "🎁 **FREE GIFT!**"
        else:
            price_val = price_raw if curr in ["JPY", "KRW"] else price_raw / 100
            price_str = f"💰 **{price_val:,.2f} {curr}**"
            color = CAT_COLORS.get(category_key, discord.Color.gold())

        embed = discord.Embed(title=f"{cat_emoji} {name}", description=price_str, color=color)

        teaser = offer.get("teaserImg") or offer.get("headerImageUrl")
        if teaser:
            embed.set_thumbnail(url=teaser)

        rewards = offer.get("rewards", [])
        rewards_lines = []
        for r in rewards:
            title_raw = r.get("title", "")
            title = self.clean_gge_text(title_raw)

            if any(
                phrase in title
                for phrase in ["Voici un cadeau", "cadeau de notre part", "Here's a gift", "gift from us"]
            ):
                continue

            qty = r.get("qty", 1)
            details = r.get("details", f"x{format_num(qty)}")
            details = self.clean_gge_text(details)

            if title:
                rewards_lines.append(f"• **{details}** {title}")
            else:
                rewards_lines.append(f"• {details}")

        if rewards_lines:
            chunk = "\n".join(rewards_lines)[:1020]
            embed.add_field(name=t(langue, "offres_content", defaut="Content:"), value=chunk, inline=False)

        footer_text = t(langue, "offres_footer", cat=cat_name, defaut=f"Category: {cat_name} • GGE Assistant")
        embed.set_footer(text=footer_text, icon_url=self.bot.user.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()

        return embed

    async def send_offers_to_channel(
        self, channel: discord.TextChannel, embeds: list, cat_key: str, ping_role_id: int = None
    ):
        """Helper factorisé pour créer un Thread, envoyer les embeds et identifier le rôle si nécessaire."""
        langue = "EN"
        cat_emoji, cat_name_en = CAT_MAPPING.get(cat_key, ("🛒", "Offers"))
        cat_name = t(langue, f"offres_cat_{cat_key}", defaut=cat_name_en)

        msg_text = t(
            langue,
            "offres_thread_msg",
            count=len(embeds),
            cat=cat_name,
            defaut=f"{cat_emoji} **{len(embeds)} new offers** are available in the **{cat_name}** category!",
        )

        allowed_mentions = discord.AllowedMentions.none()

        if ping_role_id:
            role = channel.guild.get_role(ping_role_id)
            if ping_role_id == channel.guild.id or (role and role.name in ("@everyone", "@here")):
                tag = role.name if role else "@everyone"
                msg_text = f"{tag} {msg_text}"
                allowed_mentions = discord.AllowedMentions(everyone=True)
            else:
                msg_text = f"<@&{ping_role_id}> {msg_text}"
                allowed_mentions = discord.AllowedMentions(roles=True)

        try:
            parent_msg = await channel.send(content=msg_text, allowed_mentions=allowed_mentions)
            thread = await parent_msg.create_thread(name=f"{cat_emoji} {cat_name} Offers", auto_archive_duration=1440)

            for i in range(0, len(embeds), 10):
                await thread.send(embeds=embeds[i : i + 10])
                await asyncio.sleep(1.0)
        except Exception as e:
            logger.error(f"Error creating thread/sending offers to {channel.id}: {e}")

    async def trigger_immediate_scan(self, channel: discord.TextChannel, guild_id: str, prefs: dict):
        """Récupère et envoie immédiatement les offres actuelles (optimisé 100% anglais)."""
        headers = await get_api_headers()
        leg = 950 if prefs.get("legendary", 0) > 0 else 0
        url = f"https://api.gge-tracker.com/api/v1/offers?locale=en&currency=EUR&level=70&legendaryLevel={leg}"

        try:
            async with self.bot.session.get(url, headers=headers, timeout=10) as r:
                if r.status != 200:
                    return
                api_data = await r.json()
        except Exception as e:
            logger.error(f"❌ Immediate Scan Error for {guild_id} : {e}")
            return

        cache = await async_json_load(self.cache_file) or {}
        param_key = f"70_{leg}"
        if param_key not in cache:
            cache[param_key] = {}

        cache_modified = False

        for cat_key in prefs.get("categories", []):
            if cat_key not in api_data or not isinstance(api_data[cat_key], dict):
                continue
            offers = api_data[cat_key].get("data", {}).get("offers", [])
            if not offers:
                continue

            cat_embeds = [self.build_offer_embed(o, cat_key) for o in offers]

            if cat_embeds:
                await self.send_offers_to_channel(channel, cat_embeds, cat_key, prefs.get("ping_role"))
                await asyncio.sleep(1.5)

            current_ids = {str(o["id"]) for o in offers if "id" in o}
            existing_cache = set(cache[param_key].get(cat_key, []))

            cache[param_key][cat_key] = list(existing_cache | current_ids)
            cache_modified = True

        if cache_modified:
            await async_json_dump(self.cache_file, cache)

    @tasks.loop(hours=12)
    async def offers_tracker_task(self):
        """Scans GGE-Tracker every 12 hours. Optimisé à 2 requêtes API max (100% EN)."""
        if not os.path.exists(self.alerts_file):
            return

        try:
            raw_configs = await async_json_load(self.alerts_file)
            configs = raw_configs.get("guilds", {}) if raw_configs else {}
        except FileNotFoundError:
            return

        if not configs:
            return

        cache = await async_json_load(self.cache_file) or {}

        groups = {0: [], 950: []}
        for guild_id, prefs in configs.items():
            leg = 950 if prefs.get("legendary", 0) > 0 else 0
            groups[leg].append((guild_id, prefs))

        headers = await get_api_headers()
        cache_modified = False

        for leg_val, guilds in groups.items():
            if not guilds:
                continue

            url = f"https://api.gge-tracker.com/api/v1/offers?locale=en&currency=EUR&level=70&legendaryLevel={leg_val}"
            param_key = f"70_{leg_val}"

            try:
                async with self.bot.session.get(url, headers=headers, timeout=10) as r:
                    if r.status != 200:
                        continue
                    api_data = await r.json()
            except Exception as e:
                logger.error(f"❌ Offers Tracker Error for {param_key} : {e}")
                continue

            if param_key not in cache:
                cache[param_key] = {}

            channel_deliveries = {}

            for cat_key in CAT_MAPPING.keys():
                if cat_key not in api_data or not isinstance(api_data[cat_key], dict):
                    continue

                offers = api_data[cat_key].get("data", {}).get("offers", [])
                if not offers:
                    continue

                current_ids = {str(o["id"]) for o in offers if "id" in o}
                cached_ids = set(cache[param_key].get(cat_key, []))

                if not cached_ids:
                    cache[param_key][cat_key] = list(current_ids)
                    cache_modified = True
                    continue

                new_ids = current_ids - cached_ids
                if new_ids:
                    cache[param_key][cat_key] = list(current_ids)
                    cache_modified = True

                    new_offers = [o for o in offers if str(o.get("id")) in new_ids]

                    for guild_id, prefs in guilds:
                        if cat_key in prefs.get("categories", []):
                            c_id = prefs["channel_id"]
                            cat_embeds = [self.build_offer_embed(o, cat_key) for o in new_offers]

                            if c_id not in channel_deliveries:
                                channel_deliveries[c_id] = {"categories": {}, "ping_role": prefs.get("ping_role")}
                            channel_deliveries[c_id]["categories"][cat_key] = cat_embeds

            for channel_id, data in channel_deliveries.items():
                try:
                    channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                    if not channel:
                        continue

                    for cat_key, embeds in data["categories"].items():
                        await self.send_offers_to_channel(channel, embeds, cat_key, data["ping_role"])
                        await asyncio.sleep(1.5)

                except discord.Forbidden:
                    pass
                except Exception as e:
                    logger.error(f"Error sending offers to {channel_id}: {e}")

            await asyncio.sleep(2)

        if cache_modified:
            await async_json_dump(self.cache_file, cache)

    @offers_tracker_task.before_loop
    async def before_tracker(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(OffersCog(bot))
