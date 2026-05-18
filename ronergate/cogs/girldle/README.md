# Girldle cog

Ingests daily [Girldle](https://antistrategie.github.io/girl-kickers/unit-builder/#girldle)
share grids posted in a configured channel, tracks per-user Glicko-2 ratings
and streaks, and exposes lookups via slash commands.

## Adding to a server

1. Invite the Girldle bot:
   https://discord.com/oauth2/authorize?client_id=1505855018962780240&permissions=68608&integration_type=0&scope=bot+applications.commands
2. As a user with `Manage Server`, run `/girldle setup #your-channel`.
3. Wait for the bot operator to approve your server (see "Server approval"
   below). Until then your players can use the bot but won't appear on the
   global leaderboard.
4. Optional: `/girldle privacy private:true` to keep this server's results off
   the global leaderboard even after approval.
5. Optional: `/girldle backfill` to ingest existing share grids in the channel.

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

The bot owner has additional commands under a separate `/girldleadmin`
group, registered only on the operator's home guild — see the
[`girldle_admin` cog](../girldle_admin/README.md).

## Server approval

When a new server runs `/girldle setup`, it's flagged as pending. Pending
servers can still play and use `scope=server` leaderboards; they just don't
appear on the global view until approved. The bot owner's own setups are
auto-approved.

To approve: run `/girldleadmin servers` (on the operator's home guild) —
pending entries are marked ⏳ and the embed has an **Approve** button per
pending guild for one-click promotion. No notifications are pushed; you
check when you want to.

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
