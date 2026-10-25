import logging

from discord.ext import commands

from utils import MON_ID_DISCORD

from .calendar import AdminCalendar
from .i18n import AdminI18n
from .moderation import AdminModeration
from .system import AdminSystem

logger = logging.getLogger("GGE_Bot")


class AdminMasterCog(AdminSystem, AdminCalendar, AdminModeration, AdminI18n, commands.Cog, name="Admin"):
    """Panneau de Contrôle Administrateur global."""

    def __init__(self, bot):
        self.bot = bot
        self.admin_lang = "fr"

        # Initialisation de tous les sous-modules
        AdminSystem.__init__(self, bot)
        AdminCalendar.__init__(self, bot)
        AdminModeration.__init__(self, bot)
        AdminI18n.__init__(self, bot)

    def cog_check(self, ctx):
        """Filtre global : Seul le créateur peut utiliser ce module."""
        # Autorise les commandes avec le préfixe uniquement si c'est toi
        return ctx.author.id == MON_ID_DISCORD


async def setup(bot):
    await bot.add_cog(AdminMasterCog(bot))
