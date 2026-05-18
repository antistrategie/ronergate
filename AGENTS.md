# AGENTS.md

Guidance for coding agents working on this repo.

## What this is

General-purpose Discord bot for the Antistratégie server, with each feature
in its own
[cog](https://discordpy.readthedocs.io/en/stable/ext/commands/cogs.html).

The same image is also deployed as a separate `Girldle`-branded public bot
(different token, different application) that other servers can invite. See
"Where it runs" below.

Current features:

- **Girldle** (`cogs/girldle/`): ingests daily Girldle results posted in a
  per-guild configured channel, tracks Glicko-2 ratings and streaks, exposes
  lookups via slash commands. Girldle itself is the unit-guessing game in the
  sister repo `girl-kickers`.
- **Girldle admin** (`cogs/girldle_admin/`): bot-owner controls (`approve`,
  `servers`, `remove`) registered as guild-restricted commands to
  `OWNER_GUILD_ID` so they don't appear in any other server's autocomplete.

## Where it runs

Production lives on an [exe.dev](https://exe.dev/llms.txt) VM at
`ronergate.exe.xyz`, managed by docker compose with two services sharing a
single SQLite volume:

- `ronergate`: the operator's bot. Loads `girldle` + `girldle_admin`. Lives in
  the antistrategie server.
- `girldle`: the public bot. Loads only `girldle`. Invitable by other servers.

Images are built by CI on push to main and pushed to
`ghcr.io/antistrategie/ronergate:latest`. Deploy is just
`docker compose pull && docker compose up -d` on the host. The two `.env`
files live only on the VM, never in the repo.

## Schema

- `girldle_results(message_id PK, user_id, puzzle_date, posted_at, score, grid)`
  — one canonical row per (user, puzzle_date). First-seen wins.
- `girldle_posts(message_id PK, guild_id, user_id, puzzle_date, posted_at, score)`
  — every Discord message we observed. Source of truth for "which guilds saw
  this user".
- `girldle_players(user_id PK, display_name, rating, rd, volatility,
  games_played, last_played)` — Glicko-2 cache. Ratings are global.
- `girldle_config(guild_id PK, channel_id, private, approved, name)` —
  per-guild settings. `approved`/`private` gate global-leaderboard visibility.
  `name` is a cached guild name so the bot can render it from any process.

Boot-time migrations live in `cogs/girldle/migrate.py`. Idempotent ALTERs,
race-safe across the two bot processes.

## Slash commands

Public (`/girldle ...`): `leaderboard`, `stats`, `h2h`, `styles`, `setup`,
`privacy`, `backfill`, `reset`. All read commands gated on
`/girldle setup` having been run for the current guild.

Owner-only (`/girldleadmin ...`, guild-restricted): `approve`, `servers`,
`remove`. `bot.is_owner()` check at runtime as belt-and-braces.

## Share-grid format

Format produced by `girl-kickers` and parsed by `cogs/girldle/parser.py`:

```
Girldle · YYYY-MM-DD · N/8​​​​​   ← N trailing U+200B watermark chars
<emoji grid>
<https://antistrategie.github.io/...>
```

Old bare-space-separated format still parses for legacy posts. Watermark
absence sets `verified=False` on the parsed result but doesn't reject
ingestion.

## Conventions

- British English in code, comments, and prose
- No em dashes in user-facing output or prose; substitute commas, periods,
  "and", or `·`
- All command responses are public (no `ephemeral=True`)
- Admin commands gated on `manage_guild`; owner-only commands gated on
  `is_owner()` and registered guild-restricted via `OWNER_GUILD_ID`
- CI runs `ruff check` + `pytest` before building the image
