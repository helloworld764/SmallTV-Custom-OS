CREATE TABLE device_journal (
 id TEXT PRIMARY KEY,
 device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
 kind TEXT NOT NULL CHECK(kind IN ('device.renamed','settings.changed','screen.changed',
   'connection.online','connection.stale','connection.offline','connection.restored',
   'connection.unknown','connection.revoked','connection.reset')),
 occurred_at INTEGER NOT NULL CHECK(occurred_at>=0),
 details TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_device_journal_page ON device_journal(device_id,occurred_at DESC,id DESC);
CREATE INDEX idx_device_journal_order ON device_journal(device_id,id DESC);
CREATE TABLE device_journal_presence (
 device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE RESTRICT,
 last_seen_at INTEGER NOT NULL,
 stale_seconds INTEGER NOT NULL,
 offline_seconds INTEGER NOT NULL,
 clock_unknown INTEGER NOT NULL DEFAULT 0 CHECK(clock_unknown IN (0,1))
);
