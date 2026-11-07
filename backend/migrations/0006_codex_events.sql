CREATE TABLE codex_runs (
    agent_id TEXT NOT NULL REFERENCES codex_agents(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL,
    last_reported_state TEXT NOT NULL CHECK (last_reported_state IN ('WORKING', 'DONE', 'ERROR', 'INTERRUPTED')),
    last_sequence INTEGER NOT NULL CHECK (last_sequence >= 1),
    started_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (agent_id, run_id)
);
CREATE INDEX idx_codex_runs_updated ON codex_runs(agent_id, updated_at DESC, run_id);
CREATE TABLE codex_events (
    agent_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    kind TEXT NOT NULL CHECK (kind IN ('START', 'WORKING', 'DONE', 'ERROR', 'INTERRUPTED')),
    occurred_at INTEGER NOT NULL CHECK (occurred_at >= 0),
    received_at INTEGER NOT NULL,
    PRIMARY KEY (agent_id, event_id),
    UNIQUE (agent_id, run_id, sequence),
    FOREIGN KEY (agent_id, run_id) REFERENCES codex_runs(agent_id, run_id) ON DELETE RESTRICT
);
CREATE INDEX idx_codex_events_received ON codex_events(agent_id, received_at);
