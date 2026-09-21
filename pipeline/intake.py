"""Turn whatever a person hands us - files, photos, an email - into Docs and a verdict.

    analyse(files)            files: [(filename, bytes)]  ->  classification + (if it is a comparison) field results
    parse_email(name, data)   .eml / .msg  ->  {"subject", "from", "to", "body", "attachments": [(name, bytes)]}

Nothing here is UI code, so the Streamlit app, the tests and a future API all call the same functions.
"""
from __future__ import annotations

import email as _email
import io
import re
from email import policy
from html import unescape

from .classify import BL_COMPARISON, classify
from .readers import Doc, detect_kind, read_any
from .run import check_docs

EMAIL_EXTS = ("eml", "msg")
DOC_EXTS = ("txt", "pdf", "docx", "xlsx", "xlsm", "png", "jpg", "jpeg", "webp", "heic", "heif", "tif", "tiff", "bmp")


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    return re.sub(r"[ \t]+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def parse_email(name: str, data: bytes) -> dict:
    ext = name.lower().rsplit(".", 1)[-1]
    if ext == "msg":
        try:
            import extract_msg
        except ImportError:
            raise ValueError(".msg files need the 'extract-msg' package (pip install extract-msg). "
                             "Or save the email as .eml and upload that.")
        m = extract_msg.Message(io.BytesIO(data))
        atts = [(a.longFilename or a.shortFilename or "attachment", a.data) for a in m.attachments
                if getattr(a, "data", None)]
        return {"subject": m.subject or "", "from": m.sender or "", "to": m.to or "", "body": (m.body or "").strip(),
                "attachments": atts}
    msg = _email.message_from_bytes(data, policy=policy.default)
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        body = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            body = _html_to_text(body)
    atts = []
    for part in msg.iter_attachments():
        fn = part.get_filename()
        payload = part.get_payload(decode=True)
        if fn and payload:
            atts.append((fn, payload))
    return {"subject": str(msg["subject"] or ""), "from": str(msg["from"] or ""), "to": str(msg["to"] or ""),
            "body": body.strip(), "attachments": atts}


def read_uploads(files: list[tuple[str, bytes]]) -> dict:
    """Read every file once (OCR included - the slow part) and classify. Returns:
       category / confidence / reason   what kind of message or document set this is
       email                            parsed email record if an .eml/.msg was among the files, else None
       docs                             every Doc (kind detected)
       raw                              {doc.path: original bytes}, for page images and evidence boxes
    Comparing is a separate, fast step (`check_docs`) so corrections and role changes never re-run OCR."""
    emails = [(n, d) for n, d in files if n.lower().rsplit(".", 1)[-1] in EMAIL_EXTS]
    others = [(n, d) for n, d in files if n.lower().rsplit(".", 1)[-1] not in EMAIL_EXTS]
    rec, atts = None, list(others)
    if emails:
        parsed = parse_email(*emails[0])
        rec = {"email_id": "uploaded", "subject": parsed["subject"], "from": parsed["from"], "to": parsed["to"],
               "body": parsed["body"], "attachments": [n for n, _ in parsed["attachments"]]}
        atts = parsed["attachments"] + others

    docs: list[Doc] = []
    raw: dict[str, bytes] = {}
    seen: dict[str, int] = {}
    for name, data in atts:
        if name in seen:                                    # two files called scan.jpg must stay distinguishable
            seen[name] += 1
            stem, _, ext = name.rpartition(".")
            name = f"{stem or name}_{seen[name]}.{ext}" if ext else f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        docs.append(read_any(name, data))
        raw[name] = data
    for d in docs:
        d.kind = detect_kind(d) if not d.error else "UNREADABLE"

    if rec is not None:
        rec["attachments"] = [d.path for d in docs]
        c = classify(rec, [d.kind for d in docs])
    elif any(d.kind in ("SI", "BL") or d.kind.startswith("OTHER") for d in docs) or len(docs) >= 2:
        c = {"category": BL_COMPARISON, "confidence": 0.9, "reason": "uploaded documents to compare (SI / draft BL)"}
    elif docs:
        c = {"category": BL_COMPARISON, "confidence": 0.6,
             "reason": "one document uploaded - a comparison needs both an SI and a draft BL"}
    else:
        c = {"category": "GENERAL", "confidence": 0.5, "reason": "nothing to read"}
    return {**c, "email": rec, "docs": docs, "raw": raw}


def analyse(files: list[tuple[str, bytes]], vocab=None, roles: dict | None = None, corrections: dict | None = None) -> dict:
    """read_uploads + compare, in one call (tests, scripts, a future API)."""
    info = read_uploads(files)
    info["result"] = check_docs(info["docs"], corrections, vocab, roles) if info["category"] == BL_COMPARISON else None
    return info
