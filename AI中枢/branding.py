"""Product identity shared by source and frozen interfaces."""
from . import VERSION
from .core import resource_root

PRODUCT_NAME = 'GodHand'


def logo_photo(master, size=48):
    from PIL import Image, ImageOps, ImageTk
    with Image.open(resource_root() / 'logo.png') as image:
        fitted = ImageOps.contain(image.convert('RGBA'), (size, size), Image.Resampling.LANCZOS)
        canvas = Image.new('RGBA', (size, size))
        canvas.alpha_composite(fitted, ((size-fitted.width)//2, (size-fitted.height)//2))
    return ImageTk.PhotoImage(canvas, master=master)
