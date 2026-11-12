"""Bounded PNG/JPEG decode and metadata-free 240×240 PNG; no AI processing."""
from io import BytesIO
from threading import BoundedSemaphore
import warnings
from PIL import Image,ImageOps,PngImagePlugin,UnidentifiedImageError
from fastapi import HTTPException

MAX_UPLOAD=2*1024*1024
MAX_PIXELS=4_000_000
Image.MAX_IMAGE_PIXELS=MAX_PIXELS
PngImagePlugin.MAX_TEXT_CHUNK=65536
PngImagePlugin.MAX_TEXT_MEMORY=262144
_slots=BoundedSemaphore(2)

def normalize_wallpaper(raw,mime):
    return _normalize(raw,mime,pet=False)

def normalize_pet(raw,mime):
    return _normalize(raw,mime,pet=True)

def _normalize(raw,mime,pet):
    if not raw or len(raw)>MAX_UPLOAD:raise HTTPException(413,'Image must be between 1 byte and 2 MiB')
    if mime not in ('image/png','image/jpeg'):raise HTTPException(415,'Only PNG and JPEG images are supported')
    if not _slots.acquire(blocking=False):raise HTTPException(429,'Image processor is busy',headers={'Retry-After':'2'})
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw),formats=['PNG','JPEG']) as image:
                expected='PNG' if mime=='image/png' else 'JPEG'
                if image.format!=expected:raise ValueError('MIME does not match file')
                if image.width>4096 or image.height>4096 or image.width*image.height>MAX_PIXELS:raise ValueError('Image dimensions exceed limit')
                if getattr(image,'n_frames',1)!=1:raise ValueError('Animated images are unsupported')
                image.verify()
            with Image.open(BytesIO(raw),formats=['PNG','JPEG']) as image:
                image.load()
                oriented=ImageOps.exif_transpose(image)
                rgba=oriented.convert('RGBA')
                if pet:
                    bounds=rgba.getchannel('A').getbbox()
                    if bounds is None:raise ValueError('Empty image')
                    fitted=ImageOps.contain(rgba.crop(bounds),(240,240),method=Image.Resampling.LANCZOS)
                else:fitted=ImageOps.fit(rgba,(240,240),method=Image.Resampling.LANCZOS)
                clean=Image.new('RGBA',fitted.size);clean.paste(fitted)
                stream=BytesIO();clean.save(stream,format='PNG')
                result=stream.getvalue()
                if len(result)>262144:raise ValueError('Normalized image exceeds limit')
                if not pet:return result
                card=Image.new('RGB',(960,960),'#080809')
                art=ImageOps.contain(clean,(880,880),method=Image.Resampling.NEAREST)
                card.paste(art,((960-art.width)//2,(960-art.height)//2),art)
                stream=BytesIO();card.save(stream,format='PNG')
                telegram_image=stream.getvalue()
                if len(telegram_image)>1048576:raise ValueError('Preview image exceeds limit')
                return result,telegram_image
    except HTTPException:raise
    except (UnidentifiedImageError,Image.DecompressionBombError,Image.DecompressionBombWarning,OSError,ValueError,SyntaxError) as error:
        raise HTTPException(422,'Invalid, animated, oversized or unsupported image') from None
    finally:_slots.release()
