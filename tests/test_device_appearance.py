from backend.app.database import Database, MIGRATIONS
from backend.app.devices import SettingsPatch
import pytest

def test_upgrade_preserves_old_style_settings_and_buttons(tmp_path):
    old = tmp_path / 'old'; old.mkdir()
    for file in sorted(MIGRATIONS.glob('*.sql'))[:16]: (old / file.name).write_bytes(file.read_bytes())
    db = Database(tmp_path / 'upgrade.sqlite3', old); db.initialize()
    with db.transaction() as c:
        c.execute("INSERT INTO users(id,display_name) VALUES ('owner','Owner')")
        c.execute("INSERT INTO devices(id,owner_id,name) VALUES ('tv','owner','TV')")
        c.execute("INSERT INTO device_settings(device_id,clock_style,start_screen,brightness) VALUES ('tv','terminal','null',42)")
        c.execute("INSERT INTO telegram_device_intents VALUES ('button','owner','tv','clock_style','minimal','terminal',1000,0)")
    upgraded = Database(db.path); upgraded.initialize(); upgraded.initialize()
    with upgraded.connection() as c:
        row = c.execute('SELECT * FROM device_settings').fetchone()
        assert (row['device_style'],row['clock_style'],row['clock_layout'],row['start_screen'],row['brightness']) == ('terminal','terminal','regular','tamagotchi',42)
        assert c.execute('SELECT id FROM telegram_device_intents').fetchone()[0] == 'button'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()

@pytest.mark.parametrize('field,value', [('device_style','bad'),('clock_layout','bad'),('device_style',None),('clock_layout',None)])
def test_invalid_appearance_rejected(field,value):
    with pytest.raises(ValueError): SettingsPatch(**{field:value})
