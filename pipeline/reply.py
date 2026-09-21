"""Draft the reply a person would otherwise write by hand after a check.

Deterministic templates (no LLM): the wording is predictable, every number comes straight from the comparison
result, and a person reads and sends it - the system never sends anything.
"""
from __future__ import annotations

import re
from urllib.parse import quote

FIELD_LABEL = {"shipper": "Shipper", "consignee": "Consignee", "notify_party": "Notify party",
               "port_of_loading": "Port of loading", "port_of_discharge": "Port of discharge",
               "container_count": "Container count", "gross_weight_kg": "Gross weight (kg)"}
_SIGNOFF = re.compile(r"(?im)^\s*(best regards|kind regards|warm regards|regards|thanks|thank you|best|sincerely|cheers)[,!.]*\s*$")


def sender_name(body: str) -> str | None:
    """First name from the sign-off block of the ORIGINAL email (the greeting names us, not the sender)."""
    m = None
    for m in _SIGNOFF.finditer(body or ""):
        pass
    if not m:
        return None
    for line in body[m.end():].splitlines():
        line = line.strip()
        if line:
            name = re.sub(r"[^A-Za-z .'-]", "", line).strip()
            return name.split()[0].title() if name and len(name.split()[0]) > 1 else None
    return None


def _plain(f: dict) -> str:
    """One field's problem in words a customer understands (never internal field codes)."""
    label = FIELD_LABEL.get(f["field"], f["field"])
    side = "SI" if str(f.get("reason", "")).startswith("SI") else "draft BL"
    if f["verdict"] == "missing":
        shown = f.get("si") if side == "SI" else f.get("bl")
        return f"{label} is blank or a placeholder on the {side}" + (f" ({shown})" if shown else "")
    return f"we could not read {label} reliably"


def _table(rows: list[tuple[str, str, str]]) -> str:
    w0 = max(len("Field"), *(len(r[0]) for r in rows))
    w1 = max(len("SI"), *(len(r[1]) for r in rows))
    out = [f"  {'Field'.ljust(w0)}   {'SI'.ljust(w1)}   Draft BL", f"  {'-' * w0}   {'-' * w1}   {'-' * 8}"]
    out += [f"  {a.ljust(w0)}   {b.ljust(w1)}   {c}" for a, b, c in rows]
    return "\n".join(out)


def draft_reply(email: dict | None, result: dict | None) -> dict | None:
    """-> {"to", "subject", "body", "kind"} or None when there is nothing to say (not a comparison)."""
    if not result or not result.get("status"):
        return None
    email = email or {}
    name = sender_name(email.get("body", ""))
    subj = email.get("subject") or "Shipping document check"
    subject = "RE: " + re.sub(r"(?i)^\s*((re|fw|fwd)\s*[:_]\s*)+", "", subj)          # 'RE_ ' from exports counts too
    hi = f"Hi {name}," if name else "Hello,"
    status = result["status"]
    fields = result.get("fields") or []
    mism = [f for f in fields if f["verdict"] == "mismatch"]
    pending = [f for f in fields if f["verdict"] in ("missing", "uncertain")]

    if status == "OK":
        kind, body = "ok", (
            f"{hi}\n\nThank you for sending the shipping instruction and the draft bill of lading. We compared the two "
            "documents - shipper, consignee, notify party, port of loading, port of discharge, container count and gross "
            "weight - and found no mismatch.\n\nYou can go ahead and finalise the draft.\n")
    elif status == "MISMATCH":
        rows = [(FIELD_LABEL[f["field"]], str(f["si"]), str(f["bl"])) for f in mism]
        n = len(rows)
        body = (f"{hi}\n\nThank you for sending the shipping instruction and the draft bill of lading. We compared them "
                f"and found {n} difference{'s' if n != 1 else ''} that need to be corrected before the BL is finalised "
                f"(the shipping instruction is our reference):\n\n{_table(rows)}\n\n"
                "Please confirm which is correct and send us the amended draft BL (or SI) so we can check it again.\n")
        if pending:
            body += "\nWe could not verify these yet: " + "; ".join(_plain(f) for f in pending) + ".\n"
        kind = "mismatch"
    else:
        reason, detail = result.get("review_reason"), result.get("review_detail") or ""
        kind = reason or "review"
        asks = {
            "missing_attachment": "we did not receive both documents. Please resend the shipping instruction and the draft "
                                  "BL as attachments so we can compare them.",
            "wrong_doc_type": "one of the attachments is not a shipping instruction or a draft BL"
                              + (f" ({detail})" if detail else "") + ". Please resend the correct documents.",
            "unreadable": "we could not read one of the files"
                          + (f" ({detail})" if detail else "") + ". Please resend it as a clear PDF, or a sharper photo.",
            "missing_value": "some required details are missing (" + ("; ".join(_plain(f) for f in pending) or detail)
                             + "). Please send us the missing values.",
        }
        body = (f"{hi}\n\nThank you for your email. We could not complete the check because "
                f"{asks.get(reason, 'the documents need a person to review them (' + detail + ').')}\n")
    body += "\nThanks and regards,\n[Your name]"
    return {"to": email.get("from", ""), "subject": subject, "body": body, "kind": kind}


def mailto(reply: dict, limit: int = 1800) -> str | None:
    """A mailto: link that opens the person's own mail app - or None if the draft is too long for one."""
    url = f"mailto:{quote(reply['to'])}?subject={quote(reply['subject'])}&body={quote(reply['body'])}"
    return url if len(url) <= limit else None
