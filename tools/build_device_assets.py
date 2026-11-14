"""Compile approved public pets and a licensed Cyrillic font into bounded assets."""
import argparse,hashlib,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image,ImageDraw,ImageFont
from backend.app.raster import encode,inspect

STATES=('neutral','happy','eating','playing','sleeping','waking','hungry_sad','tired','blink')
KINDS=('Pixel','Byte','Glitch')

def build(output,root=ROOT):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    bounds=json.loads((root/'web/simulator/pet-frame-bounds.mjs').read_text().split('=',1)[1].strip().rstrip(';'))
    manifest={'schema_version':1,'purpose':'device protocol preparation; LCD/Flash/RAM not hardware-validated','encoding':'STP1','files':{},'sources':{}}
    for kind in KINDS:
        source=root/'web/simulator/pets'/f'{kind}-states.png';manifest['sources'][source.name]=hashlib.sha256(source.read_bytes()).hexdigest()
        with Image.open(source) as image:
            for index,state in enumerate(STATES):
                x,y,w,h=bounds[kind][index];cell=image.crop((round(x*image.width),round(y*image.height),round((x+w)*image.width),round((y+h)*image.height)))
                raw=encode(cell,128);name=f'{kind}-{state}.spt';target=output/name
                if target.exists() and target.read_bytes()!=raw:raise ValueError('Output differs; choose a fresh output directory')
                target.write_bytes(raw);manifest['files'][name]={**inspect(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    font_path=root/'assets/fonts/NotoSansMono-Regular.ttf'
    font=ImageFont.truetype(str(font_path),16);characters=''.join(chr(n) for n in range(32,127))+''.join(chr(n) for n in range(0x410,0x450))+'Ёё°·–—…'
    unknown=bytes(font.getmask('\u0378'));glyphs=[];data=bytearray();width,height=16,24
    for char in characters:
        if char!=' ' and bytes(font.getmask(char))==unknown:raise ValueError('Required font glyph is missing')
        image=Image.new('1',(width,height));ImageDraw.Draw(image).text((0,0),char,font=font,fill=1,anchor='la')
        raw=image.tobytes();glyphs.append({'codepoint':ord(char),'offset':len(data),'advance':round(font.getlength(char))});data.extend(raw)
    (output/'cyrillic-16.bin').write_bytes(data)
    font_meta={'encoding':'1bit-msb-row','cell_width':width,'cell_height':height,'bytes_per_glyph':48,'glyphs':glyphs,'fallback_codepoint':63,'license':'SIL-OFL-1.1','font_sha256':hashlib.sha256(font_path.read_bytes()).hexdigest()}
    (output/'cyrillic-16.json').write_text(json.dumps(font_meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    manifest['font']={'glyphs':len(glyphs),'bitmap_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'cell_bytes':48,'license_file':'assets/fonts/OFL.txt'}
    manifest['total_raster_bytes']=sum(item['bytes'] for item in manifest['files'].values())
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return manifest

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=build(args.output);print(json.dumps({'frames':len(result['files']),'raster_bytes':result['total_raster_bytes'],'font':result['font']}))
if __name__=='__main__':main()
