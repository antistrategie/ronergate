"""Environment configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int
    girldle_channel_id: int
    admin_role_ids: frozenset[int]
    control_channel_id: int | None
    db_path: str


def load() -> Config:
    load_dotenv()

    token = _required("DISCORD_TOKEN")
    guild_id = int(_required("GUILD_ID"))
    girldle_channel_id = int(_required("GIRLDLE_CHANNEL_ID"))
    admin_role_ids = frozenset(
        int(x.strip()) for x in os.environ.get("ADMIN_ROLE_IDS", "").split(",") if x.strip()
    )
    control_channel_id_raw = os.environ.get("CONTROL_CHANNEL_ID", "").strip()
    control_channel_id = int(control_channel_id_raw) if control_channel_id_raw else None
    db_path = os.environ.get("DB_PATH", "data/bot.sqlite")

    return Config(
        discord_token=token,
        guild_id=guild_id,
        girldle_channel_id=girldle_channel_id,
        admin_role_ids=admin_role_ids,
        control_channel_id=control_channel_id,
        db_path=db_path,
    )


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value
