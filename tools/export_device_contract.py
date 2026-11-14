"""Export/check deterministic wire schemas using already-pinned Pydantic."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from firmware.tools.protocol_contract import DisplayEnvelope,EventsEnvelope,schema

def documents():
    return {name:json.dumps(schema(model),ensure_ascii=False,sort_keys=True,indent=2)+'\n'
            for name,model in [('display-v1.schema.json',DisplayEnvelope),('events-v1.schema.json',EventsEnvelope)]}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true');parser.add_argument('--directory',type=Path,default=ROOT/'firmware/protocol');args=parser.parse_args()
    folder=args.directory
    if args.write:folder.mkdir(parents=True,exist_ok=True)
    for name,content in documents().items():
        target=folder/name
        if args.write:
            with target.open('x',encoding='utf-8',newline='\n') as stream:stream.write(content)
        elif target.read_text(encoding='utf-8')!=content:
            print('{"status":"contract_drift"}');return 1
    print('{"status":"contract_written"}' if args.write else '{"status":"contract_verified"}');return 0
if __name__=='__main__':raise SystemExit(main())
