"""Local, bounded OCR. Providers return evidence and have no domain authority."""
from __future__ import annotations

import csv
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader


class RecognitionError(Exception):
    """Safe error category; never contains source text or command output."""


class OCRProvider(Protocol):
    version: str
    def recognize(self, original: bytes, mime_type: str) -> dict: ...


class LocalOCR:
    version = 'tesseract-tur-eng-v1'
    max_bytes = 20 * 1024 * 1024
    max_pages = 20
    max_pixels = 30_000_000

    def __init__(self, *, timeout=30, executable='tesseract', pdf_executable='pdftoppm', tessdata=None):
        self.timeout = timeout
        self.executable = executable
        self.pdf_executable = pdf_executable
        self.tessdata = tessdata

    def _run(self, args):
        env = dict(os.environ, OMP_THREAD_LIMIT='1')
        if self.tessdata:
            env['TESSDATA_PREFIX'] = self.tessdata
        try:
            result = subprocess.run(args, capture_output=True, timeout=self.timeout, env=env)
        except subprocess.TimeoutExpired:
            raise RecognitionError('OCR_TIMEOUT') from None
        except OSError:
            raise RecognitionError('OCR_ENGINE_UNAVAILABLE') from None
        if result.returncode:
            raise RecognitionError('OCR_ENGINE_FAILED')
        return result.stdout.decode('utf-8', errors='replace')

    def recognize(self, original: bytes, mime_type: str) -> dict:
        if not original or len(original) > self.max_bytes:
            raise RecognitionError('OCR_SIZE_LIMIT')
        signatures = {'application/pdf': b'%PDF-', 'image/png': b'\x89PNG\r\n\x1a\n', 'image/jpeg': b'\xff\xd8\xff'}
        if mime_type not in signatures or not original.startswith(signatures[mime_type]):
            raise RecognitionError('OCR_UNSUPPORTED_FILE')
        languages = self._run([self.executable, '--list-langs'])
        if not all(lang in languages.splitlines() for lang in ('tur', 'eng')):
            raise RecognitionError('OCR_TURKISH_LANGUAGE_UNAVAILABLE')
        engine = self._run([self.executable, '--version']).splitlines()[0]
        with tempfile.TemporaryDirectory(prefix='owner-ocr-') as directory:
            root = Path(directory)
            if mime_type == 'application/pdf':
                try:
                    reader = PdfReader(io.BytesIO(original))
                    if reader.is_encrypted or not 1 <= len(reader.pages) <= self.max_pages:
                        raise RecognitionError('OCR_PDF_PAGE_LIMIT_OR_ENCRYPTED')
                except RecognitionError:
                    raise
                except Exception:
                    raise RecognitionError('OCR_INVALID_PDF') from None
                source = root / 'original.pdf'
                source.write_bytes(original)
                self._run([self.pdf_executable, '-r', '160', '-scale-to', '4000', '-png', str(source), str(root / 'page')])
                pages = sorted(root.glob('page-*.png'), key=lambda p: int(p.stem.split('-')[-1]))
            else:
                source = root / 'original'
                source.write_bytes(original)
                pages = [source]
            output = []
            warnings = []
            for number, page in enumerate(pages, 1):
                try:
                    with Image.open(page) as image:
                        if image.width * image.height > self.max_pixels:
                            raise RecognitionError('OCR_PIXEL_LIMIT')
                        image = ImageOps.exif_transpose(image).convert('RGB')
                        image.thumbnail((4000, 4000))
                        # OSD is evidence about orientation, not a text fixture.
                        derivative = root / f'input-{number}.png'
                        image.save(derivative)
                        try:
                            osd = self._run([self.executable, str(derivative), 'stdout', '--psm', '0'])
                            import re
                            rotation = re.search(r'Rotate: (90|180|270)', osd)
                            if rotation:
                                image = image.rotate(-int(rotation[1]), expand=True)
                                image.save(derivative)
                        except RecognitionError:
                            warnings.append(f'page:{number}:orientation_unverified')
                        raw = self._run([self.executable, str(derivative), 'stdout', '-l', 'tur+eng', '--psm', '6', '-c', 'tessedit_create_tsv=1'])
                except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
                    raise RecognitionError('OCR_INVALID_IMAGE') from None
                lines = {}
                for word in csv.DictReader(io.StringIO(raw), delimiter='\t'):
                    if not word.get('text', '').strip():
                        continue
                    key = (word['block_num'], word['par_num'], word['line_num'])
                    lines.setdefault(key, []).append(word)
                spans = []
                for ordinal, words in enumerate(lines.values(), 1):
                    confidence = min(float(w['conf']) for w in words)
                    spans.append(dict(page=number, line=ordinal, text=' '.join(w['text'] for w in words),
                                      confidence=format(confidence, '.2f'),
                                      box=[min(int(w['left']) for w in words), min(int(w['top']) for w in words),
                                           max(int(w['left']) + int(w['width']) for w in words),
                                           max(int(w['top']) + int(w['height']) for w in words)]))
                    if confidence < 70:
                        warnings.append(f'page:{number}:line:{ordinal}:low_confidence')
                output.append(dict(page=number, raw_tsv=raw, spans=spans))
            if not any(page['spans'] for page in output):
                warnings.append('no_readable_text')
            return dict(provider='local', engine=engine, version=self.version, languages=['tur', 'eng'],
                        input_hash=hashlib.sha256(original).hexdigest(), pages=output, warnings=warnings,
                        text='\n\n'.join('\n'.join(s['text'] for s in p['spans']) for p in output))
