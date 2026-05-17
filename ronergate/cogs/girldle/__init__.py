"""Girldle cog."""

from __future__ import annotations

from discord.ext import commands

from .cog import GirldleCog


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GirldleCog(bot))
