# AGENTS.md

Guidance for coding agents working on this repo.

## What this is

A multi-server Discord bot, structured as a general-purpose bot with each
feature in its own
[cog](https://discordpy.readthedocs.io/en/stable/ext/commands/cogs.html).

Current features:

- **Girldle**: ingests daily Girldle results posted in a per-guild configured
  channel, tracks Glicko-2 ratings and streaks, exposes lookups via slash
  commands. Girldle itself is the unit-guessing game in the sister repo
  `girl-kickers`.

## Where it runs

Production lives on an [exe.dev](https://exe.dev/llms.txt) VM at
`ronergate.exe.xyz`, managed by docker compose. The bot's `.env` lives only on
the VM, not in this repo. Images are built and pushed to
`ghcr.io/antistrategie/ronergate:latest` by CI on push to main.

## Multi-server model

- One image, one DB, many bot processes possible — each docker compose service
  has its own `DISCORD_TOKEN` and which cogs it loads is controlled by `COGS`.
- Per-guild channel config is stored in `girldle_config(guild_id, channel_id,
  private)`, set by `/girldle setup` (manage_guild gated).
- `girldle_posts` records every Discord message we saw; `girldle_results` holds
  the canonical first-seen result per (user, puzzle_date) for rating.
- Ratings are global. The leaderboard has `scope: global | server`. Servers can
  hide themselves from the global leaderboard via `/girldle privacy private:true`.

## Conventions

- British English in code, comments, and prose
- No em dashes in user-facing output or prose; substitute commas, periods, "and", or `·`
- All command responses are public (no `ephemeral=True`)
- Ingestion is scoped to each guild's configured Girldle channel; everything else is ignored
- Admin commands are gated on `manage_guild`
