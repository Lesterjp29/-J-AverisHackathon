"""Evidence overlays: show a reviewer WHERE on the page each value was read.

    pages = render_pages(name, data, scans)              # page image + word boxes (text PDFs, scans, photos)
    img   = annotate(page, items)                        # numbered, colour-coded boxes on the page
    html  = text_evidence_html(lines, items)             # same idea for txt / docx / xlsx documents

`items` are (field, verdict, evidence_line, value) tuples taken straight from a comparison result, so the picture
always agrees with the table beside it. Boxes are found by matching the evidence text against rows of words,
never by trusting page coordinates from the extractor, so it works the same for text PDFs, OCR'd scans and photos.
"""
from __future__ import annotations

import html as _html
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont
from rapidfuzz import fuzz

from .extract import FIELDS

COLORS = {"match": (34, 160, 90), "mismatch": (214, 40, 57), "missing": (224, 140, 0), "uncertain": (224, 140, 0)}
NUMBER = {f: i + 1 for i, f in enumerate(FIELDS)}
_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
          "C:/Windows/Fonts/arialbd.ttf", "/System/Library/Fonts/Helvetica.ttc"]


@dataclass
class Page:
    image: Image.Image
    rows: list[list[tuple]]          # visual rows of words: (text, x0, y0, x1, y1), left to right


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", (s or "").upper()).strip()


def _font(size: int):
    for f in _FONTS:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


# ------------------------------------------------------------------ words -> visual rows
def cluster_rows(words: list[tuple]) -> list[list[tuple]]:
    """Group word boxes into visual rows by vertical overlap, so 'Label ........ value' is one row even when a PDF
    stores the label and the value in different text blocks."""
    ws = sorted((w[:5] for w in words if w[0].strip()), key=lambda w: (w[2] + w[4]) / 2)
    if not ws:
        return []
    hs = sorted(w[4] - w[2] for w in ws)
    tol = 0.6 * hs[len(hs) // 2]
    rows: list[list[tuple]] = []
    cy: list[float] = []
    for w in ws:
        y = (w[2] + w[4]) / 2
        if rows and abs(y - cy[-1]) <= tol:
            rows[-1].append(w)
            cy[-1] = sum((x[2] + x[4]) / 2 for x in rows[-1]) / len(rows[-1])
        else:
            rows.append([w])
            cy.append(y)
    return [sorted(r, key=lambda w: w[1]) for r in rows]


# ------------------------------------------------------------------ pages
def _ocr_page(img: Image.Image) -> Page:
    from .scan import ocr_data
    _, words = ocr_data(img)
    return Page(img.convert("RGB"), cluster_rows(words))


def pdf_pages(data: bytes, dpi: int = 110, max_pages: int = 3) -> list[Page]:
    fd, pdf = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    tmp = tempfile.mkdtemp()
    try:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-f", "1", "-l", str(max_pages), "-png", pdf, os.path.join(tmp, "pg")],
                       check=True, capture_output=True, timeout=120)
        imgs = [Image.open(os.path.join(tmp, n)).convert("RGB") for n in sorted(os.listdir(tmp)) if n.endswith(".png")]
        r = subprocess.run(["pdftotext", "-bbox-layout", "-f", "1", "-l", str(max_pages), pdf, "-"],
                           capture_output=True, timeout=120)
        xml = r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else ""
    finally:
        os.unlink(pdf)
        for n in os.listdir(tmp):
            os.unlink(os.path.join(tmp, n))
        os.rmdir(tmp)
    pages = []
    xml_pages = xml.split("<page ")[1:]
    for i, img in enumerate(imgs):
        words = []
        if i < len(xml_pages):
            m = re.match(r'width="([\d.]+)" height="([\d.]+)"', xml_pages[i])
            sx = img.width / float(m.group(1)) if m else dpi / 72
            for x0, y0, x1, y1, t in re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>',
                                                xml_pages[i], re.S):
                words.append((_html.unescape(t), float(x0) * sx, float(y0) * sx, float(x1) * sx, float(y1) * sx))
        pages.append(Page(img, cluster_rows(words)) if words else _ocr_scanned_page(data, i + 1, img))
    return pages


def _ocr_scanned_page(data: bytes, page_no: int, display: Image.Image, hi_dpi: int = 300) -> Page:
    """Image-only PDF page: OCR needs ~300 dpi, but the picture we show is smaller, so scale the boxes down."""
    fd, pdf = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    tmp = tempfile.mkdtemp()
    try:
        subprocess.run(["pdftoppm", "-r", str(hi_dpi), "-f", str(page_no), "-l", str(page_no), "-png", "-singlefile",
                        pdf, os.path.join(tmp, "hi")], check=True, capture_output=True, timeout=120)
        hi = Image.open(os.path.join(tmp, "hi.png")).convert("RGB")
        hi.load()
    finally:
        os.unlink(pdf)
        for n in os.listdir(tmp):
            os.unlink(os.path.join(tmp, n))
        os.rmdir(tmp)
    from .scan import ocr_data
    _, words = ocr_data(hi)
    k = display.width / hi.width
    return Page(display, cluster_rows([(w[0], w[1] * k, w[2] * k, w[3] * k, w[4] * k) for w in words]))


def render_pages(name: str, data: bytes | None, scans: list | None = None, dpi: int = 110) -> list[Page] | None:
    """Pages with word boxes, or None for formats with no page geometry (txt / docx / xlsx)."""
    ext = name.lower().rsplit(".", 1)[-1]
    if scans:                                                      # photographed pages: boxes on what OCR actually saw
        out = []
        for r in scans:
            if r.words.get("A"):
                out.append(Page(r.variants["A"].convert("RGB"), cluster_rows(r.words["A"])))
        return out or None
    if data is None:
        return None
    if ext == "pdf":
        return pdf_pages(data, dpi)
    from .scan import IMAGE_EXTS, load_image
    if ext in IMAGE_EXTS:
        return [_ocr_page(load_image(data))]
    return None


# ------------------------------------------------------------------ locating a value
def find_box(rows: list[list[tuple]], evidence: str, value: str, min_row=78, min_val=68):
    """-> (box, matched_text) or None. First find the ROW that carries the evidence line (label + value, so two fields
    with the same value, e.g. consignee and notify, land on their own rows), then the words of the value inside it."""
    if not rows:
        return None
    texts = [_norm(" ".join(w[0] for w in r)) for r in rows]
    ev, val = _norm(evidence), _norm(value)
    best_i, best = -1, 0.0
    if ev:
        for i, t in enumerate(texts):
            sc = max(fuzz.ratio(ev, t), 0.97 * fuzz.partial_ratio(ev, t) if len(ev) >= 8 else 0)
            if sc > best:
                best_i, best = i, sc
    if best < min_row and val:                                     # wrapped value: the label sits on another row
        best_i, best = -1, 0.0
        for i, t in enumerate(texts):
            sc = fuzz.partial_ratio(val, t) if len(val) >= 4 else (100.0 if val and val in t.split() else 0)
            if sc > best:
                best_i, best = i, sc
        if best < 85:
            return None
    elif best < min_row:
        return None
    row = rows[best_i]
    toks = val.split()
    if not toks:
        return _union(row), " ".join(w[0] for w in row)
    n, top = len(toks), (None, 0.0)
    for L in {max(1, n - 1), n, n + 1}:
        for s in range(0, len(row) - L + 1):
            win = row[s:s + L]
            sc = fuzz.ratio(val, _norm(" ".join(w[0] for w in win)))
            if sc > top[1]:
                top = (win, sc)
    if top[0] is not None and top[1] >= min_val:
        return _union(top[0]), " ".join(w[0] for w in top[0])
    return _union(row), " ".join(w[0] for w in row)


def _union(ws: list[tuple]):
    return (min(w[1] for w in ws), min(w[2] for w in ws), max(w[3] for w in ws), max(w[4] for w in ws))


# ------------------------------------------------------------------ drawing
def annotate(page: Page, items: list[tuple]) -> Image.Image:
    """items: (field, verdict, evidence_line, value). Numbered boxes, colour = verdict."""
    base = page.image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    size = max(13, base.width // 70)
    font = _font(size)
    pad = max(2, base.width // 400)
    tags = []
    for field, verdict, evidence, value in items:
        if not (evidence or value):
            continue
        hit = find_box(page.rows, evidence or "", value or "")
        if not hit:
            continue
        x0, y0, x1, y1 = hit[0]
        c = COLORS.get(verdict, (90, 90, 90))
        box = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
        d.rectangle(box, fill=c + (46,), outline=c + (255,), width=max(2, base.width // 500))
        tags.append((box, c, str(NUMBER.get(field, "?"))))
    for (x0, y0, x1, y1), c, n in tags:
        w = size + 4
        tx, ty = x1 + 3, max(0, y0 - 1)                             # right of the box: usually blank, never hides a label
        if tx + w > base.width:
            tx = max(0, x0 - w)
        d.rounded_rectangle((tx, ty, tx + w, ty + size + 4), radius=4, fill=c + (255,))
        d.text((tx + 4, ty + 1), n, fill=(255, 255, 255, 255), font=font)
    return Image.alpha_composite(base, overlay).convert("RGB")


def text_evidence_html(lines: list[str], items: list[tuple]) -> str:
    """Highlighted text for documents with no page geometry."""
    marks: dict[int, list[tuple]] = {}
    for field, verdict, evidence, value in items:
        ev = _norm(evidence)
        if not ev:
            continue
        best_i, best = -1, 0.0
        for i, ln in enumerate(lines):
            sc = fuzz.ratio(ev, _norm(ln))
            if sc > best:
                best_i, best = i, sc
        if best >= 85:
            marks.setdefault(best_i, []).append((field, verdict))
    out = []
    for i, ln in enumerate(lines):
        text = _html.escape(ln) or "&nbsp;"
        if i in marks:
            r, g, b = COLORS.get(marks[i][0][1], (90, 90, 90))
            tag = " ".join(f"[{NUMBER.get(f, '?')}]" for f, _ in marks[i])
            text = (f'<span style="background:rgba({r},{g},{b},.22);border-left:4px solid rgb({r},{g},{b});'
                    f'padding:0 6px;display:block"><b>{tag}</b> {text}</span>')
        else:
            text = f'<span style="display:block;padding:0 10px">{text}</span>'
        out.append(text)
    return ('<div style="font-family:ui-monospace,Consolas,monospace;font-size:12.5px;line-height:1.55;'
            'white-space:pre-wrap;max-height:380px;overflow:auto;border:1px solid rgba(128,128,128,.3);'
            'border-radius:8px;padding:8px 0">' + "".join(out) + "</div>")


def items_from_fields(fields: list[dict], side: str) -> list[tuple]:
    """A comparison result's `fields` -> annotate() items for one side ('si' or 'bl')."""
    return [(f["field"], f["verdict"], f.get(f"{side}_evidence", ""), f.get(side) or "") for f in fields
            if f.get(side) is not None or f.get(f"{side}_evidence")]
