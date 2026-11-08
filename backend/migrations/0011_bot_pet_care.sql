ALTER TABLE telegram_replies ADD COLUMN photo_asset TEXT;
CREATE TABLE telegram_pet_drafts (
    telegram_id INTEGER PRIMARY KEY REFERENCES telegram_links(telegram_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('Pixel','Byte','Glitch')),
    expires_at INTEGER NOT NULL
);
CREATE TABLE telegram_pet_intents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    pet_id TEXT NOT NULL REFERENCES pets(id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK (action IN ('feed','play','sleep','wake','return')),
    round INTEGER NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0,1)),
    expires_at INTEGER NOT NULL
);
CREATE INDEX idx_telegram_pet_intents_owner ON telegram_pet_intents(user_id);
