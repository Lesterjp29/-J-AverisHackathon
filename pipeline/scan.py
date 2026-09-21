"""Phone-photo intake: load -> judge quality -> find the page -> flatten it -> straighten it -> clean it -> OCR it.

Photos are far messier than the synthetic scans in the sample data (perspective, shadows, blur, glare, sideways
pages), so this module does three jobs:

  1. Tell the user IMMEDIATELY when a photo is not good enough ("retake: too blurry"), instead of letting bad OCR
     turn into false mismatches later.
  2. Turn a photo into two clean OCR inputs (a lighting-normalised grey page and the same page at another scale) so the existing
     "two OCR passes must agree" reliability rule works for photos exactly as it does for scans.
  3. Keep word boxes so the UI can show WHERE each value was read.

OpenCV is optional: without it the page is only auto-contrasted (no perspective fix or deskew) and the result says so.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageOps

try:                                                   # optional, big accuracy gain
    import cv2
except ImportError:                                    # pragma: no cover
    cv2 = None
try:                                                   # iPhones save HEIC
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:                                      # pragma: no cover
    pass

MAX_SIDE = 4200            # phone photos can be 12-48 MP; more than this only slows OCR down
TARGET_LONG = 2600         # OCR works best around 300 dpi: an A4 page ~ 2500 px on its long side
IMAGE_EXTS = ("png", "jpg", "jpeg", "webp", "heic", "heif", "tif", "tiff", "bmp")


@dataclass
class Quality:
    width: int = 0
    height: int = 0
    page_long_px: int = 0
    sharpness: float = 0.0          # 99th percentile edge strength of the flattened page (0..1)
    brightness: float = 0.0
    contrast: float = 0.0
    glare_pct: float = 0.0
    page_found: bool = False
    page_fill: float = 0.0          # share of the frame the page occupies
    skew_deg: float = 0.0
    rotated_deg: int = 0
    notes: list[str] = field(default_factory=list)          # what the pipeline did
    issues: list[tuple[str, str]] = field(default_factory=list)   # (level, message): level = retake | warn

    @property
    def retake(self) -> bool:
        return any(l == "retake" for l, _ in self.issues)


@dataclass
class ScanResult:
    original: Image.Image
    page: Image.Image                # flattened colour page, for display
    variants: dict                   # {"A": normalised grey, "B": same page at 0.8x}, used for OCR; evidence boxes use A
    quality: Quality
    quad: np.ndarray | None          # detected page corners in ORIGINAL pixel coordinates
    lines: dict = field(default_factory=dict)     # {"A": [...], "B": [...]}
    words: dict = field(default_factory=dict)     # {"A": [(text, x0, y0, x1, y1, conf, line_id)], "B": [...]}


# ------------------------------------------------------------------ loading
def load_image(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)                 # phones store rotation in EXIF
    img = img.convert("RGB")
    if max(img.size) > MAX_SIDE:
        k = MAX_SIDE / max(img.size)
        img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
    return img


def _bgr(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)


def _pil(bgr: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


# ------------------------------------------------------------------ geometry
def order_points(pts) -> np.ndarray:
    pts = np.array(pts, dtype="float32").reshape(4, 2)
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype="float32")


def find_page(bgr: np.ndarray):
    """-> (quad in original coordinates | None, fill ratio). Two independent detectors (brightness and edges);
    the candidate that best fits a quadrilateral wins. Returns None when the page fills the frame anyway."""
    h, w = bgr.shape[:2]
    scale = 800.0 / max(h, w)
    small = cv2.resize(bgr, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    frame = small.shape[0] * small.shape[1]
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    edges = cv2.dilate(cv2.Canny(gray, 40, 120), np.ones((3, 3), np.uint8), iterations=2)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    best, best_score = None, 0.0
    for mask in (bright, edges):
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:4]:
            area = cv2.contourArea(c)
            if area < 0.02 * frame:
                continue
            hull = cv2.convexHull(c)
            approx = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
            quad = approx.reshape(4, 2) if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(hull))
            qarea = cv2.contourArea(order_points(quad))
            if qarea <= 0:
                continue
            fit = min(area, qarea) / max(area, qarea)             # how quadrilateral-like the blob is
            score = qarea * fit * fit
            if fit > 0.8 and score > best_score:
                best, best_score = order_points(quad), score
    if best is None:
        return None, 0.0
    fill = float(cv2.contourArea(best) / frame)
    edge = [np.linalg.norm(best[i] - best[(i + 1) % 4]) for i in range(4)]
    if min(edge) < 60 or max(edge) / max(min(edge), 1) > 6:
        return None, fill
    if fill > 0.97:                                               # page IS the frame: nothing to flatten
        return None, fill
    return best / scale, fill


def warp(bgr: np.ndarray, quad: np.ndarray) -> np.ndarray:
    tl, tr, br, bl = quad
    W = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    H = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype="float32")
    out = cv2.warpPerspective(bgr, cv2.getPerspectiveTransform(quad, dst), (W, H),
                              flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    m = max(2, int(0.008 * min(W, H)))                            # drop the page edge / desk fringe
    return out[m:H - m, m:W - m]


def _rotate(arr: np.ndarray, angle: float, expand=False, fill=None) -> np.ndarray:
    h, w = arr.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    if expand:
        c, s = abs(M[0, 0]), abs(M[0, 1])
        nw, nh = int(h * s + w * c), int(h * c + w * s)
        M[0, 2] += nw / 2 - w / 2
        M[1, 2] += nh / 2 - h / 2
        w, h = nw, nh
    if fill is None:
        fill = 255 if arr.ndim == 2 else (255, 255, 255)
    return cv2.warpAffine(arr, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=fill)


def flatten(gray: np.ndarray) -> np.ndarray:
    """Divide out slowly varying light and shadows so ink is dark and paper is uniformly bright."""
    h, w = gray.shape
    sw = 480                                                         # light varies slowly: estimate it small, scale back
    small = cv2.resize(gray, (sw, max(1, int(h * sw / w))), interpolation=cv2.INTER_AREA)
    small = cv2.morphologyEx(small, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))   # erase ink so it is not part of "light"
    small = cv2.GaussianBlur(small, (0, 0), sigmaX=sw / 25)
    bg = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.divide(gray, np.maximum(bg, 1), scale=255)


def estimate_skew(gray: np.ndarray) -> float:
    """Angle (degrees, +ve = counter-clockwise) that makes text lines horizontal. Projection-profile search on the
    lighting-flattened page. Returns 0 unless one angle is clearly better than 'no rotation', so an ambiguous page
    is never rotated on a hunch."""
    h, w = gray.shape
    small = cv2.resize(gray, (900, max(1, int(h * 900.0 / w))), interpolation=cv2.INTER_AREA)
    _, bw = cv2.threshold(flatten(small), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if not 0.2 < bw.mean() / 255 * 100 < 40:                      # ink should be a few % of the page
        return 0.0

    def score(a: float) -> float:
        r = _rotate(bw, a, fill=0)                                # pad with BACKGROUND (ink is white here)
        return float(np.var(r.sum(axis=1).astype("float64")))

    coarse = max(np.arange(-15, 15.1, 1.0), key=score)
    fine = max(np.arange(coarse - 1, coarse + 1.01, 0.2), key=score)
    if abs(fine) < 0.3 or score(fine) < 1.06 * score(0.0):
        return 0.0
    return round(float(fine), 2)


def detect_rotation(gray: np.ndarray) -> int:
    """0/90/180/270 needed to make the page upright. Tesseract OSD first; OCR-confidence voting as fallback."""
    import pytesseract
    small = Image.fromarray(gray)
    if small.width > 1400:
        small = small.resize((1400, int(small.height * 1400 / small.width)))
    try:
        d = pytesseract.image_to_osd(small, config="--psm 0", output_type=pytesseract.Output.DICT)
        if float(d.get("orientation_conf", 0)) >= 1.5:
            return int(d["rotate"]) % 360
    except Exception:
        pass

    def conf(img: Image.Image) -> float:
        data = pytesseract.image_to_data(img, config="--psm 6", output_type=pytesseract.Output.DICT)
        c = [float(x) for x, t in zip(data["conf"], data["text"]) if t.strip() and float(x) >= 0]
        return sum(c) / len(c) if c else 0.0

    tiny = small.resize((900, int(small.height * 900 / small.width))) if small.width > 900 else small
    scores = {r: conf(tiny.rotate(-r, expand=True)) for r in (0, 90, 180, 270)}
    best = max(scores, key=scores.get)
    return best if scores[best] - scores[0] >= 12 else 0


# ------------------------------------------------------------------ cleanup
def enhance(gray: np.ndarray) -> dict:
    """Two OCR inputs. A = lighting-normalised grey at ~300 dpi. B = the same page at 0.8x scale.
    Tested against alternatives (binarised, CLAHE, 1.3x): a second SCALE of the same clean page makes independent
    errors but agrees on the truth, so 'both passes agree' is a real reliability signal (34/35 fields agreed & correct
    vs 29-33 for the others), and it is the fastest."""
    h, w = gray.shape
    k = TARGET_LONG / max(h, w)
    if k > 1.0 or k < 0.75:
        gray = cv2.resize(gray, (int(w * k), int(h * k)), interpolation=cv2.INTER_CUBIC if k > 1 else cv2.INTER_AREA)
    h, w = gray.shape
    norm = flatten(gray)
    m = max(3, int(0.012 * min(h, w)))                               # page edges / desk fringe become black bars in OCR
    norm[:m, :] = 255
    norm[-m:, :] = 255
    norm[:, :m] = 255
    norm[:, -m:] = 255
    small = cv2.resize(norm, (int(w * 0.8), int(h * 0.8)), interpolation=cv2.INTER_AREA)
    return {"A": Image.fromarray(norm), "B": Image.fromarray(small)}


# ------------------------------------------------------------------ quality
def _sharpness(gray: np.ndarray) -> float:
    k = 1400.0 / gray.shape[1]
    g = cv2.resize(gray, (1400, max(1, int(gray.shape[0] * k))), interpolation=cv2.INTER_AREA).astype("float32") / 255
    mag = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    return float(np.percentile(mag, 99.5))


def _glare(gray: np.ndarray) -> float:
    """Largest compact blown-out blob, as a share of the page. Ignored on all-white (digital) pages."""
    sat = (gray >= 252).astype("uint8")
    if sat.mean() > 0.6:
        return 0.0
    n, _, stats, _ = cv2.connectedComponentsWithStats(cv2.morphologyEx(sat, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)))
    return float(stats[1:, cv2.CC_STAT_AREA].max() / sat.size) if n > 1 else 0.0


def judge(q: Quality) -> None:
    add = q.issues.append
    if q.page_long_px and q.page_long_px < 900:
        add(("retake", f"Page is only {q.page_long_px}px tall in the photo - move closer or use a higher-resolution photo."))
    elif q.page_long_px and q.page_long_px < 1500:
        add(("warn", "Low resolution: small print may be misread. Move closer if you can."))
    if q.sharpness < 0.75:
        add(("retake", "Photo is blurry - hold the phone steady, tap to focus, and retake."))
    elif q.sharpness < 1.1:
        add(("warn", "Slightly soft focus - values may need checking."))
    if q.brightness < 90 and (q.page_found or q.page_fill > 0):
        add(("retake", "Too dark - turn on a light or move to a brighter spot."))
    if q.glare_pct > 0.02:
        add(("retake" if q.glare_pct > 0.08 else "warn", "Glare / reflection on the page - tilt the page or turn off the flash."))
    if q.contrast < 60:
        add(("warn", "Low contrast: the text may be faint."))
    if q.page_found and q.page_fill < 0.30:
        add(("retake", "The page fills only a small part of the photo - move closer so it fills the frame."))
    if not q.page_found and q.page_fill == 0:
        add(("warn", "Could not find the page edges - photograph the whole page filling the frame, on a plain surface. "
                     "Results may need checking."))


# ------------------------------------------------------------------ OCR
def ocr_data(img: Image.Image, psm: int = 6):
    """-> (lines, words). Lines follow Tesseract's own segmentation; words carry pixel boxes for evidence overlays."""
    import pytesseract
    d = pytesseract.image_to_data(img, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
    rows: dict = {}
    words = []
    for i, t in enumerate(d["text"]):
        t = t.strip()
        if not t:
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        rows.setdefault(key, []).append((d["left"][i], t))
        words.append((t, d["left"][i], d["top"][i], d["left"][i] + d["width"][i], d["top"][i] + d["height"][i],
                      float(d["conf"][i]), key))
    lines = []
    for _, v in sorted(rows.items()):
        line = " ".join(t for _, t in sorted(v))
        line = re.sub(r"^[\s|!\u00a6_:;.\[\]{}]+|[\s|!\u00a6_\[\]{}]+$", "", line)   # stray edge marks glued on by OCR
        if line:
            lines.append(line)
    return lines, words


# ------------------------------------------------------------------ orchestration
def process_photo(data: bytes, ocr: bool = True, force: bool = False) -> ScanResult:
    img = load_image(data)
    q = Quality(width=img.width, height=img.height)
    if cv2 is None:                                                # pragma: no cover
        g = ImageOps.autocontrast(img.convert("L"), cutoff=1)
        q.notes.append("OpenCV not installed: page detection, deskew and lighting correction skipped.")
        res = ScanResult(img, img, {"A": g, "B": g.resize((int(g.width * 0.8), int(g.height * 0.8)))}, q, None)
        if ocr:
            _ocr_both(res)
        return res

    bgr = _bgr(img)
    quad, fill = find_page(bgr)
    q.page_found, q.page_fill = quad is not None, fill
    page = warp(bgr, quad) if quad is not None else bgr
    q.notes.append("Page edges found and flattened." if quad is not None else "Page fills the frame; no flattening needed.")

    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    rot = detect_rotation(gray)
    if rot:
        page = np.ascontiguousarray(np.rot90(page, k=(4 - rot // 90) % 4))     # OSD reports clockwise degrees
        gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
        q.rotated_deg = rot
        q.notes.append(f"Page was turned {rot}\u00b0; rotated upright.")
    skew = estimate_skew(gray)
    q.skew_deg = skew
    if abs(skew) >= 0.3:
        page = _rotate(page, skew)
        gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
        q.notes.append(f"Straightened by {skew:+.1f}\u00b0.")

    q.page_long_px = int(max(gray.shape))
    q.sharpness, q.glare_pct = _sharpness(gray), _glare(gray)
    q.brightness = float(np.percentile(gray, 80))                              # paper level
    q.contrast = float(np.percentile(gray, 60) - np.percentile(gray, 2))      # paper level minus ink level
    judge(q)

    res = ScanResult(img, _pil(page), enhance(gray), q, quad)
    if ocr and (force or not q.retake):                    # OCR on a photo we already know is unusable only wastes time
        _ocr_both(res)
    return res


def _ocr_both(res: ScanResult) -> None:
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as ex:            # tesseract runs as a subprocess: real parallelism
        futs = {k: ex.submit(ocr_data, v) for k, v in res.variants.items()}
        for k, f in futs.items():
            res.lines[k], res.words[k] = f.result()


def overlay_quad(res: ScanResult, max_w: int = 900) -> Image.Image:
    """The original photo with the detected page outline, so the user can see what the scanner locked onto."""
    im = res.original.copy()
    if res.quad is not None:
        ImageDraw.Draw(im).line([tuple(p) for p in res.quad] + [tuple(res.quad[0])], fill=(0, 200, 90),
                                width=max(4, im.width // 200))
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)))
    return im


def photos_to_doc(name: str, results: list[ScanResult]):
    """Several photographed pages -> one Doc whose two OCR passes are the pages' A and B readings in order."""
    from .readers import Doc
    a = [l for r in results for l in r.lines.get("A", [])]
    b = [l for r in results for l in r.lines.get("B", [])]
    if not any(len(x) > 3 for x in a) and not any(len(x) > 3 for x in b):
        return Doc(name, "photo", error="no readable text found in the photo", ocr=True)
    return Doc(name, "photo", lines=a, passes=[a, b], ocr=True)
