"""Parse Discord messages containing Girldle results."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

RED = "\U0001f7e5"
GREEN = "\U0001f7e9"
GRID_CHARS = {RED, GREEN}

_HEADER_RE = re.compile(
    r"^Girldle\s+(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<score>\d{1,2}|X)/8\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GirldleResult:
    puzzle_date: date
    score: int | None
    grid: str

    @property
    def solved(self) -> bool:
        return self.score is not None


def parse(content: str) -> GirldleResult | None:
    """Return a parsed result, or None if the message isn't a Girldle post."""
    lines = content.splitlines()
    for i, line in enumerate(lines):
        m = _HEADER_RE.match(line.strip())
        if m:
            return _parse_after_header(m, lines[i + 1 :])
    return None


def _parse_after_header(header_match: re.Match[str], rest: list[str]) -> GirldleResult | None:
    raw_score = header_match["score"].upper()
    score: int | None = None if raw_score == "X" else int(raw_score)

    grid_rows: list[str] = []
    for line in rest:
        stripped = line.strip()
        if not stripped:
            continue
        if all(ch in GRID_CHARS for ch in stripped):
            grid_rows.append(stripped)
        else:
            break

    if not grid_rows:
        return None

    width = len(grid_rows[0])
    if any(len(r) != width for r in grid_rows):
        return None

    expected_rows = score if score is not None else 8
    if len(grid_rows) != expected_rows:
        return None

    puzzle_date = date.fromisoformat(header_match["date"])
    return GirldleResult(
        puzzle_date=puzzle_date,
        score=score,
        grid="\n".join(grid_rows),
    )
