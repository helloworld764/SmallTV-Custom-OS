ALTER TABLE telegram_replies ADD COLUMN notification_category TEXT;
ALTER TABLE telegram_replies ADD COLUMN notification_at INTEGER;
ALTER TABLE telegram_preferences ADD COLUMN enabled_since INTEGER DEFAULT 0;
ALTER TABLE telegram_preferences ADD COLUMN pet_enabled_since INTEGER DEFAULT 0;
UPDATE telegram_preferences SET enabled_since=CASE WHEN enabled=1 THEN cast(strftime('%s','now') AS INTEGER) ELSE NULL END;
CREATE TABLE pet_sleep_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL UNIQUE,
    pet_id TEXT NOT NULL REFERENCES pets(id) ON DELETE RESTRICT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    round INTEGER NOT NULL,
    started_at INTEGER NOT NULL,
    due_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','completed','cancelled'))
);
CREATE INDEX idx_pet_sleep_due ON pet_sleep_sessions(status,due_at);
INSERT INTO pet_sleep_sessions(session_key,pet_id,user_id,round,started_at,due_at)
SELECT 'legacy:'||pets.id||':'||coalesce(sleep_at,updated_at),pets.id,owner_id,round,
       coalesce(sleep_at,updated_at),coalesce(sleep_at,updated_at)+3600
FROM pets JOIN pet_states ON pet_states.pet_id=pets.id WHERE sleeping=1;
