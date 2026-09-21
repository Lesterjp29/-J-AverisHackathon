"""Sample-data helpers: render documents to images and simulate what a phone camera does to them.

Used by the tests, and by `python tools/make_samples.py` to (re)generate the demo files in samples/.
"""
from __future__ import annotations

import io
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
          "C:/Windows/Fonts/consola.ttf", "/System/Library/Fonts/Menlo.ttc"]


def _font(size: int):
    for f in _FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def render_text_page(text: str, size=(1240, 1754), font_size=30, margin=90) -> Image.Image:
    """A printed-looking A4 page from plain text (wraps nothing; the sample documents have short lines)."""
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    f = _font(font_size)
    y = margin
    for line in text.splitlines():
        d.text((margin, y), line, fill=(20, 20, 20), font=f)
        y += int(font_size * 1.45)
        if y > size[1] - margin:
            break
    return im


def pdf_page_image(pdf: str | Path, dpi=150, page=1) -> Image.Image:
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page), "-png", "-singlefile", str(pdf), f"{d}/p"],
                       check=True, capture_output=True)
        return Image.open(f"{d}/p.png").convert("RGB")


def simulate_phone_photo(page: Image.Image, seed=0, angle=6.0, keystone=0.10, scale=0.72, blur=0.0, shadow=0.35,
                         noise=5.0, glare=0.0, out_size=(3000, 4000), sideways=False, jpeg_q=82, on_desk=True, exposure=1.0) -> bytes:
    """Put a page on a desk and photograph it badly. Returns JPEG bytes."""
    rng = np.random.default_rng(seed)
    W, H = out_size
    desk = np.full((H, W, 3), (52, 46, 42), np.uint8).astype(np.float32)
    if on_desk:
        tex = cv2.GaussianBlur(rng.normal(0, 14, (H // 4, W // 4)).astype(np.float32), (0, 0), 2)
        desk += cv2.resize(tex, (W, H))[..., None]
    else:
        desk[:] = 235
    pw, ph = page.size
    tw = scale * W
    th = tw * ph / pw
    if th > 0.9 * H:
        th = 0.9 * H
        tw = th * pw / ph
    cx, cy = W / 2, H / 2
    ks = keystone * tw
    dst = np.array([[-tw / 2 + ks, -th / 2], [tw / 2 - ks * 0.6, -th / 2], [tw / 2, th / 2], [-tw / 2, th / 2 + ks * 0.4]], np.float32)
    a = np.deg2rad(angle)
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]], np.float32)
    dst = dst @ R.T + np.array([cx, cy], np.float32)
    src = np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    arr = np.array(page.convert("RGB"))
    warped = cv2.warpPerspective(arr, M, (W, H), flags=cv2.INTER_CUBIC).astype(np.float32)
    mask = cv2.warpPerspective(np.full((ph, pw), 255, np.uint8), M, (W, H))[..., None] / 255.0
    paper = warped * 0.93 + 8                                          # real paper is never pure white
    img = desk * (1 - mask) + paper * mask
    xs = np.linspace(0, 1, W, dtype=np.float32)[None, :, None]
    ys = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    img *= 1 - shadow * (0.75 * xs + 0.25 * ys)                        # uneven room light
    img *= exposure                                                       # underexposed: uniformly dim
    if glare:
        gy, gx = int(H * 0.38), int(W * 0.62)
        yy, xx = np.mgrid[0:H, 0:W]
        img += glare * 255 * np.exp(-(((xx - gx) / (W * 0.10)) ** 2 + ((yy - gy) / (H * 0.07)) ** 2))[..., None]
    if blur:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    img += rng.normal(0, noise, img.shape).astype(np.float32)
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    if sideways:
        out = out.rotate(90, expand=True)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=jpeg_q)
    return buf.getvalue()


def build_eml(email: dict, attachments: dict[str, bytes]) -> bytes:
    from email.message import EmailMessage
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = email["subject"], email["from"], "docs-team@example.com"
    m.set_content(email["body"])
    for name, data in attachments.items():
        sub = "pdf" if name.endswith(".pdf") else "plain"
        m.add_attachment(data, maintype="application", subtype="octet-stream" if sub == "pdf" else "octet-stream", filename=name)
    return bytes(m)


def main():
    import json
    import shutil
    A, S = ROOT / "attachments", ROOT / "samples"
    shutil.rmtree(S, ignore_errors=True)
    (S / "1_upload_mismatch").mkdir(parents=True)
    (S / "2_upload_ok").mkdir()
    (S / "3_email").mkdir()
    (S / "4_phone_photos").mkdir()
    (S / "5_bad_photos_should_be_rejected").mkdir()
    shutil.copy(A / "email_004_SI.txt", S / "1_upload_mismatch" / "SI.txt")
    shutil.copy(A / "email_004_BL.txt", S / "1_upload_mismatch" / "BL.txt")
    shutil.copy(A / "email_059_SI.pdf", S / "2_upload_ok" / "SI.pdf")
    shutil.copy(A / "email_059_BL.pdf", S / "2_upload_ok" / "BL.pdf")
    rec = json.loads((ROOT / "inbox" / "email_004.json").read_text(encoding="utf-8"))
    (S / "3_email" / "customer_email_with_mismatch.eml").write_bytes(build_eml(rec, {
        "SI.txt": (A / "email_004_SI.txt").read_bytes(), "BL.txt": (A / "email_004_BL.txt").read_bytes()}))
    P = S / "4_phone_photos"
    # a matching pair (photos of the real PDFs) and a MISMATCHING pair (printed text pages), taken under different bad light
    (P / "match_SI_photo.jpg").write_bytes(simulate_phone_photo(pdf_page_image(A / "email_059_SI.pdf"), seed=11, angle=5))
    (P / "match_BL_photo.jpg").write_bytes(simulate_phone_photo(pdf_page_image(A / "email_059_BL.pdf"), seed=12, angle=-7, shadow=0.5, keystone=0.12))
    (P / "mismatch_SI_photo.jpg").write_bytes(simulate_phone_photo(render_text_page((A / "email_004_SI.txt").read_text(encoding="utf-8")), seed=13, angle=4))
    (P / "mismatch_BL_photo_sideways.jpg").write_bytes(simulate_phone_photo(render_text_page((A / "email_004_BL.txt").read_text(encoding="utf-8")), seed=14, angle=-6, sideways=True))
    B = S / "5_bad_photos_should_be_rejected"
    pg = pdf_page_image(A / "email_059_SI.pdf")
    (B / "blurry.jpg").write_bytes(simulate_phone_photo(pg, seed=21, blur=9))
    (B / "too_dark.jpg").write_bytes(simulate_phone_photo(pg, seed=22, exposure=0.3, noise=9))
    (B / "glare.jpg").write_bytes(simulate_phone_photo(pg, seed=23, glare=1.6))
    (B / "too_far_away.jpg").write_bytes(simulate_phone_photo(pg, seed=24, scale=0.2, out_size=(1600, 2100)))
    (S / "README.txt").write_text(
        "Sample files to try in the app\n"
        "1_upload_mismatch  : Quick check tab -> upload SI.txt + BL.txt  -> consignee and notify party differ\n"
        "2_upload_ok        : Quick check tab -> upload SI.pdf + BL.pdf  -> No mismatch detected\n"
        "3_email            : Quick check tab -> upload the .eml         -> classified, attachments compared, reply drafted\n"
        "4_phone_photos     : Scan tab -> use the four photos (SI + BL of a matching pair; SI + BL of a mismatching pair,\n"
        "                     the BL one is sideways). They are simulated phone photos: tilted, shadowed, off-centre.\n"
        "5_bad_photos_...   : Scan tab -> each should be rejected with a plain 'retake' reason (blurry, dark, glare, too far).\n", encoding="utf-8")
    print("samples written to", S)


if __name__ == "__main__":
    main()
