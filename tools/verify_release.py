"""Verify bundle contents before extracting into a NEW directory."""
import argparse,hashlib,json,zipfile
from pathlib import Path,PurePosixPath

MAX_TOTAL=128*1024*1024

def verify(source):
    with zipfile.ZipFile(source) as archive:
        entries=archive.infolist();names=[item.filename for item in entries]
        if len(names)!=len(set(names)) or sum(item.file_size for item in entries)>MAX_TOTAL:raise ValueError('Invalid archive size or duplicate entries')
        for item in entries:
            path=PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename or ':' in item.filename or (item.external_attr>>16)&0o170000==0o120000:
                raise ValueError('Unsafe archive path')
        if 'RELEASE-MANIFEST.json' not in names:raise ValueError('Manifest missing')
        manifest=json.loads(archive.read('RELEASE-MANIFEST.json'))
        if not isinstance(manifest,dict) or manifest.get('schema_version')!=1 or not isinstance(manifest.get('files'),dict):raise ValueError('Invalid manifest')
        if set(manifest['files'])!=set(names)-{'RELEASE-MANIFEST.json'}:raise ValueError('Manifest mismatch')
        for name,digest in manifest['files'].items():
            if hashlib.sha256(archive.read(name)).hexdigest()!=digest:raise ValueError('Checksum mismatch')
    return manifest

def main():
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);args=parser.parse_args()
    try:result=verify(args.source);print(json.dumps({'status':'verified','files':len(result['files'])}));return 0
    except (OSError,ValueError,KeyError,zipfile.BadZipFile):print('{"status":"verification_failed"}');return 1
if __name__=='__main__':raise SystemExit(main())
