"""Printed representative Turkish business documents, not production originals.

Pixels are rendered independently of recognition; assertions consume real OCR.
The deterministic thirty-document corpus includes damage/orientation/PDF variants.
"""
import io
from PIL import Image,ImageDraw,ImageFont,ImageFilter,ImageEnhance

TEXTS=(
    'Görgün\n08.10.2026\nGrand 100 20 2000\nBirinci 50 15 750\nİkinci 25 10 250\nToplam 3000\nAlacak 3000',
    'TEST FATURA\n08.10.2026\nKNO3 100 kg\nTutar 5000 TRY\nÖdeme yapılmadı',
    'Görgün\n08.10.2026\nGrand 100 20 2000\nBirinci 50 15 750\nİkinci 25 10 250\nToplam 3100\nAlacak 3100',
)

def document(n):
    font=ImageFont.truetype('DejaVuSans.ttf',38)
    im=Image.new('RGB',(1100,650),'white');draw=ImageDraw.Draw(im)
    draw.multiline_text((50,40),TEXTS[n%3],font=font,fill='black',spacing=20)
    variant=n//3
    if variant in (1,2,3):im=im.rotate(90*variant,expand=True)
    if variant==4:im=im.filter(ImageFilter.GaussianBlur(1.1))
    if variant==5:im=ImageEnhance.Contrast(im).enhance(.55)
    if variant==6:im=im.filter(ImageFilter.GaussianBlur(5))
    if variant==7:
        draw=ImageDraw.Draw(im);draw.rectangle((200,240,750,305),fill='white')
    stream=io.BytesIO()
    if variant==9:
        im.save(stream,format='PDF',save_all=True,append_images=[im]);mime='application/pdf'
    else:im.save(stream,format='PNG');mime='image/png'
    return stream.getvalue(),mime
