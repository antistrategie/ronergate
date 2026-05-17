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
