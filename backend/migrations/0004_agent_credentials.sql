CREATE TABLE codex_agents (
    id TEXT PRIMARY KEY NOT NULL,
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 80),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX idx_codex_agents_owner ON codex_agents(owner_id);
CREATE TABLE agent_credentials (
    agent_id TEXT PRIMARY KEY NOT NULL REFERENCES codex_agents(id) ON DELETE RESTRICT,
    token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    created_at INTEGER NOT NULL,
    revoked_at INTEGER
);
