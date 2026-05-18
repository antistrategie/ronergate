"""Girldle admin cog. Owner-only commands, guild-restricted to OWNER_GUILD_ID."""

from __future__ import annotations

from discord.ext import commands

from .cog import GirldleAdminCog


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GirldleAdminCog(bot))
