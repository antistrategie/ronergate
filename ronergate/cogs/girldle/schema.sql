CREATE TABLE IF NOT EXISTS girldle_results (
    message_id  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    puzzle_date TEXT NOT NULL,
    posted_at   TEXT NOT NULL,
    score       INTEGER,
    grid        TEXT NOT NULL,
    UNIQUE(user_id, puzzle_date)
);

CREATE INDEX IF NOT EXISTS idx_girldle_results_puzzle_date
    ON girldle_results(puzzle_date);

CREATE INDEX IF NOT EXISTS idx_girldle_results_user_id
    ON girldle_results(user_id);

CREATE TABLE IF NOT EXISTS girldle_players (
    user_id       TEXT PRIMARY KEY,
    display_name  TEXT,
    rating        REAL DEFAULT 1500,
    rd            REAL DEFAULT 350,
    volatility    REAL DEFAULT 0.06,
    games_played  INTEGER DEFAULT 0,
    last_played   TEXT
);

CREATE TABLE IF NOT EXISTS girldle_config (
    guild_id   TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    private    INTEGER NOT NULL DEFAULT 0,
    approved   INTEGER NOT NULL DEFAULT 0,
    name       TEXT
);

CREATE TABLE IF NOT EXISTS girldle_posts (
    message_id  TEXT PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    puzzle_date TEXT NOT NULL,
    posted_at   TEXT NOT NULL,
    score       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_girldle_posts_guild_id
    ON girldle_posts(guild_id);

CREATE INDEX IF NOT EXISTS idx_girldle_posts_user_puzzle
    ON girldle_posts(user_id, puzzle_date);
