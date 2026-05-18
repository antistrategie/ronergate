# ROnergate

Multi-server Discord bot. Currently runs the Girldle scraper + leaderboard for
the Antistratégie server.

## Run locally

```bash
uv venv --python 3.14
uv pip install -e ".[dev]"
cp .env.example .env  # fill in
ronergate
```

## Deploy

CI publishes `ghcr.io/antistrategie/ronergate:latest` on push to main. On the VM:

```bash
git pull
docker compose pull
docker compose up -d
```

## Tests

```bash
pytest
```

## Adding the bot to a new server

1. Invite the bot with the `applications.commands`, `Read Messages`, `Send
   Messages`, and `Read Message History` scopes.
2. As a user with `Manage Server`, run `/girldle setup #your-channel`.
3. Optional: `/girldle privacy private:true` to keep this server's results off
   the global leaderboard.
4. Optional: `/girldle backfill` to ingest existing share grids in the channel.
