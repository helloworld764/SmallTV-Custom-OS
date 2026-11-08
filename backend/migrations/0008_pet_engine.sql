CREATE TABLE pets (
    id TEXT PRIMARY KEY NOT NULL,
    owner_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('Pixel', 'Byte', 'Glitch')),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 24),
    created_at INTEGER NOT NULL CHECK (created_at >= 0)
);
CREATE TABLE pet_states (
    pet_id TEXT PRIMARY KEY NOT NULL REFERENCES pets(id) ON DELETE RESTRICT,
    satiety INTEGER NOT NULL CHECK (typeof(satiety) = 'integer' AND satiety BETWEEN 0 AND 360000),
    energy INTEGER NOT NULL CHECK (typeof(energy) = 'integer' AND energy BETWEEN 0 AND 360000),
    mood INTEGER NOT NULL CHECK (typeof(mood) = 'integer' AND mood BETWEEN 0 AND 360000),
    sleeping INTEGER NOT NULL CHECK (sleeping IN (0, 1)),
    updated_at INTEGER NOT NULL CHECK (updated_at >= 0),
    last_care_at INTEGER NOT NULL CHECK (last_care_at >= 0),
    round INTEGER NOT NULL CHECK (round >= 1),
    feed_at INTEGER,
    play_at INTEGER,
    sleep_at INTEGER,
    reaction_id TEXT NOT NULL,
    reaction_kind TEXT NOT NULL CHECK (reaction_kind IN ('ADOPT', 'FEED', 'PLAY', 'SLEEP', 'WAKE', 'RETURN')),
    reaction_at INTEGER NOT NULL CHECK (reaction_at >= 0)
);
CREATE TABLE pet_actions (
    pet_id TEXT NOT NULL REFERENCES pets(id) ON DELETE RESTRICT,
    action_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('feed', 'play', 'sleep', 'wake', 'return')),
    confirmed INTEGER NOT NULL CHECK (confirmed IN (0, 1)),
    applied_at INTEGER NOT NULL CHECK (applied_at >= 0),
    round INTEGER NOT NULL CHECK (round >= 1),
    PRIMARY KEY (pet_id, action_id)
);
CREATE INDEX idx_pet_actions_time ON pet_actions(pet_id, applied_at);
