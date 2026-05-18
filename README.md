# ROnergate

Discord bot for the [Antistratégie](https://discord.gg/XcfYGmxvde) server.
General-purpose, structured as a set of cogs that can be enabled per-process
via `COGS`. Currently:

- [Girldle](ronergate/cogs/girldle/README.md) — daily Girldle scraper,
  Glicko-2 leaderboard, per-server channels, global or per-server view.
- [Girldle admin](ronergate/cogs/girldle_admin/README.md) — operator-only
  controls registered guild-restricted to the home server.

The same image also runs as a separate, public **Girldle** bot that other
servers can invite — see "Two bots from one image" below.

## Run locally

```bash
uv venv --python 3.14
uv pip install -e ".[dev]"
cp .env.example .env  # fill in DISCORD_TOKEN etc.
ronergate
```

## Tests

```bash
pytest
```

## Deploy

CI publishes `ghcr.io/antistrategie/ronergate:latest` on every push to `main`.

On the host, you only need two files: `docker-compose.yml` and `.env`.

```yaml
# docker-compose.yml
services:
  ronergate:
    image: ghcr.io/antistrategie/ronergate:latest
    container_name: ronergate
    restart: unless-stopped
    env_file: .env
    volumes:
      - ronergate-data:/app/data
    pull_policy: always

volumes:
  ronergate-data:
```

```bash
# .env (see .env.example)
DISCORD_TOKEN=your-bot-token
DB_PATH=/app/data/bot.sqlite
COGS=girldle,girldle_admin
OWNER_GUILD_ID=your-home-guild-id
```

To deploy a new version:

```bash
docker compose pull && docker compose up -d
```

## Two bots from one image

The repo's deploy on `ronergate.exe.xyz` runs two services from the same
image, sharing one SQLite volume:

- **ROnergate** (this bot, in Antistratégie): loads `girldle` +
  `girldle_admin`. Admin commands are guild-restricted via `OWNER_GUILD_ID`.
- **Girldle** (separate Discord application, invitable by other servers):
  loads only `girldle`. Same code, different token.

```yaml
services:
  ronergate:
    image: ghcr.io/antistrategie/ronergate:latest
    env_file: .env.ronergate    # COGS=girldle,girldle_admin · OWNER_GUILD_ID set
    volumes: [ronergate-data:/app/data]
  girldle:
    image: ghcr.io/antistrategie/ronergate:latest
    env_file: .env.girldle      # different token · COGS=girldle
    volumes: [ronergate-data:/app/data]
```

Sharing the volume means both bots see the same leaderboard data, and
ROnergate can administer Girldle's view of it via `/girldleadmin` (since
the admin cog reads/writes the shared DB).
