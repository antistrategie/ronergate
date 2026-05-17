"""Girldle command group + passive message handler."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from ... import errors
from ...config import Config
from ...db import Database
from ...permissions import is_admin
from . import analysis, ingest
from .parser import parse

LEADERBOARD_LIMIT = 100
NAME_DISPLAY_WIDTH = 18
PROVISIONAL_RD = 150

log = logging.getLogger(__name__)

RESET_CONFIRM_PHRASE = "wipe-girldle"


class GirldleCog(commands.Cog):
    girldle = app_commands.Group(
        name="girldle",
        description="Girldle stats and admin commands.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config: Config = bot.config  # type: ignore[attr-defined]
        self.db: Database = bot.db  # type: ignore[attr-defined]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.channel.id != self.config.girldle_channel_id:
            return
        try:
            await ingest.handle_message(self.db, message)
        except Exception as e:
            await errors.report(self.bot, f"ingest failed for {message.jump_url}", e)

    @commands.Cog.listener()
    async def on_message_edit(
        self, before: discord.Message, after: discord.Message
    ) -> None:
        if after.author.bot:
            return
        if after.channel.id != self.config.girldle_channel_id:
            return
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    "DELETE FROM girldle_results WHERE message_id = ?", (str(after.id),)
                )
            await ingest.handle_message(self.db, after)
        except Exception as e:
            await errors.report(self.bot, f"edit re-ingest failed for {after.jump_url}", e)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.channel.id != self.config.girldle_channel_id:
            return
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    "DELETE FROM girldle_results WHERE message_id = ?", (str(message.id),)
                )
        except Exception as e:
            await errors.report(self.bot, f"delete cleanup failed for {message.id}", e)

    @girldle.command(name="leaderboard", description="All-time Glicko-2 ratings.")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        ingest.recompute_ratings(self.db)
        rows = list(
            self.db.conn.execute(
                """
                SELECT user_id, display_name, rating, rd, games_played, last_played
                FROM girldle_players
                WHERE games_played >= 1
                ORDER BY (rating - 3 * rd) DESC
                LIMIT ?
                """,
                (LEADERBOARD_LIMIT,),
            )
        )
        if not rows:
            await interaction.response.send_message("No Girldle results yet.")
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
            lines.append(
                f"{rank} **{name}** · {rating_label} · {games_label} · {last}"
            )
        description = "\n".join(lines)
        if len(description) > 4000:
            description = description[:4000].rsplit("\n", 1)[0] + "\n…"
        embed = discord.Embed(
            title="Girldle leaderboard",
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

    @girldle.command(name="backfill", description="Read channel history and ingest results.")
    @app_commands.default_permissions(administrator=True)
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
        if not is_admin(interaction.user, self.config):
            await interaction.response.send_message("Admins only.")
            return

        target = channel or interaction.guild.get_channel(self.config.girldle_channel_id)
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

    @girldle.command(name="reset", description="Wipe all Girldle data.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(confirm=f"Type '{RESET_CONFIRM_PHRASE}' to confirm.")
    async def reset(self, interaction: discord.Interaction, confirm: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this in the server, not in DMs.")
            return
        if not is_admin(interaction.user, self.config):
            await interaction.response.send_message("Admins only.")
            return
        if confirm != RESET_CONFIRM_PHRASE:
            await interaction.response.send_message(
                f"Pass `confirm:{RESET_CONFIRM_PHRASE}` to wipe."
            )
            return
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM girldle_results")
            conn.execute("DELETE FROM girldle_players")
        await interaction.response.send_message("Wiped all Girldle data.")


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
