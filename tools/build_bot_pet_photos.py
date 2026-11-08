"""Offline catalogue asset preparation; Pillow is needed only to regenerate PNGs."""

import hashlib
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'web'/'simulator'/'pets'
STATES = ('neutral','happy','eating','playing','sleeping','waking','hungry_sad','tired','blink')


def main():
    output = SOURCE/'bot'
    output.mkdir(exist_ok=True)
    manifest = {'purpose':'Storm Bot catalogue thumbnails; not firmware assets','size':[320,320],'sources':{},'files':{}}
    for kind in ('Pixel','Byte','Glitch'):
        path = SOURCE/(kind+'-states.png')
        manifest['sources'][kind] = hashlib.sha256(path.read_bytes()).hexdigest()
        image = Image.open(path).convert('RGBA')
        width,height = image.size
        for index,state in enumerate(STATES):
            col,row = index%3,index//3
            cell = image.crop((col*width//3,row*height//3,(col+1)*width//3,(row+1)*height//3))
            bounds = cell.getchannel('A').getbbox()
            if bounds:
                cell = cell.crop(bounds)
            scale = min(300/cell.width,300/cell.height)
            cell = cell.resize((max(1,round(cell.width*scale)),max(1,round(cell.height*scale))),Image.Resampling.NEAREST)
            canvas = Image.new('RGB',(320,320),(8,8,9))
            canvas.paste(cell,((320-cell.width)//2,(320-cell.height)//2),cell)
            target = output/f'{kind}-{state}.png'
            canvas.save(target,optimize=True)
            manifest['files'][target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    main()
