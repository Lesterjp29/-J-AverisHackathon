"""Email -> category. Body + attachments decide; the SUBJECT is only a weak tiebreaker
because the sample data reuses the same subject styles across different intents
('REQUEST BL DRAFT ...' appears on comparison requests AND on 'please send the draft BL')."""
from __future__ import annotations

import re

BL_COMPARISON, SI_REQUEST, INVOICE_QUERY, GENERAL, SPAM = (
    "BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM")

# "Please assist to send the draft BL for X for checking asap": no comparison is asked for and
# nothing is attached - a request to *send* a document. Decision + rationale in the README.
# Flip to BL_COMPARISON to treat these as missing-attachment review cases; score both ways.
ASK_SEND_DRAFT_LABEL = GENERAL

_BANNER = re.compile(r"^\s*WARNING: This email originated outside.*?(?:\n\s*\n|$)", re.I | re.S)

SPAM_RX = re.compile(
    r"congratulations|claim your|limited time offer|buy now|bank officer|urgent business proposal|"
    r"unpaid customs fee|you have won|verify your account|storage limit|mailbox has exceeded|"
    r"bitcoin|guaranteed|% off|undelivered messages|bit\.ly|track-parcel|webmail-verify|free-iphone|"
    r"gift card|survey and pay", re.I)
COMPARE_RX = re.compile(r"(compare|check|verify|confirm).{0,60}\b(si|shipping instruction)\b.{0,40}\b(bl|bill of lading)\b|"
                        r"\b(si|shipping instruction)\b.{0,40}\b(and|against|with)\b.{0,30}\b(draft )?(bl|bill of lading)\b|"
                        r"check the draft bl against the si", re.I | re.S)
ASK_SEND_RX = re.compile(r"(assist|please|kindly)?.{0,20}\bsend\b.{0,20}draft bl", re.I)
SI_BODY_RX = re.compile(r"shipping instruction for", re.I)
INVOICE_RX = re.compile(r"\binvoice\b|\bGR\b.{0,30}missing|\bTHC\b|local charge|D&D|detention|reverse the PGI", re.I)


def clean_body(body: str) -> str:
    return _BANNER.sub("", body).strip()


def classify(email: dict, doc_kinds: list[str] | None = None) -> dict:
    """doc_kinds: content-detected kinds of the attachments (SI/BL/OTHER:x/UNKNOWN), if any."""
    body = clean_body(email.get("body", ""))
    subj = email.get("subject", "")
    n_att = len(email.get("attachments", []))
    doc_kinds = doc_kinds or []

    def out(label, conf, why):
        return {"category": label, "confidence": conf, "reason": why}

    if SPAM_RX.search(body) or SPAM_RX.search(subj):
        return out(SPAM, 0.97, "spam phrasing / phishing link")

    # comparison request: SI/BL attachments, OR the body asks for a comparison (attachments may be missing)
    if any(k in ("SI", "BL") or k.startswith("OTHER") for k in doc_kinds) and n_att:
        return out(BL_COMPARISON, 0.95, "attachments are shipping documents (" + ", ".join(k.replace("OTHER:", "").replace("_", " ") for k in doc_kinds) + ")")
    if COMPARE_RX.search(body) and not SI_BODY_RX.search(body):
        return out(BL_COMPARISON, 0.9 if n_att else 0.85,
                   "body asks to compare/check SI vs draft BL" + ("" if n_att else " (no attachments present)"))

    if SI_BODY_RX.search(body) and re.search(r"\bPOL\s*:", body) and re.search(r"Shipper\s*:", body, re.I):
        return out(SI_REQUEST, 0.95, "new shipping instruction supplied in the body")

    if ASK_SEND_RX.search(body):
        return out(ASK_SEND_DRAFT_LABEL, 0.7, "asks for the draft BL to be sent; nothing to compare")

    if INVOICE_RX.search(body):
        return out(INVOICE_QUERY, 0.92, "invoice / billing / charges wording")
    if INVOICE_RX.search(subj) and re.search(r"billing|freight|invoice", subj, re.I):
        return out(INVOICE_QUERY, 0.6, "invoice wording in subject only")

    return out(GENERAL, 0.8, "operational notice / no actionable shipping-document request")
