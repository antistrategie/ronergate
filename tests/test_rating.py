from datetime import date

from ronergate.cogs.girldle.rating import (
    DEFAULT_RD,
    GameResult,
    Match,
    Rating,
    recompute,
    update,
)


def test_glickman_paper_example():
    """Worked example from http://www.glicko.net/glicko/glicko2.pdf section 7."""
    player = Rating(rating=1500, rd=200, volatility=0.06)
    matches = [
        Match(opponent_rating=1400, opponent_rd=30, score=1.0),
        Match(opponent_rating=1550, opponent_rd=100, score=0.0),
        Match(opponent_rating=1700, opponent_rd=300, score=0.0),
    ]
    result = update(player, matches)
    assert abs(result.rating - 1464.06) < 0.05
    assert abs(result.rd - 151.52) < 0.1
    assert abs(result.volatility - 0.05999) < 1e-4


def test_no_matches_inflates_rd_only():
    player = Rating(rating=1700, rd=80, volatility=0.06)
    result = update(player, [])
    assert result.rating == player.rating
    assert result.rd > player.rd
    assert result.volatility == player.volatility


def test_no_matches_rd_capped_at_default():
    player = Rating(rating=1500, rd=DEFAULT_RD, volatility=0.5)
    result = update(player, [])
    assert result.rd <= DEFAULT_RD


def test_recompute_winner_outranks_loser():
    results = [
        GameResult(user_id="alice", puzzle_date=date(2026, 5, 1), score=3),
        GameResult(user_id="bob", puzzle_date=date(2026, 5, 1), score=6),
        GameResult(user_id="alice", puzzle_date=date(2026, 5, 2), score=4),
        GameResult(user_id="bob", puzzle_date=date(2026, 5, 2), score=5),
    ]
    ratings = recompute(results)
    assert ratings["alice"].rating > ratings["bob"].rating


def test_recompute_x8_loses_to_solver():
    results = [
        GameResult(user_id="alice", puzzle_date=date(2026, 5, 1), score=4),
        GameResult(user_id="bob", puzzle_date=date(2026, 5, 1), score=None),
    ]
    ratings = recompute(results)
    assert ratings["alice"].rating > ratings["bob"].rating


def test_inactive_player_rd_grows_over_time():
    results = [
        GameResult(user_id="alice", puzzle_date=date(2026, 5, 1), score=3),
        GameResult(user_id="bob", puzzle_date=date(2026, 5, 1), score=5),
    ]
    after_one = recompute(results)
    alice_rd_after_one = after_one["alice"].rd

    # Bob keeps playing without Alice
    for d in range(2, 30):
        results.append(GameResult(user_id="bob", puzzle_date=date(2026, 5, d), score=4))
        results.append(GameResult(user_id="carol", puzzle_date=date(2026, 5, d), score=5))

    after_more = recompute(results)
    # Alice's RD should grow due to inactivity
    assert after_more["alice"].rd >= alice_rd_after_one
