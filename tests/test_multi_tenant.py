"""Tests for the multi-server schema and guild-scoped queries.

Exercises the SQL paths directly against a real on-disk sqlite file
(via the Database class) without mocking discord.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ronergate.db import Database

ROOT = Path(__file__).parent.parent
COGS_DIR = ROOT / "ronergate" / "cogs"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(str(tmp_path / "test.sqlite"))
    d.bootstrap(COGS_DIR)
    return d


def _seed(
    db: Database,
    *,
    message_id: str,
    guild_id: str,
    user_id: str,
    puzzle_date: str,
    score: int | None = 3,
) -> None:
    """Mimic ingest.store_result without needing a discord.Message."""
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO girldle_results
                (message_id, user_id, puzzle_date, posted_at, score, grid)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, user_id, puzzle_date, f"{puzzle_date}T12:00:00", score, "grid"),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO girldle_posts
                (message_id, guild_id, user_id, puzzle_date, posted_at, score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, guild_id, user_id, puzzle_date, f"{puzzle_date}T12:00:00", score),
        )
        conn.execute(
            """
            INSERT INTO girldle_players (user_id, display_name, games_played, last_played)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                games_played = girldle_players.games_played + 1,
                last_played = excluded.last_played
            """,
            (user_id, f"user-{user_id}", puzzle_date),
        )


def test_schema_has_all_expected_tables(db: Database):
    tables = {
        row["name"]
        for row in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"girldle_results", "girldle_players", "girldle_config", "girldle_posts"} <= tables


def test_girldle_config_has_private_column_default_zero(db: Database):
    db.conn.execute(
        "INSERT INTO girldle_config (guild_id, channel_id) VALUES (?, ?)", ("g1", "c1")
    )
    row = db.conn.execute(
        "SELECT private FROM girldle_config WHERE guild_id = ?", ("g1",)
    ).fetchone()
    assert row["private"] == 0


def test_server_scope_filters_via_posts_table(db: Database):
    # u1 only in A, u2 only in B, u3 in both (two separate posts of same puzzle)
    _seed(db, message_id="m1", guild_id="A", user_id="u1", puzzle_date="2026-05-10")
    _seed(db, message_id="m2", guild_id="B", user_id="u2", puzzle_date="2026-05-10")
    _seed(db, message_id="m3a", guild_id="A", user_id="u3", puzzle_date="2026-05-10")
    _seed(db, message_id="m3b", guild_id="B", user_id="u3", puzzle_date="2026-05-10")

    def members_of(guild_id: str) -> set[str]:
        return {
            row["user_id"]
            for row in db.conn.execute(
                """
                SELECT user_id FROM girldle_players
                WHERE games_played >= 1
                  AND user_id IN (SELECT user_id FROM girldle_posts WHERE guild_id = ?)
                """,
                (guild_id,),
            )
        }

    # Crucially: u3 shows up in BOTH leaderboards because they posted in both
    assert members_of("A") == {"u1", "u3"}
    assert members_of("B") == {"u2", "u3"}


def test_canonical_dedup_keeps_one_result_per_user_puzzle(db: Database):
    """Same user, same puzzle, two guilds → one canonical row, two post rows."""
    _seed(db, message_id="m1", guild_id="A", user_id="u1", puzzle_date="2026-05-10", score=4)
    _seed(db, message_id="m2", guild_id="B", user_id="u1", puzzle_date="2026-05-10", score=4)

    results = list(
        db.conn.execute(
            "SELECT message_id FROM girldle_results "
            "WHERE user_id = 'u1' AND puzzle_date = '2026-05-10'"
        )
    )
    posts = list(
        db.conn.execute(
            "SELECT guild_id FROM girldle_posts WHERE user_id = 'u1' AND puzzle_date = '2026-05-10'"
        )
    )
    assert len(results) == 1
    assert {p["guild_id"] for p in posts} == {"A", "B"}


def test_reset_drops_posts_then_orphan_results(db: Database):
    """Reset guild A drops A's posts, then any canonical result with no remaining posts."""
    # u1 only in A → posts dropped, result orphaned, both gone
    _seed(db, message_id="m1", guild_id="A", user_id="u1", puzzle_date="2026-05-10")
    # u2 only in B → untouched
    _seed(db, message_id="m2", guild_id="B", user_id="u2", puzzle_date="2026-05-10")
    # u3 in both → A post deleted, but B post survives, so canonical result also survives
    _seed(db, message_id="m3a", guild_id="A", user_id="u3", puzzle_date="2026-05-11")
    _seed(db, message_id="m3b", guild_id="B", user_id="u3", puzzle_date="2026-05-11")

    with db.transaction() as conn:
        conn.execute("DELETE FROM girldle_posts WHERE guild_id = ?", ("A",))
        conn.execute(
            """
            DELETE FROM girldle_results
            WHERE (user_id, puzzle_date) NOT IN (
                SELECT user_id, puzzle_date FROM girldle_posts
            )
            """
        )

    remaining_posts = {
        (row["guild_id"], row["user_id"]) for row in db.conn.execute(
            "SELECT guild_id, user_id FROM girldle_posts"
        )
    }
    assert remaining_posts == {("B", "u2"), ("B", "u3")}

    remaining_results = {
        row["user_id"] for row in db.conn.execute("SELECT user_id FROM girldle_results")
    }
    assert remaining_results == {"u2", "u3"}  # u1 dropped, u3 survives via B


def _public_global_query() -> str:
    """The SELECT clause the cog uses for scope=global (approved AND not private)."""
    return """
        SELECT p.user_id FROM girldle_players p
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


def test_global_excludes_private_guilds(db: Database):
    _seed(db, message_id="m1", guild_id="A", user_id="u2", puzzle_date="2026-05-10")
    _seed(db, message_id="m2", guild_id="B", user_id="u1", puzzle_date="2026-05-10")
    db.conn.execute(
        "INSERT INTO girldle_config (guild_id, channel_id, approved, private) "
        "VALUES (?, ?, 1, 1)",
        ("B", "ch-b"),
    )
    db.conn.execute(
        "INSERT INTO girldle_config (guild_id, channel_id, approved) VALUES (?, ?, 1)",
        ("A", "ch-a"),
    )

    visible = {row["user_id"] for row in db.conn.execute(_public_global_query())}
    assert visible == {"u2"}


def test_global_excludes_unapproved_guilds(db: Database):
    # Both public, but only A is approved
    _seed(db, message_id="m1", guild_id="A", user_id="u_app", puzzle_date="2026-05-10")
    _seed(db, message_id="m2", guild_id="B", user_id="u_pending", puzzle_date="2026-05-10")
    db.conn.execute(
        "INSERT INTO girldle_config (guild_id, channel_id, approved) VALUES (?, ?, 1)",
        ("A", "ch-a"),
    )
    db.conn.execute(
        "INSERT INTO girldle_config (guild_id, channel_id, approved) VALUES (?, ?, 0)",
        ("B", "ch-b"),
    )

    visible = {row["user_id"] for row in db.conn.execute(_public_global_query())}
    assert visible == {"u_app"}


def test_approved_default_is_zero(db: Database):
    db.conn.execute(
        "INSERT INTO girldle_config (guild_id, channel_id) VALUES (?, ?)", ("X", "c")
    )
    row = db.conn.execute(
        "SELECT approved FROM girldle_config WHERE guild_id = ?", ("X",)
    ).fetchone()
    assert row["approved"] == 0


def test_primary_guild_is_most_posted_in(db: Database):
    """For the global leaderboard, primary_guild_id is the public guild with the most posts."""
    # u1 posts 3 times in A, 1 time in B; both public
    _seed(db, message_id="ma1", guild_id="A", user_id="u1", puzzle_date="2026-05-10")
    _seed(db, message_id="ma2", guild_id="A", user_id="u1", puzzle_date="2026-05-11")
    _seed(db, message_id="ma3", guild_id="A", user_id="u1", puzzle_date="2026-05-12")
    _seed(db, message_id="mb1", guild_id="B", user_id="u1", puzzle_date="2026-05-13")

    row = db.conn.execute(
        """
        SELECT (
            SELECT guild_id FROM girldle_posts
            WHERE user_id = p.user_id
              AND guild_id NOT IN (SELECT guild_id FROM girldle_config WHERE private = 1)
            GROUP BY guild_id
            ORDER BY COUNT(*) DESC
            LIMIT 1
        ) AS primary_guild_id
        FROM girldle_players p
        WHERE p.user_id = 'u1'
        """
    ).fetchone()
    assert row["primary_guild_id"] == "A"


def test_girldle_config_upsert_replaces_channel(db: Database):
    db.conn.execute(
        """
        INSERT INTO girldle_config (guild_id, channel_id) VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id
        """,
        ("42", "100"),
    )
    db.conn.execute(
        """
        INSERT INTO girldle_config (guild_id, channel_id) VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id
        """,
        ("42", "200"),
    )
    rows = list(
        db.conn.execute("SELECT channel_id FROM girldle_config WHERE guild_id = ?", ("42",))
    )
    assert len(rows) == 1
    assert rows[0]["channel_id"] == "200"
