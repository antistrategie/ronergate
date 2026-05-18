"""Environment configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    discord_token: str
    db_path: str
    cogs: tuple[str, ...]
    owner_guild_id: int | None


def load() -> Config:
    load_dotenv()

    token = _required("DISCORD_TOKEN")
    db_path = os.environ.get("DB_PATH", "data/bot.sqlite")

    cogs_raw = os.environ.get("COGS", "girldle").strip()
    cogs = tuple(f"ronergate.cogs.{name.strip()}" for name in cogs_raw.split(",") if name.strip())
    if not cogs:
        raise RuntimeError("COGS env var resolved to an empty list")

    return Config(
        discord_token=token,
        db_path=db_path,
        cogs=cogs,
        owner_guild_id=_optional_int("OWNER_GUILD_ID"),
    )


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _optional_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else None
