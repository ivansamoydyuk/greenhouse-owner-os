"""Local recognition only; provider receives bytes and cannot call domain services.

Poppler + Tesseract Turkish/English, bounded pages, pixels, time and output.
Raw TSV retains page/line/crop provenance. No filename parsing or field invention.
"""
import csv
import io
import subprocess
import tempfile
from pathlib import Path

class RecognitionFailure(Exception):
    pass

class LocalOCR:
    version = 'local-tesseract-tur-eng-v1'
    provider = 'local'
    def recognize(self, content, mime):
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS = 25000000
        with tempfile.TemporaryDirectory(prefix='owner-ocr-') as td:
            root=Path(td); src=root/('source.pdf' if mime=='application/pdf' else 'source.image');src.write_bytes(content)
            engine=self._run(['tesseract','--version']).splitlines()[0]
            if 'tur' not in self._run(['tesseract','--list-langs']).splitlines():raise RecognitionFailure('turkish_model_unavailable')
            if mime=='application/pdf':
                info=self._run(['pdfinfo',str(src)])
                pages=next((int(l.split(':',1)[1]) for l in info.splitlines() if l.startswith('Pages:')),0)
                if not 1<=pages<=20:raise RecognitionFailure('pdf_page_limit')
                self._run(['pdftoppm','-scale-to','2400','-png',str(src),str(root/'page')],timeout=45)
                images=sorted(root.glob('page-*.png'),key=lambda p:int(p.stem.split('-')[-1]))
            else:
                try:
                    with Image.open(src) as im:
                        if im.width*im.height>25000000:raise RecognitionFailure('image_pixel_limit')
                        im=ImageOps.exif_transpose(im).convert('RGB');im.thumbnail((3000,3000));im.save(root/'page-1.png')
                except Exception as ex:raise RecognitionFailure('image_decode_failed') from ex
                images=[root/'page-1.png']
            result=[];warnings=[]
            for page,path in enumerate(images,1):
                # Test all four orientations. Unreadable recognition remains warnings;
                # this score chooses an OCR derivative, never authoritative numbers.
                candidates=[]
                with Image.open(path) as im:
                    for angle in (0,90,180,270):
                        derivative=root/'derivative.png';im.rotate(angle,expand=True).save(derivative)
                        tsv=self._run(['tesseract',str(derivative),'stdout','-l','tur+eng','--psm','6','tsv'],timeout=20)
                        words=[r for r in csv.DictReader(io.StringIO(tsv),delimiter='\t') if r.get('text','').strip() and r.get('conf','-1')!='-1']
                        score=sum(max(0,float(r['conf']))*len(r['text']) for r in words)
                        candidates.append((score,angle,tsv,words))
                _,angle,tsv,words=max(candidates,key=lambda c:c[0]);lines={}
                for w in words:
                    k=(w['block_num'],w['par_num'],w['line_num']);lines.setdefault(k,[]).append(w)
                spans=[dict(page=page,line=i+1,text=' '.join(w['text'] for w in ws),
                            bbox=[min(int(w['left']) for w in ws),min(int(w['top']) for w in ws),
                                  max(int(w['left'])+int(w['width']) for w in ws),max(int(w['top'])+int(w['height']) for w in ws)],
                            confidence=str(min(float(w['conf']) for w in ws))) for i,ws in enumerate(lines.values())]
                if not spans or any(float(s['confidence'])<65 for s in spans):warnings.append(f'page_{page}_needs_review')
                result.append(dict(page=page,rotation=angle,raw_tsv=tsv,lines=spans))
            return dict(provider=self.provider,engine=engine,version=self.version,pages=result,
                        text='\n'.join(s['text'] for p in result for s in p['lines']),warnings=warnings)
    def _run(self,args,timeout=10):
        try:
            r=subprocess.run(args,capture_output=True,text=True,timeout=timeout,check=True,
                             env=__import__('os').environ|{'OMP_THREAD_LIMIT':'1'})
            if len(r.stdout)>2000000:raise RecognitionFailure('ocr_output_limit')
            return r.stdout
        except (OSError,subprocess.SubprocessError) as ex:raise RecognitionFailure('recognition_failed') from ex
