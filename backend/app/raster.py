"""STP1 bounded palette raster. Transport preparation, not a validated LCD driver."""
import struct
from PIL import Image,ImageOps

MAX_SIDE=240
HEADER=struct.Struct('<4sHHHH')

def encode(image,size=128,*,trim_transparency=True):
    if not 1<=size<=MAX_SIDE:raise ValueError('Invalid raster size')
    rgba=image.convert('RGBA');bounds=rgba.getchannel('A').getbbox()
    if bounds is None and trim_transparency:raise ValueError('Empty raster')
    source=rgba.crop(bounds) if trim_transparency else rgba
    fitted=ImageOps.contain(source,(size,size),method=Image.Resampling.NEAREST)
    canvas=Image.new('RGBA',(size,size));canvas.paste(fitted,((size-fitted.width)//2,(size-fitted.height)//2))
    indexed=canvas.convert('RGB').quantize(colors=255,method=Image.Quantize.MEDIANCUT,dither=Image.Dither.NONE)
    indices=indexed.tobytes();palette=indexed.getpalette();count=max(indices)+1
    colors=bytes([0,0,0,0])+b''.join(bytes(palette[n*3:n*3+3]+[255]) for n in range(count))
    pixels=bytes(index+1 if alpha>=128 else 0 for index,alpha in zip(indices,canvas.getchannel('A').tobytes()))
    return HEADER.pack(b'STP1',size,size,count+1,0)+colors+pixels

def inspect(raw):
    if len(raw)<HEADER.size:raise ValueError('Truncated raster')
    magic,width,height,count,reserved=HEADER.unpack_from(raw)
    if magic!=b'STP1' or reserved or not 1<=width<=MAX_SIDE or not 1<=height<=MAX_SIDE or not 1<=count<=256:raise ValueError('Invalid raster header')
    offset=HEADER.size+count*4
    if len(raw)!=offset+width*height or any(index>=count for index in raw[offset:]):raise ValueError('Invalid raster payload')
    return {'width':width,'height':height,'palette_entries':count,'pixel_offset':offset,'bytes':len(raw)}
