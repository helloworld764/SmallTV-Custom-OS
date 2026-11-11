ALTER TABLE telegram_replies ADD COLUMN edit_message_id INTEGER DEFAULT NULL
 CHECK (edit_message_id IS NULL OR edit_message_id > 0);
