"""Girldle command group + passive message handler."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from ... import errors
from ...config import Config
from ...db import Database
from . import analysis, ingest
from .parser import parse

LEADERBOARD_LIMIT = 100
NAME_DISPLAY_WIDTH = 18
PROVISIONAL_RD = 150

log = logging.getLogger(__name__)

RESET_CONFIRM_PHRASE = "wipe-girldle"


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
    """View with one Approve button per pending (guild_id, label) tuple. None if empty."""
    if not pending:
        return None
    view = discord.ui.View(timeout=None)
    for guild_id, label in pending[:25]:  # Discord cap of 25 components per message
        view.add_item(_ApprovalButton(guild_id, label=f"Approve {label}"))
    return view


def _girldle_channel_for(db: Database, guild_id: int) -> int | None:
    row = db.conn.execute(
        "SELECT channel_id FROM girldle_config WHERE guild_id = ?",
        (str(guild_id),),
    ).fetchone()
    return int(row["channel_id"]) if row else None


class GirldleCog(commands.Cog):
    girldle = app_commands.Group(
        name="girldle",
        description="Girldle stats and admin commands.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config: Config = bot.config  # type: ignore[attr-defined]
        self.db: Database = bot.db  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(_ApprovalButton)

    def _guild_name(self, guild_id: str | None) -> str | None:
        if not guild_id:
            return None
        guild = self.bot.get_guild(int(guild_id))
        return guild.name if guild else None

    def _is_girldle_channel(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False
        configured = _girldle_channel_for(self.db, message.guild.id)
        return configured is not None and message.channel.id == configured

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not self._is_girldle_channel(message):
            return
        try:
            await ingest.handle_message(self.bot, self.db, message)
        except Exception as e:
            await errors.report(self.bot, f"ingest failed for {message.jump_url}", e)

    @commands.Cog.listener()
    async def on_message_edit(
        self, before: discord.Message, after: discord.Message
    ) -> None:
        if after.author.bot:
            return
        if not self._is_girldle_channel(after):
            return
        try:
            self._purge_message(after.id)
            await ingest.handle_message(self.bot, self.db, after)
        except Exception as e:
            await errors.report(self.bot, f"edit re-ingest failed for {after.jump_url}", e)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if not self._is_girldle_channel(message):
            return
        try:
            self._purge_message(message.id)
        except Exception as e:
            await errors.report(self.bot, f"delete cleanup failed for {message.id}", e)

    def _purge_message(self, message_id: int) -> None:
        """Remove a single message from posts + drop the canonical result if no posts remain."""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT user_id, puzzle_date FROM girldle_posts WHERE message_id = ?",
                (str(message_id),),
            ).fetchone()
            conn.execute("DELETE FROM girldle_posts WHERE message_id = ?", (str(message_id),))
            if row is None:
                # Pre-posts-table row: also remove from canonical
                conn.execute(
                    "DELETE FROM girldle_results WHERE message_id = ?", (str(message_id),)
                )
                return
            still_posted = conn.execute(
                "SELECT 1 FROM girldle_posts WHERE user_id = ? AND puzzle_date = ? LIMIT 1",
                (row["user_id"], row["puzzle_date"]),
            ).fetchone()
            if still_posted is None:
                conn.execute(
                    "DELETE FROM girldle_results WHERE user_id = ? AND puzzle_date = ?",
                    (row["user_id"], row["puzzle_date"]),
                )

    @girldle.command(name="leaderboard", description="All-time Glicko-2 ratings.")
    @app_commands.describe(
        scope=(
            "global = everyone (default), "
            "server = only members who've played in this server"
        ),
    )
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        scope: Literal["global", "server"] = "global",
    ) -> None:
        ingest.recompute_ratings(self.db)

        if scope == "server":
            if interaction.guild is None:
                await interaction.response.send_message("Server scope only works in a server.")
                return
            rows = list(
                self.db.conn.execute(
                    """
                    SELECT p.user_id, p.display_name, p.rating, p.rd,
                           p.games_played, p.last_played, NULL AS primary_guild_id
                    FROM girldle_players p
                    WHERE p.games_played >= 1
                      AND p.user_id IN (
                          SELECT user_id FROM girldle_posts WHERE guild_id = ?
                      )
                    ORDER BY (p.rating - 3 * p.rd) DESC
                    LIMIT ?
                    """,
                    (str(interaction.guild.id), LEADERBOARD_LIMIT),
                )
            )
            scope_label = f"this server ({interaction.guild.name})"
        else:
            rows = list(
                self.db.conn.execute(
                    """
                    SELECT p.user_id, p.display_name, p.rating, p.rd,
                           p.games_played, p.last_played,
                           (
                               SELECT guild_id FROM girldle_posts
                               WHERE user_id = p.user_id
                                 AND guild_id IN (
                                     SELECT guild_id FROM girldle_config
                                     WHERE approved = 1 AND private = 0
                                 )
                               GROUP BY guild_id
                               ORDER BY COUNT(*) DESC
                               LIMIT 1
                           ) AS primary_guild_id
                    FROM girldle_players p
                    WHERE p.games_played >= 1
                      AND EXISTS (
                          SELECT 1 FROM girldle_posts po
                          WHERE po.user_id = p.user_id
                            AND po.guild_id IN (
                                SELECT guild_id FROM girldle_config
                                WHERE approved = 1 AND private = 0
                            )
                      )
                    ORDER BY (p.rating - 3 * p.rd) DESC
                    LIMIT ?
                    """,
                    (LEADERBOARD_LIMIT,),
                )
            )
            scope_label = "global"

        if not rows:
            await interaction.response.send_message(f"No Girldle results yet ({scope_label}).")
            return

        today = date.today()
        lines: list[str] = []
        any_provisional = False
        for i, row in enumerate(rows, start=1):
            rank = _rank_prefix(i)
            name = _truncate(row["display_name"] or row["user_id"], NAME_DISPLAY_WIDTH)
            last = _format_last_played(row["last_played"], today)
            games = row["games_played"]
            games_label = f"{games} game{'s' if games != 1 else ''}"
            provisional = row["rd"] > PROVISIONAL_RD
            any_provisional = any_provisional or provisional
            rating_label = f"{int(row['rating'])}{'?' if provisional else ''}"
            line = f"{rank} **{name}** · {rating_label} · {games_label} · {last}"
            if scope == "global":
                guild_name = self._guild_name(row["primary_guild_id"])
                if guild_name:
                    line += f" · _{guild_name}_"
            lines.append(line)
        description = "\n".join(lines)
        if len(description) > 4000:
            description = description[:4000].rsplit("\n", 1)[0] + "\n…"
        embed = discord.Embed(
            title=f"Girldle leaderboard · {scope_label}",
            description=description,
            color=discord.Color.gold(),
        )
        footer = f"{len(rows)} players · ranked by conservative rating (rating minus 3 × RD)"
        if any_provisional:
            footer += " · ? = provisional (few games or inactive)"
        embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed)

    @girldle.command(name="stats", description="Per-player stats.")
    @app_commands.describe(user="Defaults to you.")
    async def stats(
        self, interaction: discord.Interaction, user: discord.User | None = None
    ) -> None:
        ingest.recompute_ratings(self.db)
        target = user or interaction.user
        player = self.db.conn.execute(
            """
            SELECT user_id, display_name, rating, rd, games_played, last_played
            FROM girldle_players WHERE user_id = ?
            """,
            (str(target.id),),
        ).fetchone()
        if player is None or player["games_played"] == 0:
            await interaction.response.send_message(f"No Girldle results for {target.mention}.")
            return

        agg = self.db.conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN score IS NULL THEN 1 ELSE 0 END) AS fails,
                AVG(score) AS avg_score
            FROM girldle_results WHERE user_id = ?
            """,
            (str(target.id),),
        ).fetchone()
        total = agg["total"]
        fails = agg["fails"]
        avg_score = agg["avg_score"]
        current_streak = _current_streak(self.db, str(target.id))
        best_streak = _best_streak(self.db, str(target.id))
        rank, total_ranked = _leaderboard_rank(self.db, str(target.id))

        name = player["display_name"] or target.display_name
        rank_line = f"{_rank_prefix(rank)} of {total_ranked}" if rank else "unranked"
        embed = discord.Embed(
            title="Girldle stats",
            description=f"**{name}** · {rank_line}",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Rating",
            value=f"{int(player['rating'])} (RD {int(player['rd'])})",
            inline=True,
        )
        embed.add_field(name="Games", value=str(total), inline=True)
        embed.add_field(
            name="Solve rate",
            value=f"{(total - fails) / total:.0%} ({fails} fail{'s' if fails != 1 else ''})",
            inline=True,
        )
        embed.add_field(
            name="Avg score",
            value=f"{avg_score:.2f}" if avg_score is not None else "n/a",
            inline=True,
        )
        embed.add_field(
            name="Current streak", value=_format_streak(current_streak), inline=True
        )
        embed.add_field(name="Best streak", value=_format_streak(best_streak), inline=True)
        embed.set_footer(text=f"Last played {player['last_played']}")
        if isinstance(target, discord.abc.User) and target.display_avatar:
            embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @girldle.command(name="h2h", description="Head-to-head record between two players.")
    async def h2h(
        self,
        interaction: discord.Interaction,
        user1: discord.User,
        user2: discord.User,
    ) -> None:
        if user1.id == user2.id:
            await interaction.response.send_message("Pick two different players.")
            return

        rows = list(
            self.db.conn.execute(
                """
                SELECT a.score AS s1, b.score AS s2
                FROM girldle_results a
                JOIN girldle_results b
                    ON a.puzzle_date = b.puzzle_date
                WHERE a.user_id = ? AND b.user_id = ?
                """,
                (str(user1.id), str(user2.id)),
            )
        )
        if not rows:
            await interaction.response.send_message(
                f"No shared puzzles between {user1.display_name} and {user2.display_name}."
            )
            return

        wins, losses, draws = 0, 0, 0
        for row in rows:
            s1, s2 = row["s1"], row["s2"]
            if s1 is None and s2 is None:
                draws += 1
            elif s1 is None:
                losses += 1
            elif s2 is None:
                wins += 1
            elif s1 < s2:
                wins += 1
            elif s1 > s2:
                losses += 1
            else:
                draws += 1

        if wins > losses:
            verdict = f"**{user1.display_name}** leads"
            colour = discord.Color.green()
        elif losses > wins:
            verdict = f"**{user2.display_name}** leads"
            colour = discord.Color.red()
        else:
            verdict = "Dead even"
            colour = discord.Color.greyple()

        embed = discord.Embed(
            title="Head to head",
            description=f"**{user1.display_name}** vs **{user2.display_name}**\n{verdict}",
            color=colour,
        )
        embed.add_field(name=user1.display_name, value=f"{wins} wins", inline=True)
        embed.add_field(name=user2.display_name, value=f"{losses} wins", inline=True)
        embed.add_field(name="Draws", value=str(draws), inline=True)
        embed.set_footer(text=f"{len(rows)} shared puzzles")
        await interaction.response.send_message(embed=embed)

    @girldle.command(name="styles", description="Top snipers and plodders.")
    async def styles(self, interaction: discord.Interaction) -> None:
        ascending = analysis.snipers(self.db.conn, limit=10_000)
        if not ascending:
            await interaction.response.send_message("Not enough data yet.")
            return
        n = len(ascending)
        half = n // 2
        sniper_limit = min(5, half)
        plodder_limit = min(5, n - half)
        snipers = ascending[:sniper_limit]
        plodders = list(reversed(ascending[-plodder_limit:])) if plodder_limit else []
        description = (
            "_How players' guesses overlap with the answer. "
            "Snipers triangulate from elimination; plodders build up from partial matches._\n\n"
            f"🟥 **Snipers** _(fewest greens)_\n{_format_style_lines(snipers)}\n\n"
            f"🟩 **Plodders** _(most greens)_\n{_format_style_lines(plodders)}"
        )
        embed = discord.Embed(
            title="Solve styles",
            description=description,
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)

    @girldle.command(
        name="setup",
        description="Configure the channel Girldle results are read from.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel="Channel where players post their share grids.")
    async def setup_cmd(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this in the server, not in DMs.")
            return
        guild = interaction.guild
        is_owner = await self.bot.is_owner(interaction.user)

        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT approved FROM girldle_config WHERE guild_id = ?",
                (str(guild.id),),
            ).fetchone()
            is_new = existing is None
            # Owner sets up → auto-approve. Otherwise approved stays as it was
            # (0 for new, unchanged for re-setup).
            if is_new:
                approved = 1 if is_owner else 0
                conn.execute(
                    "INSERT INTO girldle_config (guild_id, channel_id, approved) "
                    "VALUES (?, ?, ?)",
                    (str(guild.id), str(channel.id), approved),
                )
            else:
                conn.execute(
                    "UPDATE girldle_config SET channel_id = ? WHERE guild_id = ?",
                    (str(channel.id), str(guild.id)),
                )

        msg = f"Girldle channel set to {channel.mention}. Results posted there will be ingested."
        if is_new and not is_owner:
            msg += (
                "\nThis server is not yet approved for the global leaderboard. "
                "The bot operator will see this in `/girldle servers`."
            )
        await interaction.response.send_message(msg)

    @girldle.command(name="backfill", description="Read channel history and ingest results.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        channel="Channel to read (default: configured Girldle channel)",
        limit="Maximum messages to scan (default: all)",
        dry_run="Report counts without writing",
    )
    async def backfill(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        limit: int | None = None,
        dry_run: bool = False,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this in the server, not in DMs.")
            return

        target = channel
        if target is None:
            configured = _girldle_channel_for(self.db, interaction.guild.id)
            if configured is None:
                await interaction.response.send_message(
                    "No Girldle channel configured. Run `/girldle setup` first or pass `channel:`."
                )
                return
            target = interaction.guild.get_channel(configured)
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("Could not resolve target channel.")
            return

        await interaction.response.defer(thinking=True)

        parsed: list = []
        scanned = 0
        async for msg in target.history(limit=limit, oldest_first=True):
            scanned += 1
            if msg.author.bot:
                continue
            result = parse(msg.content)
            if result is not None:
                parsed.append((msg, result))

        if dry_run:
            await interaction.followup.send(
                f"Dry run: scanned {scanned}, "
                f"would ingest {len(parsed)} results from #{target.name}."
            )
            return

        stored = ingest.ingest_messages(self.db, parsed)
        ingest.recompute_ratings(self.db)
        await interaction.followup.send(
            f"Scanned {scanned}, ingested {stored} results from #{target.name}."
        )

    @girldle.command(name="reset", description="Wipe this server's Girldle posts.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(confirm=f"Type '{RESET_CONFIRM_PHRASE}' to confirm.")
    async def reset(self, interaction: discord.Interaction, confirm: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this in the server, not in DMs.")
            return
        if confirm != RESET_CONFIRM_PHRASE:
            await interaction.response.send_message(
                f"Pass `confirm:{RESET_CONFIRM_PHRASE}` to wipe."
            )
            return
        with self.db.transaction() as conn:
            post_cursor = conn.execute(
                "DELETE FROM girldle_posts WHERE guild_id = ?",
                (str(interaction.guild.id),),
            )
            deleted_posts = post_cursor.rowcount
            # Drop canonical results that no longer have any sighting anywhere.
            result_cursor = conn.execute(
                """
                DELETE FROM girldle_results
                WHERE (user_id, puzzle_date) NOT IN (
                    SELECT user_id, puzzle_date FROM girldle_posts
                )
                """
            )
            deleted_results = result_cursor.rowcount
        ingest.recompute_ratings(self.db)
        await interaction.response.send_message(
            f"Wiped {deleted_posts} post{'s' if deleted_posts != 1 else ''} for this server "
            f"({deleted_results} canonical result{'s' if deleted_results != 1 else ''} dropped, "
            "rest still seen elsewhere). Ratings recomputed."
        )

    @girldle.command(
        name="privacy",
        description="Hide this server's results from the global leaderboard.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        private="true = hide from global leaderboard, false = show (default for new servers)",
    )
    async def privacy(self, interaction: discord.Interaction, private: bool) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this in the server, not in DMs.")
            return
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT channel_id FROM girldle_config WHERE guild_id = ?",
                (str(interaction.guild.id),),
            ).fetchone()
            if row is None:
                await interaction.response.send_message(
                    "Run `/girldle setup` first to configure a channel."
                )
                return
            conn.execute(
                "UPDATE girldle_config SET private = ? WHERE guild_id = ?",
                (1 if private else 0, str(interaction.guild.id)),
            )
        state = "hidden from" if private else "visible on"
        await interaction.response.send_message(
            f"This server's results are now {state} the global leaderboard."
        )

    @girldle.command(
        name="approve",
        description="(Owner) Approve a server for the global leaderboard, or revoke approval.",
    )
    @app_commands.default_permissions(administrator=True)
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

    @girldle.command(
        name="servers",
        description="(Owner) List all servers known to the bot and their state.",
    )
    @app_commands.default_permissions(administrator=True)
    async def servers_cmd(self, interaction: discord.Interaction) -> None:
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        rows = list(
            self.db.conn.execute(
                """
                SELECT c.guild_id, c.channel_id, c.approved, c.private,
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
            name = self._guild_name(row["guild_id"]) or "_(bot not in guild)_"
            if row["approved"] and not row["private"]:
                marker = "✅"
            elif row["private"]:
                marker = "🔒"
            else:
                marker = "⏳"
                # Track for Approve buttons. Fall back to ID if name unresolved.
                label = self._guild_name(row["guild_id"]) or row["guild_id"]
                pending.append((int(row["guild_id"]), label))
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

    @girldle.command(
        name="remove",
        description="(Owner) Remove a server's config and wipe its posts.",
    )
    @app_commands.default_permissions(administrator=True)
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


def _truncate(s: str, width: int) -> str:
    return s if len(s) <= width else s[: width - 1] + "…"


def _rank_prefix(rank: int) -> str:
    if rank == 1:
        return "\U0001f947"  # 🥇
    if rank == 2:
        return "\U0001f948"  # 🥈
    if rank == 3:
        return "\U0001f949"  # 🥉
    return f"`#{rank}`"


def _format_streak(days: int) -> str:
    return f"{days} day{'s' if days != 1 else ''}"


def _leaderboard_rank(db: Database, user_id: str) -> tuple[int | None, int]:
    """Return (rank, total_ranked_players). rank is None if user isn't in the ranking."""
    total = db.conn.execute(
        "SELECT COUNT(*) AS n FROM girldle_players WHERE games_played >= 1"
    ).fetchone()["n"]
    row = db.conn.execute(
        """
        SELECT (
            SELECT COUNT(*) FROM girldle_players p2
            WHERE p2.games_played >= 1
              AND (p2.rating - 3 * p2.rd) > (p1.rating - 3 * p1.rd)
        ) + 1 AS rank
        FROM girldle_players p1
        WHERE p1.user_id = ? AND p1.games_played >= 1
        """,
        (user_id,),
    ).fetchone()
    return (row["rank"] if row else None, total)


def _best_streak(db: Database, user_id: str) -> int:
    rows = list(
        db.conn.execute(
            "SELECT puzzle_date FROM girldle_results WHERE user_id = ? ORDER BY puzzle_date ASC",
            (user_id,),
        )
    )
    if not rows:
        return 0
    dates = [date.fromisoformat(r["puzzle_date"]) for r in rows]
    best = 1
    current = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]) == timedelta(days=1):
            current += 1
            best = max(best, current)
        else:
            current = 1
    return best


def _format_last_played(iso: str | None, today: date) -> str:
    if not iso:
        return "n/a"
    d = date.fromisoformat(iso)
    delta = (today - d).days
    if delta <= 0:
        return "today"
    if delta == 1:
        return "yesterday"
    return f"{delta}d ago"


def _format_style_lines(rows: list) -> str:
    if not rows:
        return "_Not enough data yet._"
    lines: list[str] = []
    for i, r in enumerate(rows, start=1):
        rank = _rank_prefix(i)
        name = r.display_name or r.user_id
        lines.append(
            f"{rank} **{name}** · {int(round(r.score * 100))}% green · "
            f"{r.solves} solve{'s' if r.solves != 1 else ''}"
        )
    return "\n".join(lines)


def _current_streak(db: Database, user_id: str) -> int:
    rows = list(
        db.conn.execute(
            "SELECT puzzle_date FROM girldle_results WHERE user_id = ? ORDER BY puzzle_date DESC",
            (user_id,),
        )
    )
    if not rows:
        return 0
    dates = [date.fromisoformat(r["puzzle_date"]) for r in rows]
    streak = 1
    for i in range(1, len(dates)):
        if (dates[i - 1] - dates[i]) == timedelta(days=1):
            streak += 1
        else:
            break
    return streak
