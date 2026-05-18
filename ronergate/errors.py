"""Error reporting helper."""

from __future__ import annotations

import logging

from discord.ext import commands

log = logging.getLogger(__name__)


async def report(bot: commands.Bot, header: str, exc: BaseException) -> None:
    """Log an exception. Bots run in `docker logs` so that's where reports land."""
    del bot  # kept in the signature for callsite stability
    log.exception("%s: %s", header, exc)
