"""Frozen Capture Daily Completion synthetic source assets.

This module renders the pre-labelled input documents, never parser output. It
imports no application code, OCR engine or database. PDF pages contain raster
images only. Canonical benchmark labels and asset hashes must not be revised to
fit recognition results. These are printed synthetic fixtures, not camera data.

API: ``document('H01') -> (bytes, MIME)``; ``asset_path('H01')`` returns the
committed original; ``write_assets(directory)`` deterministically regenerates all
originals, page previews and a hash manifest. CLI: ``python -m
tests.capture_daily_corpus --output tests/fixtures/capture_daily_assets``.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont, __version__ as PIL_VERSION


FIXTURES = Path(__file__).parent / "fixtures"
BENCHMARK = FIXTURES / "capture_daily_benchmark.json"
ASSETS = FIXTURES / "capture_daily_assets"
WIDTH, HEIGHT = 1600, 1000
FONT_SIZE = 34
FONT_NAME = "DejaVuSans.ttf"
PDF_DPI = 144
FIXED_TIME = time.struct_time((2026, 10, 12, 6, 7, 20, 0, 285, 0))
ASSET_CASE_IDS = tuple(f"H{n:02}" for n in range(1, 9)) + tuple(
    f"P{n:02}" for n in range(1, 6)
)
PDF_CASE_IDS = frozenset({"H02", "P02", "P05"})


def benchmark() -> dict:
    return json.loads(BENCHMARK.read_text(encoding="utf-8"))


def case(case_id: str) -> dict:
    try:
        return next(row for row in benchmark()["cases"] if row["id"] == case_id)
    except StopIteration as exc:
        raise ValueError(f"Unknown frozen benchmark case {case_id!r}") from exc


def _font() -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_NAME, FONT_SIZE)


def _number(value: str, places: int) -> str:
    """Only formatting; never derive or repair any amount from other fields."""
    text = f"{Decimal(value):,.{places}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _blank() -> tuple[Image.Image, ImageDraw.ImageDraw, ImageFont.FreeTypeFont]:
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    return image, ImageDraw.Draw(image), _font()


def _text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str,
          font: ImageFont.FreeTypeFont, *, right: bool = False) -> None:
    x, y = xy
    if right:
        x -= int(draw.textlength(value, font=font))
    box = draw.textbbox((x, y), value, font=font)
    if box[0] < 30 or box[2] > WIDTH - 30 or box[3] > HEIGHT - 30:
        raise AssertionError(f"Fixture text outside fixed page: {value!r} {box}")
    draw.text((x, y), value, font=font, fill="black")


def _depot_page(doc: dict, indexes: list[int], *, totals: bool,
                page: int = 1, pages: int = 1) -> tuple[Image.Image, list[tuple[int, int, int, int]]]:
    image, draw, font = _blank()
    masked_regions: list[tuple[int, int, int, int]] = []
    _text(draw, (70, 55), doc["title"], font)
    _text(draw, (70, 125), "SENTETİK TEST BELGESİ", font)
    _text(draw, (70, 200), f"Tarih: {doc['date']}", font)
    _text(draw, (800, 200), f"Fiş No: {doc['document_number']}", font)
    masked_regions.append((60, 195, 1540, 255))
    for x, label, right in ((70, "Kalite", False), (630, "Net KG", True),
                            (1090, "Birim Fiyat TRY", True), (1510, "Tutar TRY", True)):
        _text(draw, (x, 300), label, font, right=right)
    draw.line((70, 360, 1510, 360), fill="black", width=2)
    for row_index, doc_index in enumerate(indexes):
        line = doc["lines"][doc_index]
        y = 390 + row_index * 80
        _text(draw, (70, y), line["label"], font)
        _text(draw, (630, y), _number(line["kg"], 3), font, right=True)
        _text(draw, (1090, y), _number(line["unit_price"], 2), font, right=True)
        _text(draw, (1510, y), _number(line["amount"], 2), font, right=True)
        masked_regions.append((60, y - 5, 1540, y + 55))
    if totals:
        draw.line((70, 665, 1510, 665), fill="black", width=2)
        _text(draw, (70, 700), "TOPLAM", font)
        _text(draw, (630, 700), f"{_number(doc['total_kg'], 3)} KG", font, right=True)
        _text(draw, (1510, 700), f"{_number(doc['total_amount'], 2)} TRY", font, right=True)
        masked_regions.append((60, 695, 1540, 755))
        if doc.get("adet") is not None:
            _text(draw, (70, 780), f"ADET: {doc['adet']}", font)
            masked_regions.append((60, 775, 650, 835))
        _text(draw, (1510, 780), f"ALACAK: {_number(doc['alacak'], 2)} TRY", font, right=True)
        masked_regions.append((660, 775, 1540, 835))
    _text(draw, (70, 910), "Bu belge gerçek bir ticari işlem değildir.", font)
    if pages > 1:
        _text(draw, (1510, 910), f"Sayfa {page}/{pages}", font, right=True)
    return image, masked_regions


def _invoice_page(doc: dict) -> Image.Image:
    image, draw, font = _blank()
    _text(draw, (70, 55), doc["title"], font)
    _text(draw, (70, 125), "SENTETİK TEST BELGESİ", font)
    _text(draw, (70, 205), f"Satıcı: {doc['supplier']}", font)
    _text(draw, (70, 280), f"Tarih: {doc['date']}", font)
    _text(draw, (800, 280), f"Fatura No: {doc['number']}", font)
    for x, label, right in ((70, "Ürün", False), (680, "Miktar", True),
                            (1110, "Birim Fiyat TRY", True), (1510, "Tutar TRY", True)):
        _text(draw, (x, 385), label, font, right=right)
    draw.line((70, 445, 1510, 445), fill="black", width=2)
    for row_index, line in enumerate(doc["lines"]):
        y = 475 + 80 * row_index
        _text(draw, (70, y), line["product"], font)
        places = 3 if line["unit"] == "kg" else 0
        _text(draw, (680, y), f"{_number(line['quantity'], places)} {line['unit']}", font, right=True)
        _text(draw, (1110, y), _number(line["unit_price"], 2), font, right=True)
        _text(draw, (1510, y), _number(line["amount"], 2), font, right=True)
    draw.line((70, 590, 1510, 590), fill="black", width=2)
    _text(draw, (70, 625), "TOPLAM", font)
    _text(draw, (1510, 625), f"{_number(doc['total'], 2)} {doc['currency']}", font, right=True)
    if doc.get("payment_stamp"):
        _text(draw, (70, 730), doc["payment_stamp"], font)
    _text(draw, (70, 910), "Bu belge gerçek bir ticari işlem değildir.", font)
    return image


def rendered_pages(case_id: str) -> list[Image.Image]:
    """Render independent source pixels without consulting expected extraction."""
    if case_id not in ASSET_CASE_IDS:
        raise ValueError(f"Case {case_id!r} is text or a reliability sequence, not a document")
    doc = case(case_id)["input"]["document"]
    if case_id.startswith("P"):
        return [_invoice_page(doc)]
    if case_id == "H02":
        return [_depot_page(doc, [0], totals=False, page=1, pages=2)[0],
                _depot_page(doc, [1, 2], totals=True, page=2, pages=2)[0]]
    image, regions = _depot_page(doc, list(range(len(doc["lines"]))), totals=True)
    if case_id == "H03":
        image = image.transpose(Image.Transpose.ROTATE_270)
    elif case_id == "H04":
        image = image.convert("L").filter(ImageFilter.GaussianBlur(2.0)).convert("RGB")
    elif case_id == "H05":
        draw = ImageDraw.Draw(image)
        for region in regions:
            draw.rectangle(region, fill="white")
    return [image]


def document(case_id: str) -> tuple[bytes, str]:
    """Return deterministic real PNG/PDF bytes for a frozen input case."""
    pages = rendered_pages(case_id)
    stream = io.BytesIO()
    if case_id in PDF_CASE_IDS:
        pages[0].save(stream, format="PDF", save_all=True, append_images=pages[1:],
                      resolution=PDF_DPI, quality=100, subsampling=0,
                      title=f"Capture Daily Completion {case_id}",
                      author="Synthetic TEST fixture", creator="capture_daily_corpus.py",
                      creationDate=FIXED_TIME, modDate=FIXED_TIME)
        return stream.getvalue(), "application/pdf"
    pages[0].save(stream, format="PNG", optimize=False)
    return stream.getvalue(), "image/png"


def asset_path(case_id: str, directory: Path = ASSETS) -> Path:
    if case_id not in ASSET_CASE_IDS:
        raise ValueError(f"No binary document for {case_id!r}")
    return directory / f"{case_id}.{'pdf' if case_id in PDF_CASE_IDS else 'png'}"


def write_assets(directory: Path = ASSETS) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    font_path = Path(_font().path)
    entries = []
    for case_id in ASSET_CASE_IDS:
        data, mime = document(case_id)
        path = asset_path(case_id, directory)
        path.write_bytes(data)
        pages = rendered_pages(case_id)
        previews = []
        if case_id in PDF_CASE_IDS:
            for n, page in enumerate(pages, 1):
                preview = directory / f"{case_id}.source-page-{n}.png"
                page.save(preview, format="PNG", optimize=False)
                previews.append({"filename": preview.name,
                                 "sha256": hashlib.sha256(preview.read_bytes()).hexdigest()})
        entries.append({"case_id": case_id, "filename": path.name, "mime_type": mime,
                        "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                        "page_count": len(pages), "pixel_sizes": [list(im.size) for im in pages],
                        "source_page_previews": previews})
    result = {"format_version": 1, "recipe_version": "cdc-delta-v1-render-v1",
              "benchmark_sha256": hashlib.sha256(BENCHMARK.read_bytes()).hexdigest(),
              "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "pillow_version": PIL_VERSION, "font_filename": font_path.name,
              "font_sha256": hashlib.sha256(font_path.read_bytes()).hexdigest(),
              "canvas": [WIDTH, HEIGHT], "font_size_px": FONT_SIZE,
              "pdf_dpi": PDF_DPI, "pdf_has_text_layer": False,
              "source_fact_policy": "Frozen input fields only; caption and field count never burned into depot document.",
              "recognition_run": False, "entries": entries}
    (directory / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ASSETS)
    args = parser.parse_args()
    result = write_assets(args.output)
    print(json.dumps({"output": str(args.output), "documents": len(result["entries"]),
                      "benchmark_sha256": result["benchmark_sha256"]}, indent=2))
