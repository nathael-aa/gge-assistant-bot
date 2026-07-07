# -*- coding: utf-8 -*-
import os
import sys
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')

async def envoyer_alerte():
    if not TOKEN:
        print("❌ ERREUR : Le token DISCORD_TOKEN est introuvable dans les variables d'environnement.")
        return

    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        try:
            user = await bot.fetch_user(1166375576685265040)
            script_nom = sys.argv[1] if len(sys.argv) > 1 else "Inconnu"
            
            dossier = "general"
            if "server" in script_nom.lower(): dossier = "serveur"
            elif "murs" in script_nom.lower(): dossier = "murs"
            
            await user.send(f"🚨 **ALERTE CRITIQUE : ÉCHEC DU SCANNER**\nLe script `{script_nom}` a échoué sur le NAS. Vérifie les logs dans `/app/logs/{dossier}/`.")
            print(f"✅ Alerte envoyée à {user.name}")
        except Exception as e:
            print(f"❌ Impossible d'envoyer le MP : {e}")
        finally:
            await bot.close()

    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(envoyer_alerte())