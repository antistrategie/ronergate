"""Girldle command group + passive message handler."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from ... import errors
from ...config import Config
from ...db import Database
from . import analysis, ingest
from .parser import GirldleResult, parse

LEADERBOARD_LIMIT = 25
NAME_DISPLAY_WIDTH = 18
PROVISIONAL_RD = 150

log = logging.getLogger(__name__)

RESET_CONFIRM_PHRASE = "wipe-girldle"


def _girldle_config_for(db: Database, guild_id: int) -> dict | None:
    row = db.conn.execute(
        "SELECT channel_id, name FROM girldle_config WHERE guild_id = ?",
        (str(guild_id),),
    ).fetchone()
    return dict(row) if row else None


def _guild_name_from_db(db: Database, guild_id: str | None) -> str | None:
    if not guild_id:
        return None
    row = db.conn.execute(
        "SELECT name FROM girldle_config WHERE guild_id = ?", (guild_id,)
    ).fetchone()
    return row["name"] if row and row["name"] else None


class GirldleCog(commands.Cog):
    girldle = app_commands.Group(
        name="girldle",
        description="Girldle stats and admin commands.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config: Config = bot.config  # type: ignore[attr-defined]
        self.db: Database = bot.db  # type: ignore[attr-defined]

    async def _require_setup(self, interaction: discord.Interaction) -> bool:
        """Returns True if the guild has been set up. Otherwise replies and returns False."""
        if interaction.guild is None:
            await interaction.response.send_message("Run this in a server, not in DMs.")
            return False
        if _girldle_config_for(self.db, interaction.guild.id) is None:
            await interaction.response.send_message(
                "This server isn't set up yet. An admin needs to run `/girldle setup` first."
            )
            return False
        return True

    def _is_girldle_channel(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False
        config = _girldle_config_for(self.db, message.guild.id)
        return config is not None and message.channel.id == int(config["channel_id"])

    @commands.Cog.listener()
    async def on_guild_update(
        self, before: discord.Guild, after: discord.Guild
    ) -> None:
        if before.name == after.name:
            return
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE girldle_config SET name = ? WHERE guild_id = ?",
                (after.name, str(after.id)),
            )

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
        if not await self._require_setup(interaction):
            return
        ingest.recompute_ratings(self.db)

        if scope == "server":
            if interaction.guild is None:
                await interaction.response.send_message("Server scope only works in a server.")
                return
            guild_id = str(interaction.guild.id)
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
                    (guild_id, LEADERBOARD_LIMIT),
                )
            )
            total = self.db.conn.execute(
                """
                SELECT COUNT(*) AS n FROM girldle_players p
                WHERE p.games_played >= 1
                  AND p.user_id IN (
                      SELECT user_id FROM girldle_posts WHERE guild_id = ?
                  )
                """,
                (guild_id,),
            ).fetchone()["n"]
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
            total = self.db.conn.execute(
                """
                SELECT COUNT(*) AS n FROM girldle_players p
                WHERE p.games_played >= 1
                  AND EXISTS (
                      SELECT 1 FROM girldle_posts po
                      WHERE po.user_id = p.user_id
                        AND po.guild_id IN (
                            SELECT guild_id FROM girldle_config
                            WHERE approved = 1 AND private = 0
                        )
                  )
                """
            ).fetchone()["n"]
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
            rating_label = f"**{int(row['rating'])}**{'?' if provisional else ''}"
            guild_suffix = ""
            if scope == "global":
                guild_name = _guild_name_from_db(self.db, row["primary_guild_id"])
                if guild_name:
                    guild_suffix = f" _({guild_name})_"
            line = (
                f"{rank} **{name}**{guild_suffix} · "
                f"{rating_label} · {games_label} · {last}"
            )
            lines.append(line)
        description = "\n".join(lines)
        if total > LEADERBOARD_LIMIT:
            description += (
                f"\n\nNot in the top {LEADERBOARD_LIMIT}? "
                "Run `/girldle stats` to see your rank."
            )
        embed = discord.Embed(
            title=f"Girldle leaderboard · top {LEADERBOARD_LIMIT} · {scope_label}",
            description=description,
            color=discord.Color.gold(),
        )
        footer = f"{total} players · ranked by conservative rating (rating minus 3 × RD)"
        if any_provisional:
            footer += " · ? = provisional (few games or inactive)"
        embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed)

    @girldle.command(name="stats", description="Per-player stats.")
    @app_commands.describe(user="Defaults to you.")
    async def stats(
        self, interaction: discord.Interaction, user: discord.User | None = None
    ) -> None:
        if not await self._require_setup(interaction):
            return
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
        rank_global, total_global = _leaderboard_rank(self.db, str(target.id))
        assert interaction.guild is not None
        rank_server, total_server = _leaderboard_rank(
            self.db, str(target.id), guild_id=str(interaction.guild.id)
        )

        name = player["display_name"] or target.display_name
        rank_parts: list[str] = []
        if rank_global:
            rank_parts.append(f"{_rank_prefix(rank_global)} of {total_global} global")
        if rank_server:
            rank_parts.append(f"{_rank_prefix(rank_server)} of {total_server} here")
        rank_line = " · ".join(rank_parts) if rank_parts else "unranked"
        embed = discord.Embed(
            title="Girldle stats",
            description=f"**{name}**\n{rank_line}",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Rating",
            value=f"{int(player['rating'])} (RD {int(player['rd'])})",
            inline=True,
        )
        ranking_score = int(player["rating"] - 3 * player["rd"])
        embed.add_field(
            name="Ranking score",
            value=str(ranking_score),
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
        green_density = analysis.player_green_density(self.db.conn, str(target.id))
        if green_density is not None:
            embed.add_field(
                name="Style",
                value=f"{int(round(green_density * 100))}% green",
                inline=True,
            )
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
        if not await self._require_setup(interaction):
            return
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

    @girldle.command(name="styles", description="Top snipers and plodders in this server.")
    async def styles(self, interaction: discord.Interaction) -> None:
        if not await self._require_setup(interaction):
            return
        assert interaction.guild is not None
        ascending = analysis.snipers(
            self.db.conn, guild_id=str(interaction.guild.id), limit=10_000
        )
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
                    "INSERT INTO girldle_config (guild_id, channel_id, approved, name) "
                    "VALUES (?, ?, ?, ?)",
                    (str(guild.id), str(channel.id), approved, guild.name),
                )
            else:
                conn.execute(
                    "UPDATE girldle_config SET channel_id = ?, name = ? "
                    "WHERE guild_id = ?",
                    (str(channel.id), guild.name, str(guild.id)),
                )

        if not is_new:
            await interaction.response.send_message(
                f"Girldle channel updated to {channel.mention}."
            )
            return

        await interaction.response.defer(thinking=True)
        scanned, parsed = await _scan_channel_for_results(channel)
        stored = ingest.ingest_messages(self.db, parsed)
        ingest.recompute_ratings(self.db)

        msg = (
            f"Girldle channel set to {channel.mention}. "
            f"Scanned {scanned} messages, ingested {stored} historical "
            f"result{'s' if stored != 1 else ''}."
        )
        if not is_owner:
            msg += (
                "\nThis server isn't yet approved for the global leaderboard. "
                "Ask the bot operator (https://discord.gg/XcfYGmxvde) for approval."
            )
        await interaction.followup.send(msg)

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
        if not await self._require_setup(interaction):
            return
        assert interaction.guild is not None

        target = channel
        if target is None:
            config = _girldle_config_for(self.db, interaction.guild.id)
            assert config is not None  # _require_setup ensures this
            target = interaction.guild.get_channel(int(config["channel_id"]))
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("Could not resolve target channel.")
            return

        await interaction.response.defer(thinking=True)
        scanned, parsed = await _scan_channel_for_results(target, limit=limit)

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
        if not await self._require_setup(interaction):
            return
        assert interaction.guild is not None
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
        if not await self._require_setup(interaction):
            return
        assert interaction.guild is not None
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE girldle_config SET private = ? WHERE guild_id = ?",
                (1 if private else 0, str(interaction.guild.id)),
            )
        state = "hidden from" if private else "visible on"
        await interaction.response.send_message(
            f"This server's results are now {state} the global leaderboard."
        )


async def _scan_channel_for_results(
    channel: discord.TextChannel, limit: int | None = None
) -> tuple[int, list[tuple[discord.Message, GirldleResult]]]:
    """Walk a channel's history, return (scanned_count, parsed_results)."""
    parsed: list[tuple[discord.Message, GirldleResult]] = []
    scanned = 0
    async for msg in channel.history(limit=limit, oldest_first=True):
        scanned += 1
        if msg.author.bot:
            continue
        result = parse(msg.content)
        if result is not None:
            parsed.append((msg, result))
    return scanned, parsed


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


def _leaderboard_rank(
    db: Database, user_id: str, *, guild_id: str | None = None
) -> tuple[int | None, int]:
    """Return (rank, total_ranked_players).

    rank is None if the user isn't in the ranking. If guild_id is given, the
    ranking is scoped to players who've posted in that guild; otherwise global.
    """
    if guild_id is None:
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

    total = db.conn.execute(
        """
        SELECT COUNT(*) AS n FROM girldle_players p
        WHERE p.games_played >= 1
          AND p.user_id IN (SELECT user_id FROM girldle_posts WHERE guild_id = ?)
        """,
        (guild_id,),
    ).fetchone()["n"]
    row = db.conn.execute(
        """
        SELECT (
            SELECT COUNT(*) FROM girldle_players p2
            WHERE p2.games_played >= 1
              AND p2.user_id IN (SELECT user_id FROM girldle_posts WHERE guild_id = ?)
              AND (p2.rating - 3 * p2.rd) > (p1.rating - 3 * p1.rd)
        ) + 1 AS rank
        FROM girldle_players p1
        WHERE p1.user_id = ? AND p1.games_played >= 1
          AND p1.user_id IN (SELECT user_id FROM girldle_posts WHERE guild_id = ?)
        """,
        (guild_id, user_id, guild_id),
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
