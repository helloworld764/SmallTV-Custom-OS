"""Freeze the tested dependency graph; never reads local application configuration."""
from importlib.metadata import distribution
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT=Path(__file__).resolve().parents[1]
def graph(roots):
    result={};todo=list(roots)
    while todo:
        name=canonicalize_name(todo.pop())
        if name in result:continue
        item=distribution(name);result[name]=item.version
        for raw in item.requires or []:
            req=Requirement(raw)
            if req.marker is None or req.marker.evaluate({'extra':''}):todo.append(req.name)
    return result

def main():
    runtime=graph(['fastapi','uvicorn','pydantic-settings','Pillow'])
    dev=graph(['pytest','httpx'])
    header='# Tested exact versions; regenerate with tools/freeze_dependencies.py after validation.\n'
    def lines(items):return ''.join(f'{name}=={version}'+('; sys_platform == "win32"' if name=='colorama' else '')+'\n' for name,version in sorted(items.items()))
    (ROOT/'backend/requirements-runtime.lock').write_text(header+lines(runtime),encoding='utf-8')
    (ROOT/'backend/requirements-dev.lock').write_text(header+'-r requirements-runtime.lock\n'+lines({k:v for k,v in dev.items() if k not in runtime}),encoding='utf-8')
    print(f'Pinned runtime={len(runtime)}, additional development={len(set(dev)-set(runtime))}')
if __name__=='__main__':main()
