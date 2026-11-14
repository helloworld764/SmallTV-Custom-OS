"""Reproducible source bundle. User data and local credentials are never packaged."""
import argparse,hashlib,json,subprocess,zipfile
from itertools import chain
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TOP={'README.md','TZ.md','AGENTS.md','.env.example','.gitignore','pytest.ini'}
FORBIDDEN={'.git','.venv','secrets','credentials','data','backups','tmp','temp','uploads','__pycache__','node_modules','.pytest_cache'}
SUFFIXES={'.py','.sql','.txt','.lock','.md','.mjs','.js','.css','.html','.png','.json','.svg','.spt','.bin','.ttf','.service','.example','.conf','.cpp','.h','.sh'}
def files(root=ROOT,include_tests=False):
    directories={'backend','web','firmware','codex-monitor','tools','docs','deploy'}
    if include_tests:directories.add('tests')
    result=[]
    candidates=chain((root/name for name in TOP),*( (root/name).rglob('*') for name in sorted(directories) ),(root/'assets/fonts').rglob('*'))
    for path in sorted(candidates):
        relative=path.relative_to(root)
        if not path.is_file() or any(part in FORBIDDEN for part in relative.parts):continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):raise ValueError('Bundle refuses symlinks or external paths')
        if len(relative.parts)==1:
            if path.name not in TOP:continue
        elif relative.parts[0] not in directories and relative.parts[:2]!=('assets','fonts'):continue
        if len(relative.parts)>1 and path.suffix not in SUFFIXES:continue
        if path.name.startswith('.env') and path.name!='.env.example':continue
        if path.suffix in ('.db','.sqlite','.sqlite3','.key','.pem','.dpapi','.log'):continue
        result.append((relative.as_posix(),path))
    return result

def build(output,root=ROOT,include_tests=False):
    selected=files(root,include_tests);manifest={'schema_version':1,'files':{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in selected}}
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,path in selected:
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            archive.writestr(info,path.read_bytes())
        info=zipfile.ZipInfo('RELEASE-MANIFEST.json',(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
        archive.writestr(info,json.dumps(manifest,sort_keys=True,indent=2))
    return manifest

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--include-tests',action='store_true');args=parser.parse_args()
    try:
        manifest=build(args.output,include_tests=args.include_tests)
        print(json.dumps({'status':'built','files':len(manifest['files']),'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest()}));return 0
    except (OSError,ValueError,zipfile.BadZipFile):print('{"status":"bundle_failed"}');return 1
if __name__=='__main__':raise SystemExit(main())
