import hashlib
import io
from datetime import datetime

import discord
from discord.ext import commands

from utils import CONFIG_DIR, SERVEURS_DIR, async_json_dump, async_json_load


class AdminCalendar(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _reload_calendar_cache(self, ctx):
        cog = self.bot.get_cog("CalendrierCog")
        if cog:
            await cog.load_cache_from_file()
            if hasattr(cog, "load_event_mapping"):
                cog.event_mapping = cog.load_event_mapping()
            await ctx.send("♻️ *Le cache du calendrier a été mis à jour en mémoire.*")
        else:
            await ctx.send("⚠️ *Attention : Le module Calendrier n'est pas chargé.*")

    @commands.command(name="cal_keys", hidden=True)
    async def cal_keys(self, ctx):
        mapping_file = CONFIG_DIR / "event_mapping.json"
        if not mapping_file.exists():
            return await ctx.send("❌ Fichier `event_mapping.json` introuvable.")
        mapping = await async_json_load(mapping_file)

        embed = discord.Embed(
            title="🔑 Clés d'Événements GGE",
            description='Voici la liste exacte des clés à utiliser pour les commandes `!cal_add`.\n⚠️ **Mettre des guillemets `" "` s\'il y a des espaces !**',
            color=discord.Color.gold(),
        )
        lignes = [
            f'{data.get("emoji", "📅")} **{data.get("name_default", key.title())}** ➔ `"{key}"`'
            for key, data in mapping.items()
        ]
        embed.add_field(name="Clés valides", value="\n".join(lignes), inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="cal_list", hidden=True)
    async def cal_list(self, ctx):
        cal_file = SERVEURS_DIR / "calendrier.json"
        if not cal_file.exists():
            return await ctx.send("❌ Fichier calendrier.json introuvable.")

        data = await async_json_load(cal_file)
        events = data.get("cached_events", [])
        if not events:
            return await ctx.send("ℹ️ Aucun événement en cache.")

        events = sorted(events, key=lambda x: x["start"])
        lignes = ["=== LISTE DES ÉVÉNEMENTS GGE ===", f"Généré le : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"]
        for ev in events:
            lignes.append(
                f"[{ev['id']}] {ev['key'].upper()} : {ev['start'].replace('T', ' ')} ➔ {ev['end'].replace('T', ' ')}"
            )

        buffer = io.BytesIO("\n".join(lignes).encode("utf-8"))
        fichier = discord.File(fp=buffer, filename="cal_events_ids.txt")
        await ctx.send(f"✅ **{len(events)} événements trouvés.**", file=fichier)

    @commands.command(name="cal_add", hidden=True)
    async def cal_add(self, ctx, event_key: str, start: str, end: str):
        try:
            dt_start = datetime.strptime(start, "%d/%m/%Y %H:%M")
            dt_end = datetime.strptime(end, "%d/%m/%Y %H:%M")
        except ValueError:
            return await ctx.send('❌ Format de date invalide. Utilise : `"JJ/MM/AAAA HH:MM"`')

        mapping_file = CONFIG_DIR / "event_mapping.json"
        mapping = await async_json_load(mapping_file)

        if event_key.lower() not in mapping:
            return await ctx.send(f"❌ La clé `{event_key}` n'existe pas.")

        cal_file = SERVEURS_DIR / "calendrier.json"
        data = await async_json_load(cal_file)

        uid = f"{event_key.lower()}_{int(dt_start.timestamp())}"
        new_id = hashlib.md5(uid.encode()).hexdigest()[:5]

        data.setdefault("cached_events", []).append(
            {"id": new_id, "key": event_key.lower(), "start": dt_start.isoformat(), "end": dt_end.isoformat()}
        )
        await async_json_dump(cal_file, data, indent=2, ensure_ascii=False)
        await ctx.send(f"✅ Événement `{event_key}` ajouté avec l'ID `{new_id}`.")
        await self._reload_calendar_cache(ctx)
