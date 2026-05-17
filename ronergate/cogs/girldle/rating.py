"""Glicko-2 rating system.

Reference: http://www.glicko.net/glicko/glicko2.pdf

For Girldle: one rating period = one puzzle date. Within a period, players form an
implicit round-robin where the lower score wins (X/8 loses to every solver, equal
scores draw). Ratings are recomputed from raw results on demand.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

TAU = 0.5
DEFAULT_RATING = 1500.0
DEFAULT_RD = 350.0
DEFAULT_VOLATILITY = 0.06
SCALE = 173.7178
CONVERGENCE_EPSILON = 1e-6


@dataclass(frozen=True)
class Rating:
    rating: float = DEFAULT_RATING
    rd: float = DEFAULT_RD
    volatility: float = DEFAULT_VOLATILITY

    @property
    def conservative(self) -> float:
        """Display rank by this: rating minus 2 standard deviations of uncertainty."""
        return self.rating - 2.0 * self.rd


@dataclass(frozen=True)
class Match:
    opponent_rating: float
    opponent_rd: float
    score: float  # 1.0 win, 0.5 draw, 0.0 loss (for the player being updated)


def update(player: Rating, matches: list[Match]) -> Rating:
    if not matches:
        phi = player.rd / SCALE
        new_phi = math.sqrt(phi**2 + player.volatility**2)
        return Rating(
            rating=player.rating,
            rd=min(new_phi * SCALE, DEFAULT_RD),
            volatility=player.volatility,
        )

    mu = (player.rating - 1500.0) / SCALE
    phi = player.rd / SCALE

    opponents = [
        ((m.opponent_rating - 1500.0) / SCALE, m.opponent_rd / SCALE, m.score) for m in matches
    ]

    def g(phi_j: float) -> float:
        return 1.0 / math.sqrt(1.0 + 3.0 * phi_j**2 / math.pi**2)

    def E(mu_j: float, phi_j: float) -> float:
        return 1.0 / (1.0 + math.exp(-g(phi_j) * (mu - mu_j)))

    v_inv = sum(g(p) ** 2 * E(m, p) * (1.0 - E(m, p)) for m, p, _ in opponents)
    v = 1.0 / v_inv

    score_residual = sum(g(p) * (s - E(m, p)) for m, p, s in opponents)
    delta = v * score_residual

    new_volatility = _update_volatility(player.volatility, delta, phi, v)

    phi_star = math.sqrt(phi**2 + new_volatility**2)
    new_phi = 1.0 / math.sqrt(1.0 / phi_star**2 + 1.0 / v)
    new_mu = mu + new_phi**2 * score_residual

    return Rating(
        rating=new_mu * SCALE + 1500.0,
        rd=min(new_phi * SCALE, DEFAULT_RD),
        volatility=new_volatility,
    )


def _update_volatility(sigma: float, delta: float, phi: float, v: float) -> float:
    a = math.log(sigma**2)

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta**2 - phi**2 - v - ex)
        den = 2.0 * (phi**2 + v + ex) ** 2
        return num / den - (x - a) / TAU**2

    A = a
    if delta**2 > phi**2 + v:
        B = math.log(delta**2 - phi**2 - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        B = a - k * TAU

    fA, fB = f(A), f(B)
    while abs(B - A) > CONVERGENCE_EPSILON:
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA /= 2.0
        B, fB = C, fC

    return math.exp(A / 2.0)


@dataclass(frozen=True)
class GameResult:
    user_id: str
    puzzle_date: date
    score: int | None  # None = X/8


def _score_for(a: GameResult, b: GameResult) -> float:
    """Score for player a vs player b. Lower puzzle score wins. X/8 loses to solvers."""
    if a.score is None and b.score is None:
        return 0.5
    if a.score is None:
        return 0.0
    if b.score is None:
        return 1.0
    if a.score < b.score:
        return 1.0
    if a.score > b.score:
        return 0.0
    return 0.5


def recompute(results: list[GameResult]) -> dict[str, Rating]:
    """Replay every result in chronological order; return final ratings per user."""
    by_date: dict[date, list[GameResult]] = defaultdict(list)
    for r in results:
        by_date[r.puzzle_date].append(r)

    ratings: dict[str, Rating] = {}
    for puzzle_date in sorted(by_date.keys()):
        period_results = by_date[puzzle_date]
        ratings = _apply_period(ratings, period_results)
    return ratings


def _apply_period(
    pre: dict[str, Rating], period_results: list[GameResult]
) -> dict[str, Rating]:
    today_ids = {r.user_id for r in period_results}

    matches_for: dict[str, list[Match]] = defaultdict(list)
    for i, a in enumerate(period_results):
        for b in period_results[i + 1 :]:
            score = _score_for(a, b)
            rating_a = pre.get(a.user_id, Rating())
            rating_b = pre.get(b.user_id, Rating())
            matches_for[a.user_id].append(
                Match(opponent_rating=rating_b.rating, opponent_rd=rating_b.rd, score=score)
            )
            matches_for[b.user_id].append(
                Match(opponent_rating=rating_a.rating, opponent_rd=rating_a.rd, score=1.0 - score)
            )

    post = dict(pre)
    for user_id in today_ids:
        post[user_id] = update(pre.get(user_id, Rating()), matches_for[user_id])
    for user_id, rating in pre.items():
        if user_id not in today_ids:
            post[user_id] = update(rating, [])
    return post
