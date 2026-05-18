"""Bot-owner-only Girldle controls. Guild-restricted to OWNER_GUILD_ID."""

from __future__ import annotations

import logging
import os
import re

import discord
from discord import app_commands
from discord.ext import commands

from ...db import Database
from ..girldle import ingest

log = logging.getLogger(__name__)


def _resolve_owner_guild_id() -> int:
    raw = os.environ.get("OWNER_GUILD_ID", "").strip()
    if not raw:
        raise RuntimeError(
            "girldle_admin cog requires OWNER_GUILD_ID env var. "
            "If you don't have a home guild, don't list this cog in COGS."
        )
    return int(raw)


_OWNER_GUILD_ID = _resolve_owner_guild_id()
_OWNER_GUILD = discord.Object(id=_OWNER_GUILD_ID)


class _ApprovalButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"girldle_approve:(?P<guild_id>\d+)",
):
    """One-click approval button. Persistent across bot restarts via dynamic custom_id."""

    def __init__(self, guild_id: int, label: str | None = None):
        super().__init__(
            discord.ui.Button(
                label=(label or "Approve")[:80],
                style=discord.ButtonStyle.success,
                custom_id=f"girldle_approve:{guild_id}",
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> _ApprovalButton:
        return cls(int(match["guild_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await interaction.client.is_owner(interaction.user):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        db: Database = interaction.client.db  # type: ignore[attr-defined]
        with db.transaction() as conn:
            conn.execute(
                "UPDATE girldle_config SET approved = 1 WHERE guild_id = ?",
                (str(self.guild_id),),
            )
        await interaction.response.send_message(
            f"✅ Approved guild `{self.guild_id}`.", ephemeral=True
        )


def _pending_approval_view(pending: list[tuple[int, str]]) -> discord.ui.View | None:
    if not pending:
        return None
    view = discord.ui.View(timeout=None)
    for guild_id, label in pending[:25]:
        view.add_item(_ApprovalButton(guild_id, label=f"Approve {label}"))
    return view


def _guild_name_from_db(db: Database, guild_id: str) -> str | None:
    row = db.conn.execute(
        "SELECT name FROM girldle_config WHERE guild_id = ?", (guild_id,)
    ).fetchone()
    return row["name"] if row and row["name"] else None


class GirldleAdminCog(commands.Cog):
    girldleadmin = app_commands.Group(
        name="girldleadmin",
        description="Bot-owner Girldle controls.",
        guild_ids=[_OWNER_GUILD_ID],
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(_ApprovalButton)

    @girldleadmin.command(
        name="approve",
        description="Approve a server for the global leaderboard, or revoke approval.",
    )
    @app_commands.describe(
        guild_id="Numeric guild ID",
        approved="true = visible globally, false = revoke approval",
    )
    async def approve_cmd(
        self,
        interaction: discord.Interaction,
        guild_id: str,
        approved: bool = True,
    ) -> None:
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        try:
            gid = int(guild_id)
        except ValueError:
            await interaction.response.send_message("`guild_id` must be a number.")
            return
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE girldle_config SET approved = ? WHERE guild_id = ?",
                (1 if approved else 0, str(gid)),
            )
            if cursor.rowcount == 0:
                await interaction.response.send_message(
                    f"No config row for guild `{gid}`. They need to run `/girldle setup` first."
                )
                return
        verb = "approved" if approved else "revoked"
        await interaction.response.send_message(f"Guild `{gid}` {verb}.")

    @girldleadmin.command(
        name="servers",
        description="List all servers known to the bot and their state.",
    )
    async def servers_cmd(self, interaction: discord.Interaction) -> None:
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        rows = list(
            self.db.conn.execute(
                """
                SELECT c.guild_id, c.channel_id, c.approved, c.private, c.name,
                       (SELECT COUNT(*) FROM girldle_posts WHERE guild_id = c.guild_id) AS posts,
                       (SELECT COUNT(DISTINCT user_id) FROM girldle_posts
                        WHERE guild_id = c.guild_id) AS players
                FROM girldle_config c
                ORDER BY posts DESC, c.guild_id ASC
                """
            )
        )
        if not rows:
            await interaction.response.send_message("No servers configured.")
            return
        lines: list[str] = []
        pending: list[tuple[int, str]] = []
        for row in rows:
            name = row["name"] or "_(unknown — bot hasn't seen this guild yet)_"
            if row["approved"] and not row["private"]:
                marker = "✅"
            elif row["private"]:
                marker = "🔒"
            else:
                marker = "⏳"
                pending.append((int(row["guild_id"]), row["name"] or row["guild_id"]))
            lines.append(
                f"{marker} **{name}** · `{row['guild_id']}` · "
                f"<#{row['channel_id']}> · {row['posts']} posts · {row['players']} players"
            )
        embed = discord.Embed(
            title=f"Girldle servers ({len(rows)})",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="✅ approved · ⏳ pending approval · 🔒 private")
        view = _pending_approval_view(pending)
        if view is not None:
            await interaction.response.send_message(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed)

    @girldleadmin.command(
        name="remove",
        description="Remove a server's config and wipe its posts.",
    )
    @app_commands.describe(guild_id="Numeric guild ID to remove")
    async def remove_cmd(self, interaction: discord.Interaction, guild_id: str) -> None:
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        try:
            gid = int(guild_id)
        except ValueError:
            await interaction.response.send_message("`guild_id` must be a number.")
            return
        with self.db.transaction() as conn:
            config_cursor = conn.execute(
                "DELETE FROM girldle_config WHERE guild_id = ?", (str(gid),)
            )
            post_cursor = conn.execute(
                "DELETE FROM girldle_posts WHERE guild_id = ?", (str(gid),)
            )
            result_cursor = conn.execute(
                """
                DELETE FROM girldle_results
                WHERE (user_id, puzzle_date) NOT IN (
                    SELECT user_id, puzzle_date FROM girldle_posts
                )
                """
            )
        if config_cursor.rowcount == 0 and post_cursor.rowcount == 0:
            await interaction.response.send_message(f"No data for guild `{gid}`.")
            return
        ingest.recompute_ratings(self.db)
        await interaction.response.send_message(
            f"Removed guild `{gid}`: dropped config, "
            f"{post_cursor.rowcount} post{'s' if post_cursor.rowcount != 1 else ''}, "
            f"{result_cursor.rowcount} orphan canonical "
            f"result{'s' if result_cursor.rowcount != 1 else ''}."
        )
