# Girldle admin cog

Owner-only controls for the Girldle leaderboard. Commands are registered as
**guild-restricted** to `OWNER_GUILD_ID`, so they never appear in any other
server's slash-command list — even though the bot may be a member of those
servers.

## Why a separate cog

Two reasons:

1. **Visibility hiding**. Guild-restricted commands only sync to the specified
   guild, so they don't leak into autocomplete on other servers. (Using
   `default_permissions` only hides commands from non-admins, which doesn't
   help when server owners are admins by definition.)
2. **Per-bot opt-in**. The Girldle-branded bot doesn't have a home guild, so
   it shouldn't expose admin commands at all. Setting `COGS=girldle` (without
   `girldle_admin`) on the Girldle bot means these commands literally don't
   exist on that process.

The operator uses ROnergate to administer both bots: they share the same
SQLite volume, so a click in antistrategie updates the same DB the Girldle
bot reads.

## Commands

All gated on `bot.is_owner()` at runtime as a belt-and-braces check.

| Command | Description |
| --- | --- |
| `/girldleadmin servers` | List every known guild, approval state, channel, post count. Pending guilds get a one-click **Approve** button right in the embed. |
| `/girldleadmin approve guild_id: approved:?` | Approve or revoke a guild's global-leaderboard visibility. Useful as a fallback if the button times out. |
| `/girldleadmin remove guild_id:` | Drop a guild's config and all its posts. Canonical results survive if seen elsewhere; orphans are cleaned. |

## Enabling the cog

In `.env`:

```bash
COGS=girldle,girldle_admin
OWNER_GUILD_ID=<your home guild ID>
```

The cog hard-errors at import if `OWNER_GUILD_ID` is unset.
