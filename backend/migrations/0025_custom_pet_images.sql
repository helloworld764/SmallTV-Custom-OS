CREATE TABLE pet_appearances (
 pet_id TEXT PRIMARY KEY REFERENCES pets(id) ON DELETE RESTRICT,
 id TEXT NOT NULL UNIQUE,
 image BLOB NOT NULL CHECK(length(image) BETWEEN 1 AND 262144),
 telegram_image BLOB NOT NULL CHECK(length(telegram_image) BETWEEN 1 AND 1048576),
 updated_at INTEGER NOT NULL
);
CREATE TABLE pet_image_drafts (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 image BLOB CHECK(image IS NULL OR length(image) BETWEEN 1 AND 262144),
 telegram_image BLOB CHECK(telegram_image IS NULL OR length(telegram_image) BETWEEN 1 AND 1048576),
 sha256 TEXT NOT NULL,
 expires_at INTEGER NOT NULL,
 confirmed_pet_id TEXT REFERENCES pets(id) ON DELETE RESTRICT,
 confirmed_name TEXT,
 CHECK ((confirmed_pet_id IS NULL AND image IS NOT NULL AND telegram_image IS NOT NULL)
     OR (confirmed_pet_id IS NOT NULL AND image IS NULL AND telegram_image IS NULL AND confirmed_name IS NOT NULL))
);
CREATE UNIQUE INDEX idx_pet_pending_owner ON pet_image_drafts(user_id) WHERE confirmed_pet_id IS NULL;
CREATE TABLE pet_image_limits (
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 window_start INTEGER NOT NULL, attempts INTEGER NOT NULL
);
CREATE TABLE telegram_custom_pet_inputs (
 telegram_id INTEGER PRIMARY KEY REFERENCES telegram_links(telegram_id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 token TEXT NOT NULL,
 step TEXT NOT NULL CHECK(step IN ('photo','name','confirm')),
 draft_id TEXT REFERENCES pet_image_drafts(id) ON DELETE CASCADE,
 name TEXT,
 expires_at INTEGER NOT NULL
);
