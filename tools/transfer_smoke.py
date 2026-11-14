"""Synthetic Windows -> Linux SQLite restore drill. Never uses Settings/.env."""
import argparse,hashlib,json,secrets,sys
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.app.database import Database
from backend.app.security import create_account,token_hash
from backend.app.pets import Adopt,adopt_owned
from backend.app.backups import backup_database,restore_database,verify_backup

def fingerprint(database):
    tables=('users','local_accounts','devices','device_settings','pets','pet_states')
    with database.connection() as connection:
        values={table:[tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY rowid')] for table in tables}
    return hashlib.sha256(json.dumps(values,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def create(directory):
    directory.mkdir(parents=True,exist_ok=False)
    database=Database(directory/'source.sqlite3');database.initialize()
    owner=create_account(database,'transfer-test',secrets.token_urlsafe(24),'Transfer test')
    identity=str(uuid4())
    with database.transaction() as connection:
        connection.execute('INSERT INTO devices(id,owner_id,name) VALUES(?,?,?)',(identity,owner,'Тест переноса'))
        connection.execute('INSERT INTO device_settings(device_id,brightness,theme) VALUES(?,42,?)',(identity,'dark'))
        connection.execute('INSERT INTO device_credentials VALUES(?,?,1000,NULL)',(identity,token_hash('synthetic-'+uuid4().hex)))
        adopt_owned(connection,owner,Adopt(kind='Pixel',name='Мурка'),1000)
    backup_database(database.path,directory/'snapshot.sqlite3')
    (directory/'expected.json').write_text(json.dumps({'persistent_hash':fingerprint(database)}),encoding='utf-8')

def check(directory,output):
    source=directory/'snapshot.sqlite3';before=hashlib.sha256(source.read_bytes()).hexdigest();verify_backup(source)
    report=restore_database(source,output,active_database=directory/'source.sqlite3')
    database=Database(output)
    assert fingerprint(database)==json.loads((directory/'expected.json').read_text())['persistent_hash']
    assert before==hashlib.sha256(source.read_bytes()).hexdigest()
    with database.connection() as connection:
        assert connection.execute('SELECT count(*) FROM auth_sessions').fetchone()[0]==0
        assert connection.execute('SELECT count(*) FROM device_credentials WHERE revoked_at IS NULL').fetchone()[0]==0
    return report.schema_version

def main():
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=('create','check'));parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--output',type=Path);args=parser.parse_args()
    if args.operation=='create':create(args.directory);result={'status':'synthetic_backup_created'}
    else:
        if args.output is None:parser.error('--output required for check')
        result={'status':'transfer_restore_verified','schema_version':check(args.directory,args.output)}
    print(json.dumps(result));return 0
if __name__=='__main__':raise SystemExit(main())
