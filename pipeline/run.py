"""Orchestrator: inbox -> classify -> (comparison only) read -> extract -> compare -> report.

Outputs (in --out):
  report.json          rich per-email result (evidence, confidence, review status)
  report.md            human-readable report, one line per comparison email
  review_queue.json    cases the system would not decide on its own, with source evidence
  submission.json      minimal shape for the self-evaluation endpoint

Failures are recorded on the email (status FAILED + error), never dropped, and a single email can be
re-run with --retry email_123. Human decisions come back in via --resolutions (see review.py).
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from .classify import BL_COMPARISON, classify
from .compare import Vocab, compare_docs, table_consistency
from .extract import FIELDS, extract, party_lines
from .normalize import canon_name, canon_port
from .readers import Doc, detect_kind, read_any
from .llm import llm_classify_fallback, llm_extract_fallback, llm_resolve_uncertain

ROLE_ORDER = {"SI": 0, "BL": 1}


# ---------------------------------------------------------------- one comparison
def build_vocab(inbox) -> Vocab:
    """Party names / places from born-digital attachments, used only to de-noise OCR readings."""
    names, places = set(), set()
    for e in inbox.emails():
        for a in e.get("attachments", []):
            d = read_any(a, inbox.read_bytes(a))
            if d.error or d.ocr or detect_kind(d) not in ("SI", "BL"):
                continue
            f = extract(d)
            for fn in ("shipper", "consignee", "notify_party"):
                if f[fn] and not f[fn].blank:
                    names.add(canon_name(party_lines(f[fn])[0]))
            for fn in ("port_of_loading", "port_of_discharge"):
                if f[fn] and not f[fn].blank:
                    places.add(canon_port(f[fn].raw)[0])
    return Vocab(names, places)


def check_documents(email: dict, inbox, corrections: dict | None = None, vocab: Vocab | None = None) -> dict:
    atts = email.get("attachments", [])
    res = {"status": None, "review_reason": None, "review_detail": None, "has_defect": False,
           "defect_fields": [], "fields": [], "documents": []}

    docs = [read_any(a, inbox.read_bytes(a)) for a in atts]
    for d in docs:
        d.kind = detect_kind(d) if not d.error else "UNREADABLE"
        res["documents"].append({"path": d.path, "format": d.fmt, "kind": d.kind, "ocr": d.ocr,
                                 "error": d.error})

    if len(docs) < 2:
        have = docs[0].kind if docs else "nothing"
        return {**res, "status": "NEEDS_REVIEW", "review_reason": "missing_attachment",
                "review_detail": f"comparison needs an SI and a draft BL; only {len(docs)} attachment(s) found ({have})"}

    bad = [d for d in docs if d.error]
    if bad:
        return {**res, "status": "NEEDS_REVIEW", "review_reason": "unreadable",
                "review_detail": "; ".join(f"{Path(d.path).name}: {d.error}" for d in bad)}

    kinds = sorted(d.kind for d in docs)
    if kinds != ["BL", "SI"]:
        return {**res, "status": "NEEDS_REVIEW", "review_reason": "wrong_doc_type",
                "review_detail": "expected one SI and one draft BL, got " +
                                 ", ".join(f"{Path(d.path).name} = {d.kind}" for d in docs)}

    # by CONTENT, not filename
    si_doc, bl_doc = sorted(docs, key=lambda d: ROLE_ORDER[d.kind])
    swapped = si_doc.path != atts[0]
    si_f, bl_f = extract(si_doc), extract(bl_doc)

    # *AI – When the regex/fuzzy label matcher finds nothing (a field located by extract.py is never touched)
    for fn in FIELDS:
        if si_f[fn] is None:
            sugg = llm_extract_fallback(fn, si_doc.lines)
            if sugg:
                si_f[fn] = Field(raw=sugg["value"], block=[sugg["value"]], blank=False,
                                 evidence=f"LLM extraction fallback (confidence {sugg['confidence']:.2f})")
        if bl_f[fn] is None:
            sugg = llm_extract_fallback(fn, bl_doc.lines)
            if sugg:
                bl_f[fn] = Field(raw=sugg["value"], block=[sugg["value"]], blank=False,
                                 evidence=f"LLM extraction fallback (confidence {sugg['confidence']:.2f})")

    si_p = [extract(Doc(si_doc.path, "ocr", lines=p, ocr=True))
            for p in si_doc.passes] if si_doc.ocr else None
    bl_p = [extract(Doc(bl_doc.path, "ocr", lines=p, ocr=True))
            for p in bl_doc.passes] if bl_doc.ocr else None

    if corrections:                                   # human-corrected values re-enter here
        from .review import apply_corrections
        apply_corrections(si_f, bl_f, corrections)
        si_p = bl_p = None

    results = compare_docs(si_f, bl_f, si_passes=si_p, bl_passes=bl_p,
                           si_ocr=si_doc.ocr, bl_ocr=bl_doc.ocr, vocab=vocab)

    # *AI – Uses LLM for fields flagged as "uncertain"
    for r in results:
        if r.verdict == "uncertain":
            sugg = llm_resolve_uncertain(r, r.si_evidence, r.bl_evidence)
            if sugg:
                for k, v in sugg.items():
                    # shows up automatically in r.as_dict()
                    setattr(r, k, v)

    # a document that contradicts itself is not trustworthy enough to call OK
    for d, f in ((si_doc, si_f), (bl_doc, bl_f)):
        note = table_consistency(d.lines, f)
        if note:
            for r in results:
                if r.field == "container_count" and r.verdict == "match":
                    r.verdict, r.reason = "uncertain", f"{d.kind}: {note}"

    res["fields"] = [r.as_dict() for r in results]
    res["swapped_attachment_order"] = swapped
    mism = [r for r in results if r.verdict == "mismatch"]
    miss = [r for r in results if r.verdict == "missing"]
    unc = [r for r in results if r.verdict == "uncertain"]

    if mism:                                          # a confirmed difference is reportable even if other fields are blank
        res.update(status="MISMATCH", has_defect=True,
                   defect_fields=[r.field for r in mism])
        pending = miss + unc
        if pending:
            res["review_detail"] = "also needs a person: " + \
                ", ".join(f"{r.field} ({r.reason})" for r in pending)
    elif miss:
        res.update(status="NEEDS_REVIEW", review_reason="missing_value",
                   review_detail="; ".join(f"{r.field}: {r.reason}" for r in miss + unc))
    elif unc:
        ocr = si_doc.ocr or bl_doc.ocr
        res.update(status="NEEDS_REVIEW", review_reason="unreadable" if ocr else "missing_value",
                   review_detail="; ".join(f"{r.field}: {r.reason}" for r in unc))
    else:
        res["status"] = "OK"

    return res


def process(email: dict, inbox, resolutions: dict, vocab: Vocab | None = None) -> dict:
    eid = email["email_id"]
    base = {"email_id": eid,
            "subject": email["subject"], "processing": "done", "error": None}
    try:
        kinds = []
        for a in email.get("attachments", []):
            d = read_any(a, inbox.read_bytes(a))
            kinds.append("UNREADABLE" if d.error else detect_kind(d))
        c = classify(email, kinds)
        base.update(c)
        if c["category"] != BL_COMPARISON:
            return base
        res = check_documents(
            email, inbox, (resolutions.get(eid) or {}).get("corrections"), vocab)
        base.update(res)
        dec = resolutions.get(eid)
        # reviewer confirmed the outcome outright
        if dec and dec.get("decision"):
            base.update(status=dec["decision"], has_defect=dec["decision"] == "MISMATCH",
                        defect_fields=dec.get("defect_fields", []), review_reason=None,
                        review_detail=None, resolved_by=dec.get("reviewer", "human"),
                        resolution_note=dec.get("note"))
        elif dec and dec.get("corrections"):
            base["resolved_by"] = dec.get("reviewer", "human")
            base["resolution_note"] = dec.get("note")
    except Exception as e:                            # visible + retryable, never silent
        base.update(processing="FAILED", error=f"{type(e).__name__}: {e}",
                    trace=traceback.format_exc(limit=3), status="NEEDS_REVIEW",
                    review_reason="unreadable", review_detail=f"processing failed: {e}",
                    has_defect=False, defect_fields=[], fields=[])
        if "category" not in base:
            base["category"] = "GENERAL"

        c = classify(email, kinds)
    # temporarily, right after classify(email, kinds) in run.py
    print(f"[{eid}] rule confidence={c['confidence']}")
    # only fires if c["confidence"] < 0.75
    llm_c = llm_classify_fallback(email, c)
    if llm_c:
        c = llm_c
    base.update(c)

    return base


# ---------------------------------------------------------------- outputs
def to_submission(r: dict) -> dict:
    if r["category"] != BL_COMPARISON:
        return {"category": r["category"], "status": "OK", "review_reason": None,
                "defect_fields": [], "has_defect": False}
    return {"category": r["category"], "status": r["status"], "review_reason": r.get("review_reason"),
            "defect_fields": r.get("defect_fields", []), "has_defect": bool(r.get("has_defect"))}


def review_item(r: dict) -> dict:
    return {"email_id": r["email_id"], "subject": r["subject"], "reason": r.get("review_reason"),
            "detail": r.get("review_detail"),
            "fields_needing_attention": [f for f in r.get("fields", []) if f["verdict"] in ("missing", "uncertain")],
            "all_fields": r.get("fields", []), "documents": r.get("documents", []),
            "processing": r.get("processing"), "error": r.get("error"),
            "how_to_resolve": {"decision": "OK|MISMATCH (+ defect_fields)",
                               "or corrections": {"si": {"<field>": "<value>"}, "bl": {"<field>": "<value>"}}}}


def write_report_md(results: list[dict], path: Path):
    L = ["# Shipping document verification report", ""]
    comps = [r for r in results if r["category"] == BL_COMPARISON]
    from collections import Counter
    cat = Counter(r["category"] for r in results)
    st = Counter(r["status"] for r in comps)
    L += [f"**{len(results)} emails** — " + ", ".join(f"{k}: {v}" for k, v in sorted(cat.items())), "",
          f"**{len(comps)} comparison requests** — " + ", ".join(f"{k}: {v}" for k, v in sorted(st.items())), ""]
    L += ["| Email | Result | What needs attention |", "|---|---|---|"]
    for r in comps:
        if r["status"] == "OK":
            L.append(f"| {r['email_id']} | No mismatch detected | — |")
        elif r["status"] == "MISMATCH":
            bits = [f"**{f['field']}** SI: {f['si']} / BL: {f['bl']}" for f in r["fields"]
                    if f["verdict"] == "mismatch"]
            L.append(f"| {r['email_id']} | MISMATCH | " +
                     "<br>".join(bits) + " |")
        else:
            L.append(
                f"| {r['email_id']} | NEEDS REVIEW ({r['review_reason']}) | {r.get('review_detail')} |")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", default=".",
                    help="data folder or http://host:8080")
    ap.add_argument("--out", default="out")
    ap.add_argument(
        "--resolutions", help="JSON of human decisions/corrections keyed by email_id")
    ap.add_argument("--retry", nargs="*",
                    help="re-run only these email_ids (merged into existing report.json)")
    ap.add_argument("--ask-send-as", choices=["GENERAL", "BL_COMPARISON"], default=None,
                    help="label for 'please send the draft BL' emails (default GENERAL); score both ways")
    ap.add_argument("--allow-missing-tools", action="store_true",
                    help="continue even if poppler/tesseract are missing (PDFs then go to review as unreadable)")
    ap.add_argument("--submit", action="store_true",
                    help="POST submission to the server (HTTP source only)")
    a = ap.parse_args(argv)

    from .env import preflight
    preflight(a.allow_missing_tools)
    # loader.py may sit next to pipeline/ (project root) or inside the data folder
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    if not a.source.startswith("http"):
        sys.path.insert(1, str(Path(a.source).resolve()))
    try:
        from loader import Inbox
    except ModuleNotFoundError:
        raise SystemExit("Cannot find loader.py. Put the bundle's loader.py next to the pipeline/ folder "
                         "(or inside the data folder you pass as the first argument).")
    if a.ask_send_as:
        from . import classify as _c
        _c.ASK_SEND_DRAFT_LABEL = a.ask_send_as
    inbox = Inbox(a.source)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    resolutions = json.loads(Path(a.resolutions).read_text(
        encoding="utf-8")) if a.resolutions else {}

    emails = inbox.emails()
    prior = {}
    if a.retry and (out / "report.json").exists():
        prior = {r["email_id"]: r for r in json.loads(
            (out / "report.json").read_text(encoding="utf-8"))}
        emails = [e for e in emails if e["email_id"]
                  in set(a.retry) or e["email_id"] not in prior]

    vocab = build_vocab(inbox)
    fresh = {e["email_id"]: process(
        e, inbox, resolutions, vocab) for e in emails}
    merged = {**prior, **fresh}
    order = [e["email_id"] for e in inbox.emails()]
    results = [merged[i] for i in order]

    (out / "report.json").write_text(json.dumps(results,
                                                indent=1, ensure_ascii=False), encoding="utf-8")
    write_report_md(results, out / "report.md")
    (out / "review_queue.json").write_text(json.dumps(
        [review_item(r) for r in results if r.get("status") ==
         "NEEDS_REVIEW" and r["category"] == BL_COMPARISON],
        indent=1, ensure_ascii=False), encoding="utf-8")
    sub = {r["email_id"]: to_submission(r) for r in results}
    (out / "submission.json").write_text(json.dumps(sub, indent=1), encoding="utf-8")

    failed = [r["email_id"]
              for r in results if r.get("processing") == "FAILED"]
    print(f"{len(results)} emails -> {out}/  (failed: {failed or 'none'})")
    if a.submit:
        print(json.dumps(inbox.submit(sub), indent=1))
    return results


if __name__ == "__main__":
    main()
