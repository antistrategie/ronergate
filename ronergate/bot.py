"""Bot entry point."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from . import errors
from .cogs.girldle.migrate import migrate as girldle_migrate
from .config import Config, load
from .db import Database

log = logging.getLogger(__name__)


class ROnergate(commands.Bot):
    def __init__(self, config: Config, db: Database):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!unused!", intents=intents)
        self.config = config
        self.db = db

    async def setup_hook(self) -> None:
        self.tree.on_error = self._on_app_command_error  # type: ignore[assignment]
        for ext in self.config.cogs:
            await self.load_extension(ext)
        await self.tree.sync()
        if self.config.owner_guild_id is not None:
            await self.tree.sync(guild=discord.Object(id=self.config.owner_guild_id))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else None)
        # Cache the names of every guild we're in so the leaderboard can
        # render them even from a bot that isn't a member of that guild
        # (we share the DB across bot instances).
        for guild in self.guilds:
            self.db.conn.execute(
                "UPDATE girldle_config SET name = ? WHERE guild_id = ?",
                (guild.name, str(guild.id)),
            )
        # Clear any guild-scoped commands left over from earlier deploys.
        # Skip the owner_guild_id since we intentionally use guild-scoped
        # commands there for admin tools.
        for guild in self.guilds:
            if guild.id == self.config.owner_guild_id:
                continue
            existing = await self.tree.fetch_commands(guild=guild)
            if existing:
                log.info("clearing %d guild commands in %s", len(existing), guild.name)
                self.tree.clear_commands(guild=guild)
                await self.tree.sync(guild=guild)

    async def _on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        underlying = getattr(error, "original", error)
        cmd_name = interaction.command.qualified_name if interaction.command else "<unknown>"
        await errors.report(self, f"`/{cmd_name}` failed for {interaction.user}", underlying)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("Something went wrong. Logged.", ephemeral=True)
            else:
                await interaction.response.send_message(
                    "Something went wrong. Logged.", ephemeral=True
                )
        except discord.HTTPException:
            pass


async def _run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load()
    db = Database(config.db_path)
    cogs_dir = Path(__file__).parent / "cogs"
    db.bootstrap(cogs_dir)
    girldle_migrate(db.conn)

    bot = ROnergate(config, db)
    try:
        await bot.start(config.discord_token)
    finally:
        db.close()


def main() -> None:
    asyncio.run(_run())
