import hashlib
import io
import re
from datetime import datetime

import discord
from discord.ext import commands

from utils import CONFIG_DIR, SERVEURS_DIR, async_json_dump, async_json_load


class AdminCalendar(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _reload_calendar_cache(self, ctx):
        """Fonction interne pour forcer la mise à jour du CalendrierCog en mémoire"""
        cog = self.bot.get_cog("CalendrierCog")
        if cog:
            await cog.load_cache_from_file()
            if hasattr(cog, "load_event_mapping"):
                cog.event_mapping = cog.load_event_mapping()
            await ctx.send("♻️ *Le cache du calendrier a été mis à jour en mémoire.*")
        else:
            await ctx.send("⚠️ *Attention : Le module Calendrier n'est pas chargé. Redémarre le bot pour appliquer.*")

    @commands.command(name="cal_keys", hidden=True)
    async def cal_keys(self, ctx):
        """[CACHÉE] Liste toutes les clés valides pour ajouter/modifier un événement."""

        mapping_file = CONFIG_DIR / "event_mapping.json"

        if not mapping_file.exists():
            return await ctx.send("❌ Fichier `event_mapping.json` introuvable.")

        mapping = await async_json_load(mapping_file)

        embed = discord.Embed(
            title="🔑 Clés d'Événements GGE",
            description="Voici la liste exacte des clés à utiliser pour les commandes `!cal_add` et `!cal_mapping`.\n⚠️ **N'oublie pas de mettre des guillemets `\" \"` s'il y a des espaces !**",
            color=discord.Color.gold(),
        )

        lignes = []
        for key, data in mapping.items():
            emoji = data.get("emoji", "📅")
            nom = data.get("name_default", key.title())
            lignes.append(f'{emoji} **{nom}** ➔ `"{key}"`')

        embed.add_field(name="Clés valides", value="\n".join(lignes), inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="cal_list", hidden=True)
    async def cal_list(self, ctx):
        """[CACHÉE] Liste tous les événements actifs avec leurs IDs."""

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
            start_str = ev["start"].replace("T", " ")
            end_str = ev["end"].replace("T", " ")
            lignes.append(f"[{ev['id']}] {ev['key'].upper()} : {start_str} ➔ {end_str}")

        buffer = io.BytesIO("\n".join(lignes).encode("utf-8"))
        fichier = discord.File(fp=buffer, filename="cal_events_ids.txt")

        await ctx.send(f"✅ **{len(events)} événements trouvés.** Voici la liste avec les IDs :", file=fichier)

    @commands.command(name="cal_add", hidden=True)
    async def cal_add(self, ctx, event_key: str, start: str, end: str):
        """[CACHÉE] Ajoute un event. Ex: !cal_add "rift raid" "15/09/2026 11:00" "22/09/2026 09:00" """

        try:
            dt_start = datetime.strptime(start, "%d/%m/%Y %H:%M")
            dt_end = datetime.strptime(end, "%d/%m/%Y %H:%M")
        except ValueError:
            return await ctx.send('❌ Format de date invalide. Utilise : `"JJ/MM/AAAA HH:MM"`')

        mapping_file = CONFIG_DIR / "event_mapping.json"
        mapping = await async_json_load(mapping_file)

        if event_key.lower() not in mapping:
            cles = ", ".join(f"`{k}`" for k in mapping.keys())
            return await ctx.send(f"❌ La clé `{event_key}` n'existe pas. Clés valides : {cles}")

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

    @commands.command(name="cal_edit", hidden=True)
    async def cal_edit(self, ctx, event_id: str, start: str, end: str):
        """[CACHÉE] Modifie les dates. Ex: !cal_edit 1a2b3 "15/09/2026 11:00" "22/09/2026 09:00" """

        try:
            dt_start = datetime.strptime(start, "%d/%m/%Y %H:%M")
            dt_end = datetime.strptime(end, "%d/%m/%Y %H:%M")
        except ValueError:
            return await ctx.send('❌ Format de date invalide. Utilise : `"JJ/MM/AAAA HH:MM"`')

        cal_file = SERVEURS_DIR / "calendrier.json"

        data = await async_json_load(cal_file)

        trouve = False
        for ev in data.get("cached_events", []):
            if ev.get("id") == event_id:
                ev["start"] = dt_start.isoformat()
                ev["end"] = dt_end.isoformat()
                trouve = True
                break

        if not trouve:
            return await ctx.send(f"❌ Aucun événement trouvé avec l'ID `{event_id}`.")

        await async_json_dump(cal_file, data, indent=2, ensure_ascii=False)

        await ctx.send(f"✅ Événement `{event_id}` mis à jour :\n**Début:** {start}\n**Fin:** {end}")
        await self._reload_calendar_cache(ctx)

    @commands.command(name="cal_del", hidden=True)
    async def cal_del(self, ctx, event_id: str):
        """[CACHÉE] Supprime un événement du calendrier. Ex: !cal_del 1a2b3"""

        cal_file = SERVEURS_DIR / "calendrier.json"

        data = await async_json_load(cal_file)

        events = data.get("cached_events", [])
        events_filtres = [ev for ev in events if ev.get("id") != event_id]

        if len(events) == len(events_filtres):
            return await ctx.send(f"❌ Aucun événement trouvé avec l'ID `{event_id}`.")

        data["cached_events"] = events_filtres

        if "deleted_events" not in data:
            data["deleted_events"] = []
        if event_id not in data["deleted_events"]:
            data["deleted_events"].append(event_id)

        await async_json_dump(cal_file, data, indent=2, ensure_ascii=False)

        await ctx.send(
            f"🗑️ Événement `{event_id}` supprimé et ajouté à la **liste noire** (il sera ignoré par les futurs scans)."
        )
        await self._reload_calendar_cache(ctx)

    @commands.command(name="cal_mapping", hidden=True)
    async def cal_mapping(self, ctx, event_key: str, cible: str, heure: str):
        """[CACHÉE] Modifie l'heure par défaut. Ex: !cal_mapping "berimond" start "10:30" """

        if cible.lower() not in ["start", "end"]:
            return await ctx.send("❌ La cible doit être `start` ou `end`.")

        if not re.match(r"^\d{2}:\d{2}$", heure):
            return await ctx.send("❌ L'heure doit être au format `HH:MM` (ex: 11:00).")

        mapping_file = CONFIG_DIR / "event_mapping.json"

        mapping = await async_json_load(mapping_file)

        if event_key.lower() not in mapping:
            cles = ", ".join(f"`{k}`" for k in mapping.keys())
            return await ctx.send(f"❌ La clé `{event_key}` n'existe pas. Clés valides : {cles}")

        mapping[event_key.lower()][cible.lower()] = heure

        await async_json_dump(mapping_file, mapping, indent=2, ensure_ascii=False)

        await ctx.send(f"⚙️ Mapping de `{event_key}` mis à jour : **{cible.lower()}** ➔ `{heure}`.")
        await self._reload_calendar_cache(ctx)
