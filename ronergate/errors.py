"""Error reporting to a Discord channel."""

from __future__ import annotations

import logging
import traceback

import discord
from discord.ext import commands

log = logging.getLogger(__name__)


async def report(bot: commands.Bot, header: str, exc: BaseException) -> None:
    """Log an exception and (if configured) post a short summary to the control channel."""
    log.exception("%s: %s", header, exc)
    channel_id = bot.config.control_channel_id  # type: ignore[attr-defined]
    if channel_id is None:
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        log.warning("control_channel_id=%s not found in cache", channel_id)
        return

    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    body = f"⚠️ {header}\n```\n{tb}\n```"
    try:
        await channel.send(body[:1990])
    except discord.HTTPException as e:
        log.warning("failed to post error report: %s", e)
