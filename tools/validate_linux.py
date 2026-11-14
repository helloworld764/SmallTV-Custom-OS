"""Offline validation in a fresh Linux temporary directory; no system installation.

Run using Linux Python 3.13, a source bundle, cp313 Linux wheelhouse and Node archive.
The caller verifies the Node archive against official SHASUMS256.txt before invocation.
"""
import argparse,json,os,shutil,subprocess,sys,tarfile,tempfile,venv,zipfile
from pathlib import Path
from verify_release import verify

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--wheels',type=Path,required=True);parser.add_argument('--node-archive',type=Path,required=True);parser.add_argument('--art-fixtures',type=Path);args=parser.parse_args()
    verify(args.bundle)
    root=Path(tempfile.mkdtemp(prefix='storm-validation-'));source=root/'source';source.mkdir()
    # Manifest verifier has already rejected traversal, duplicates, symlinks and excessive size.
    with zipfile.ZipFile(args.bundle) as archive:archive.extractall(source)
    # Development-only approved art archive; never part of the server release bundle.
    if args.art_fixtures:shutil.copytree(args.art_fixtures,source/'assets/pets')
    venv.EnvBuilder(with_pip=False).create(root/'venv');python=root/'venv/bin/python'
    wheel=next(args.wheels.glob('pip-*.whl'));env={**os.environ,'PYTHONPATH':str(wheel),'PYTHONUTF8':'1','PIP_DISABLE_PIP_VERSION_CHECK':'1'}
    subprocess.run([str(python),'-m','pip','install','--no-index','--find-links',str(args.wheels),'-r',str(source/'backend/requirements-dev.lock')],env=env,check=True)
    with tarfile.open(args.node_archive) as archive:archive.extractall(root/'node',filter='data')
    node=next((root/'node').glob('*/bin/node'));env.pop('PYTHONPATH',None);env['PATH']=str(node.parent)+os.pathsep+env['PATH']
    print(json.dumps({'isolated_directory':str(root),'python':sys.version.split()[0]}),flush=True)
    subprocess.run([str(python),'-m','pytest','-q','--tb=short','-ra'],cwd=source,env=env,check=True)
    subprocess.run([str(node),'--test',*[str(path) for path in sorted((source/'tests').glob('*.test.mjs'))]],cwd=source,env=env,check=True)
    native=root/'native-raster'
    subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pedantic',str(source/'tests/native_raster.cpp'),'-o',str(native)],check=True)
    subprocess.run([str(native),str(source/'firmware/assets/catalog-v1/Pixel-neutral.spt')],check=True)
    print('{"status":"linux_validation_passed","native_raster":"passed"}')
    return 0
if __name__=='__main__':raise SystemExit(main())
