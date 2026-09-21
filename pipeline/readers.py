"""Attachment readers: bytes -> Doc (text lines + document kind + read quality).

One reader per format, all returning the same Doc shape so extraction and
comparison never care where the text came from.
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field

CJK_PAREN = re.compile(r"\([^)]*[\u3000-\u9fff\uff00-\uffef][^)]*\)")   # "(发货人)" "(毛重 KGS)" dropped whole
CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef\u25a0\u25a1\ufffd]+")      # stray CJK / missing-glyph boxes


@dataclass
class Doc:
    path: str
    fmt: str                      # txt | pdf | pdf_scan | docx | xlsx | unknown
    lines: list[str] = field(default_factory=list)   # "Label: value" / continuation lines
    passes: list[list[str]] = field(default_factory=list)  # extra OCR passes (scans only)
    error: str | None = None      # set when the file cannot be read at all
    ocr: bool = False
    meta: dict = field(default_factory=dict)   # e.g. {"scans": [ScanResult]} for photographed pages

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


# ---------------------------------------------------------------- helpers
def _tmp(data: bytes, suffix: str) -> str:
    fd, p = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return p


def _pdftotext(data: bytes) -> str | None:
    p = _tmp(data, ".pdf")
    try:
        r = subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, timeout=60)
        if r.returncode != 0:
            return None
        return r.stdout.decode("utf-8", errors="replace")
    except FileNotFoundError:
        raise RuntimeError("poppler 'pdftotext' is not installed / not on PATH")
    except Exception:
        return None
    finally:
        os.unlink(p)


def _ocr_pdf(data: bytes, dpis=(300, 400)) -> list[list[str]]:
    """Rasterize page 1..n at several DPIs and OCR each. Returns one line-list per pass."""
    import pytesseract
    from PIL import Image

    p = _tmp(data, ".pdf")
    passes: list[list[str]] = []
    try:
        for dpi in dpis:
            d = tempfile.mkdtemp()
            subprocess.run(["pdftoppm", "-r", str(dpi), "-png", p, os.path.join(d, "pg")],
                           check=True, capture_output=True, timeout=120)
            lines: list[str] = []
            for fn in sorted(os.listdir(d)):
                im = Image.open(os.path.join(d, fn)).convert("L")
                txt = pytesseract.image_to_string(im, config="--psm 6")
                lines += [l.rstrip() for l in txt.split("\n") if l.strip()]
                os.unlink(os.path.join(d, fn))
            os.rmdir(d)
            passes.append(lines)
    finally:
        os.unlink(p)
    return passes


# ---------------------------------------------------------------- per-format
def read_txt(path: str, data: bytes) -> Doc:
    text = data.decode("utf-8", errors="replace")
    return Doc(path, "txt", lines=text.splitlines())


def read_pdf(path: str, data: bytes) -> Doc:
    if not data.startswith(b"%PDF"):
        return Doc(path, "pdf", error="not a PDF (bad header)")
    try:
        txt = _pdftotext(data)
    except RuntimeError as e:                        # environment problem, NOT a bad file
        return Doc(path, "pdf", error=f"TOOL MISSING: {e}")
    if txt is None:
        return Doc(path, "pdf", error="PDF could not be parsed (corrupt or truncated)")
    if len(re.sub(r"\s", "", txt)) < 40:            # image-only page -> OCR
        try:
            passes = _ocr_pdf(data)
        except Exception as e:                      # OCR engine failure is visible, not silent
            return Doc(path, "pdf_scan", error=f"OCR failed: {e}", ocr=True)
        if not any(len(" ".join(p)) > 40 for p in passes):
            return Doc(path, "pdf_scan", error="scan produced no readable text", ocr=True)
        return Doc(path, "pdf_scan", lines=passes[0], passes=passes, ocr=True)
    return Doc(path, "pdf", lines=txt.splitlines())


def read_docx(path: str, data: bytes) -> Doc:
    try:
        import docx
        d = docx.Document(io.BytesIO(data))
    except Exception as e:
        return Doc(path, "docx", error=f"Word file could not be opened: {e}")
    lines: list[str] = []
    for p in d.paragraphs:
        if p.text.strip():
            lines.append(p.text)
    for t in d.tables:
        for row in t.rows:
            cells = [c.text for c in row.cells]
            if len(cells) >= 2:
                label, value = cells[0].strip(), cells[1]
                vl = value.split("\n")
                lines.append(f"{label}: {vl[0]}")
                lines += ["   " + x for x in vl[1:]]
    return Doc(path, "docx", lines=lines)


def read_xlsx(path: str, data: bytes) -> Doc:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception as e:
        return Doc(path, "xlsx", error=f"Excel file could not be opened: {e}")
    lines: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            vals = [("" if v is None else str(v)).strip() for v in row]
            vals = [v for v in vals if v != ""]
            if not vals:
                continue
            if len(vals) == 1:
                lines.append(vals[0])
                continue
            label, value = vals[0], vals[1]
            # "NAME | address; address" -> name line + address lines
            parts = [x.strip() for x in re.split(r"\s\|\s|;\s", value) if x.strip()]
            lines.append(f"{label}: {parts[0] if parts else ''}")
            lines += ["   " + x for x in parts[1:]]
    return Doc(path, "xlsx", lines=lines)


def read_image(path: str, data: bytes) -> Doc:
    """A photo or image of a page. Quality is judged first: an unusable photo is 'unreadable' with a plain reason,
    rather than being OCR'd into confident nonsense."""
    from . import scan
    try:
        res = scan.process_photo(data)
    except Exception as e:
        return Doc(path, "photo", error=f"image could not be processed: {e}", ocr=True)
    doc = scan.photos_to_doc(path, [res])
    doc.meta["scans"] = [res]
    if doc.error is None and not doc.lines and res.quality.retake:
        doc.error = "photo quality too low: " + res.quality.issues[0][1]
    elif doc.error and res.quality.retake:
        doc.error = "photo quality too low: " + res.quality.issues[0][1]
    return doc


def read_any(path: str, data: bytes) -> Doc:
    ext = path.lower().rsplit(".", 1)[-1]
    try:
        from .scan import IMAGE_EXTS
        if ext in IMAGE_EXTS:
            return read_image(path, data)
        if ext == "txt":
            return read_txt(path, data)
        if ext == "pdf":
            return read_pdf(path, data)
        if ext == "docx":
            return read_docx(path, data)
        if ext in ("xlsx", "xlsm"):
            return read_xlsx(path, data)
    except Exception as e:                           # never crash the batch on one file
        return Doc(path, ext, error=f"reader crashed: {e}")
    return Doc(path, ext, error=f"unsupported attachment type .{ext}")


# ---------------------------------------------------------------- doc kind
def strip_cjk(s: str) -> str:
    return CJK.sub("", CJK_PAREN.sub("", s))


def detect_kind(doc: Doc) -> str:
    """SI | BL | OTHER:<name> | UNKNOWN — decided by CONTENT (title), never by filename."""
    head = " ".join(doc.lines[:8]).upper()
    h = re.sub(r"[^A-Z/ ]", " ", head)
    hs = re.sub(r"\s+", "", h)                       # OCR often drops spaces ("BILLOF LADING")
    if re.search(r"SHIPPINGINSTRUCTION|BLINSTRUCTION|B/LINSTRUCTION|BILLOFLADINGINSTRUCTION", hs):
        return "SI"
    if re.search(r"PACKINGLIST", hs):
        return "OTHER:packing_list"
    if re.search(r"CERTIFICATEOFORIGIN", hs):
        return "OTHER:certificate_of_origin"
    if re.search(r"COMMERCIALINVOICE", hs):
        return "OTHER:commercial_invoice"
    if re.search(r"BILLOFLADING|^B/L|\bBL\b", hs):
        return "BL"
    return "UNKNOWN"
