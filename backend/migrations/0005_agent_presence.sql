CREATE TABLE agent_presence (
    agent_id TEXT PRIMARY KEY NOT NULL REFERENCES codex_agents(id) ON DELETE RESTRICT,
    last_seen_at INTEGER NOT NULL CHECK (last_seen_at >= 0)
);
