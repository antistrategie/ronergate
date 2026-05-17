"""Permission helpers."""

from __future__ import annotations

import discord

from .config import Config


def is_admin(user: discord.abc.User, config: Config) -> bool:
    """True iff user is a guild Member with at least one admin role."""
    if not isinstance(user, discord.Member):
        return False
    user_role_ids = {role.id for role in user.roles}
    return bool(user_role_ids & config.admin_role_ids)
