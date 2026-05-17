# AGENTS.md

Guidance for coding agents working on this repo.

## What this is

A Discord bot for the Antistratégie server. Structured as a general-purpose
bot, with each feature isolated in its own
[cog](https://discordpy.readthedocs.io/en/stable/ext/commands/cogs.html).

Current features:

- **Girldle**: ingests daily Girldle results posted in a configured channel,
  tracks Glicko-2 ratings and streaks, exposes lookups via slash commands.
  Girldle itself is the unit-guessing game in the sister repo `girl-kickers`.

## Where it runs

Production lives on an [exe.dev](https://exe.dev/llms.txt) VM, managed by docker compose.
The bot's `.env` lives only on the VM, not in this repo.

## Conventions

- British English in code, comments, and prose
- No em dashes in user-facing output or prose; substitute commas, periods, "and", or `·`
- All command responses are public (no `ephemeral=True`)
- Ingestion and reactions are scoped to the configured Girldle channel; everything else is ignored
