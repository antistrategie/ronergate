# Girldle cog

Ingests daily [Girldle](https://antistrategie.github.io/girl-kickers/unit-builder/#girldle)
share grids posted in a configured channel, tracks per-user Glicko-2 ratings
and streaks, and exposes lookups via slash commands.

## Adding to a server

1. Invite the bot with the `applications.commands`, `Read Messages`, `Send
   Messages`, and `Read Message History` scopes.
2. As a user with `Manage Server`, run `/girldle setup #your-channel`.
3. Optional: `/girldle privacy private:true` to keep this server's results off
   the global leaderboard.
4. Optional: `/girldle backfill` to ingest existing share grids in the channel.

## Commands

| Command | Who | Description |
| --- | --- | --- |
| `/girldle leaderboard scope:global\|server` | anyone | Top players. Defaults to global. Server scope only counts members who've posted here. |
| `/girldle stats user:?` | anyone | Per-player rating, solve rate, current and best streak. |
| `/girldle h2h user1: user2:` | anyone | Head-to-head record over shared puzzles. |
| `/girldle styles` | anyone | Top snipers (fewest greens before solving) and plodders (most greens). |
| `/girldle setup channel:` | Manage Server | Configure the channel results are read from. New servers are not approved for the global leaderboard until the bot operator approves them. |
| `/girldle privacy private:` | Manage Server | Hide this server from the global leaderboard. |
| `/girldle backfill channel:? limit:? dry_run:?` | Manage Server | Read channel history and ingest historical results. |
| `/girldle reset confirm:` | Manage Server | Wipe this server's posts. Canonical results survive if seen elsewhere. |
| `/girldle approve guild_id: approved:?` | Bot owner | Approve or revoke a server's global-leaderboard visibility. |
| `/girldle servers` | Bot owner | List all known servers, their channel, approval state, and post count. |
| `/girldle remove guild_id:` | Bot owner | Drop a server's config and all its posts. Orphaned canonical results are also removed. |

## Server approval

When a new server runs `/girldle setup`, it's flagged as pending. Pending
servers can still play and use `scope=server` leaderboards; they just don't
appear on the global view until approved. The bot owner's own setups are
auto-approved.

To approve: run `/girldle servers` — pending entries are marked ⏳ and the
embed has an **Approve** button per pending guild for one-click promotion. No
notifications are pushed; you check when you want to.

## Data model

- `girldle_results` — canonical first-seen result per (user, puzzle_date). Used
  by the Glicko-2 rating math.
- `girldle_posts` — every Discord message we saw, with its guild. Used by
  scope=server leaderboards, primary-guild lookup, and audit.
- `girldle_players` — cached per-user rating, streak, and games played.
- `girldle_config` — per-guild settings: girldle channel and privacy flag.

Ratings are **global**: a user's skill rating reflects all their games across
every server. The leaderboard view can be filtered by scope and by privacy
flag, but the rating itself doesn't fork.

## Cross-server posting

If a user posts the same puzzle in two servers:

- Both posts are recorded in `girldle_posts`.
- The canonical row in `girldle_results` is whichever post arrived first.
- Their **rating** uses the canonical score (first-write-wins).
- They appear on **both** servers' `scope=server` leaderboards.
- If the second post has a different score from the first, a warning is sent
  to the configured `CONTROL_CHANNEL_ID`.
