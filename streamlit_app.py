"""Streamlit UI: shipping instruction (SI) vs draft bill of lading (BL) checker.

    docker compose up                      # recommended: nothing else to install (see README)
    streamlit run streamlit_app.py         # or run it directly

Tabs
  Quick check      drop any files (or an email) -> instant comparison, evidence on the page, editable values, draft reply
  Scan with phone  photograph the SI and the BL -> quality feedback -> comparison (works from a phone browser)
  Inbox tabs       the batch pipeline over an inbox dataset: overview, emails, review queue, mismatch report

Everything sits on the same backend as `python -m pipeline.run`, so a result here is the result everywhere.
Uploaded files are processed in memory and are not written to disk.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import app as backend_mod                                          # noqa: E402  (shared, UI-agnostic backend)
from pipeline import evidence as ev                                # noqa: E402
from pipeline import scan                                          # noqa: E402
from pipeline.classify import BL_COMPARISON                        # noqa: E402
from pipeline.env import find_poppler_dirs, save_poppler_path      # noqa: E402
from pipeline.extract import FIELDS                                # noqa: E402
from pipeline.intake import DOC_EXTS, EMAIL_EXTS, read_uploads     # noqa: E402
from pipeline.reply import draft_reply, mailto                     # noqa: E402
from pipeline.run import check_docs, process                       # noqa: E402

st.set_page_config(page_title="Shipping document verification", page_icon="🚢", layout="wide")

# Streamlit renamed `use_container_width=True` to `width="stretch"` around 1.50; support both.
_VER = tuple(int(x) for x in st.__version__.split(".")[:2] if x.isdigit())
STRETCH = {"width": "stretch"} if _VER >= (1, 50) else {"use_container_width": True}

FIELD = {"shipper": "Shipper", "consignee": "Consignee", "notify_party": "Notify party",
         "port_of_loading": "Port of loading", "port_of_discharge": "Port of discharge",
         "container_count": "Container count", "gross_weight_kg": "Gross weight (kg)"}
REASON = {"wrong_doc_type": "Wrong document type", "missing_attachment": "Missing attachment",
          "unreadable": "Unreadable / unreliable read", "missing_value": "Missing or blank value"}
REASON_HELP = {
    "wrong_doc_type": "One attachment is not an SI or a draft BL. Enter all seven values from the right documents, or confirm an outcome.",
    "missing_attachment": "An SI and a draft BL are both needed. Enter all seven values for both sides, or confirm an outcome.",
    "unreadable": "A file could not be read reliably. Open the source, then enter the values or confirm an outcome.",
    "missing_value": "A required value is blank or a placeholder. Enter the real value; the comparison re-runs automatically."}
CAT = {"BL_COMPARISON": "Comparison request", "SI_REQUEST": "New SI request", "INVOICE_QUERY": "Invoice query",
       "GENERAL": "General", "SPAM": "Spam"}
OUTCOME = {"OK": "✅ No mismatch detected", "MISMATCH": "❌ Mismatch", "NEEDS_REVIEW": "⚠️ Needs review"}
BLANK = ("n/a", "na", "tba", "tbc", "tbd", "nil", "none", "unknown")
DEFAULT_SOURCE = os.environ.get("SDOC_DATA", ".")
DEFAULT_OUT = os.environ.get("SDOC_OUT", "out")
SAMPLES = ROOT / "samples"


def outcome(r: dict) -> str:
    return OUTCOME.get(r.get("status"), "—") if r.get("category") == BL_COMPARISON else "—"


def is_blank(v) -> bool:
    t = str(v or "").strip().lower()
    return t == "" or t in BLANK or t.startswith(("_", "?")) or set(t) <= {"-", "."}


def sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


@st.cache_resource(show_spinner=False)
def get_backend(source: str, out: str) -> backend_mod.App:
    return backend_mod.App(source, out, "GENERAL")


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("🚢 Shipping docs")
    st.caption("Check a shipping instruction against a draft bill of lading.")
    with st.expander("Inbox dataset (batch mode)"):
        source = st.text_input("Data source", value=DEFAULT_SOURCE, help="The folder that CONTAINS inbox/ and attachments/ "
                               "(usually just a dot), or http://localhost:8080 for the organisers' Docker dataset.")
        out = st.text_input("Results folder", value=DEFAULT_OUT, help="Where reports and your review decisions are saved.")
    source, out = (source.strip() or "."), (out.strip() or "out")
    _p = Path(source)
    if not source.startswith("http") and _p.name.lower() == "inbox" and _p.is_dir() and not (_p / "inbox").exists():
        st.info(f"'{source}' is the inbox folder itself, so I used its parent folder instead.")
        source = str(_p.parent)
    # cache by ABSOLUTE paths, so a different working folder never reuses another folder's backend
    B = get_backend(source if source.startswith("http") else str(Path(source).resolve()), str(Path(out).resolve()))
    if B.connect_error:                       # data may have been copied in since: re-check, no restart needed
        B.connect()
        if not B.connect_error:
            B.load_existing()
    DATASET = not B.connect_error
    B.env = backend_mod.env_setup()
    _poppler_ok = bool(B.env["pdftotext"] and B.env["pdftoppm"])
    if DATASET:
        ask = st.selectbox('"Please send the draft BL" emails as', ["GENERAL", "BL_COMPARISON"],
                           index=0 if B.ask_send_as == "GENERAL" else 1,
                           help="Emails that ask someone to SEND the draft BL (no comparison, no attachments) are "
                                "ambiguous. Score both ways and keep the better one. Applied on the next run.")
        run = st.button("Re-run pipeline" if B.results else "Run pipeline", type="primary", **STRETCH,
                        disabled=not _poppler_ok,
                        help=None if _poppler_ok else "Disabled: poppler was not found, so PDFs could not be read.")
        st.caption(f"Current results use: **{B.ask_send_as}**")
    else:
        run, ask = False, "GENERAL"
        st.caption("No inbox dataset connected - Quick check and Scan still work.")
    st.divider()
    missing = [k for k, v in B.env.items() if not v]
    st.caption("Tools: " + "  ".join(f"{'✅' if v else '❌'} {k}" for k, v in B.env.items()))
    if not _poppler_ok:
        st.warning("poppler not found. PDFs cannot be read until it is. (Running in Docker avoids this entirely.)")
        _guess = find_poppler_dirs(str(Path.home()))
        if _guess:
            st.caption("Found a poppler folder:")
            st.code(_guess[0], language=None)
            if st.button("Use this folder", key="use_guess", **STRETCH):
                err = save_poppler_path(_guess[0])
                st.error(err) if err else st.rerun()
        _typed = st.text_input("Poppler folder (the one containing pdftotext.exe)", key="poppler_dir",
                               placeholder=r"C:\Users\you\Downloads\...\poppler-26.09.0\Library\bin")
        if st.button("Save and use", key="save_poppler", **STRETCH):
            err = save_poppler_path(_typed)
            st.error(err) if err else st.rerun()
    if "tesseract" in missing:
        st.warning("Tesseract not found: scans and photos cannot be read.")

# ------------------------------------------------------------------ dataset run
if run:
    B.set_label(ask)
    bar = st.progress(0.0, text="Learning known company and port names…")
    vocab = B.get_vocab()
    fresh, total = {}, len(B.emails)
    for n, (eid, email) in enumerate(B.emails.items(), 1):
        fresh[eid] = process(email, B.inbox, B.resolutions, vocab)
        if n % 5 == 0 or n == total:
            bar.progress(n / total, text=f"Processing {eid}  ({n}/{total})")
    with B.lock:
        B.results = fresh
        B.persist()
    bar.empty()
    st.toast("Pipeline finished")

counts = B.counts() if (DATASET and B.results) else None
review = B.review() if counts else {"queue": [], "resolved": []}
queue, resolved = review["queue"], review["resolved"]

t_qc, t_scan, t_over, t_mail, t_review, t_mm = st.tabs(
    ["Quick check", "Scan with phone", "Inbox overview", "Emails",
     f"Review queue ({len(queue)})" if counts else "Review queue", "Mismatch report"])


# ------------------------------------------------------------------ shared building blocks
def cmp_frame(fields: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{"Field": FIELD.get(f["field"], f["field"]), "SI": f["si"] if f["si"] is not None else "—",
                          "BL": f["bl"] if f["bl"] is not None else "—", "Result": f["verdict"].upper(),
                          "Why": f["reason"] if f["verdict"] != "match" else ""} for f in fields])


def show_cmp(fields: list[dict]):
    if not fields:
        st.caption("No field comparison — the documents could not be compared.")
        return
    tint = {"MISMATCH": "background-color: rgba(220,53,69,.20)", "MISSING": "background-color: rgba(255,193,7,.22)",
            "UNCERTAIN": "background-color: rgba(255,193,7,.22)", "MATCH": ""}
    df = cmp_frame(fields)
    st.dataframe(df.style.apply(lambda row: [tint[row["Result"]]] * len(row), axis=1), hide_index=True, **STRETCH)


def status_banner(res: dict):
    s = res["status"]
    if s == "OK":
        st.success("**No mismatch detected.** All seven fields agree.")
    elif s == "MISMATCH":
        st.error("**Mismatch found:** " + ", ".join(FIELD[f] for f in res["defect_fields"]))
    else:
        st.warning(f"**Needs a person:** {REASON.get(res.get('review_reason'), res.get('review_reason'))}")
    if res.get("review_detail") and s != "OK":
        st.caption(res["review_detail"])


@st.cache_resource(show_spinner="Locating values on the page…", max_entries=16)
def _pages(key: str, _name: str, _data, _scans):
    return ev.render_pages(_name, _data, _scans)


def evidence_view(fields: list[dict], entries: dict, ns: str):
    """entries: {"si"|"bl": (name, bytes | None, text lines, scans | None)} -> boxes on the page image."""
    if not fields or not entries:
        return
    if not st.toggle("Show where each value was read on the page", key=f"ev:{ns}"):
        return
    st.caption("🟩 matches · 🟥 differs · 🟧 needs checking.   " + " · ".join(f"**{i + 1}** {FIELD[f]}" for i, f in enumerate(FIELDS)))
    cols = st.columns(2)
    for col, side in zip(cols, ("si", "bl")):
        if side not in entries:
            continue
        name, data, lines, scans = entries[side]
        with col:
            st.markdown(f"**{'Shipping instruction' if side == 'si' else 'Draft BL'}** · {Path(name).name}")
            items = ev.items_from_fields(fields, side)
            key = f"{name}:{sha(data) if data else id(scans)}"
            try:
                pages = _pages(key, name, data, scans)
            except Exception as e:                                 # never break the result over a picture
                st.caption(f"(could not draw the page: {e})")
                continue
            if pages is None:
                st.markdown(ev.text_evidence_html(lines, items), unsafe_allow_html=True)
            else:
                for pg in pages[:3]:
                    st.image(ev.annotate(pg, items), **STRETCH)


def fix_panel(ns: str, res: dict):
    """Let a person type the right value and re-compare. Instant: the documents are not re-read."""
    fields = res.get("fields") or []
    pre = res.get("prefill") or {}
    if fields:
        rows = [{"Field": FIELD[f["field"]], "SI": f["si"] or "", "BL": f["bl"] or ""} for f in fields]
        keys = [f["field"] for f in fields]
        label = "✏️ A value looks wrong? Fix it and re-compare"
    else:
        rows = [{"Field": FIELD[k], "SI": pre.get("si", {}).get(k, ""), "BL": pre.get("bl", {}).get(k, "")} for k in FIELDS]
        keys, label = list(FIELDS), "✏️ Enter the values yourself (all seven on both sides) and compare"
    with st.expander(label, expanded=res["status"] == "NEEDS_REVIEW" and bool(fields)):
        edited = st.data_editor(pd.DataFrame(rows), key=f"ed:{ns}", hide_index=True, disabled=["Field"], **STRETCH)
        c1, c2, _ = st.columns([2, 1, 3])
        if c1.button("Re-compare with my values", key=f"recmp:{ns}", type="primary"):
            corr = {"si": {}, "bl": {}}
            for i, k in enumerate(keys):
                for side, col in (("si", "SI"), ("bl", "BL")):
                    new, old = str(edited.iloc[i][col] or "").strip(), str(rows[i][col] or "").strip()
                    if new and (new != old or not fields):
                        corr[side][k] = new
            st.session_state[f"corr:{ns}"] = corr
            st.rerun()
        if st.session_state.get(f"corr:{ns}") and c2.button("Reset", key=f"reset:{ns}"):
            st.session_state.pop(f"corr:{ns}")
            st.rerun()
        if st.session_state.get(f"corr:{ns}"):
            st.caption("Showing the result with your values applied.")


def reply_box(email_rec: dict | None, res: dict, ns: str):
    d = draft_reply(email_rec, res)
    if not d:
        return
    tag = hashlib.sha1(d["body"].encode()).hexdigest()[:8]           # a new draft (after a fix) replaces the old text
    with st.expander("✉️ Draft reply", expanded=res["status"] != "OK"):
        to = st.text_input("To", d["to"], key=f"rto:{ns}:{tag}")
        subj = st.text_input("Subject", d["subject"], key=f"rsub:{ns}:{tag}")
        body = st.text_area("Message", d["body"], height=320, key=f"rbody:{ns}:{tag}")
        c1, c2, c3 = st.columns([2, 2, 3])
        link = mailto({"to": to, "subject": subj, "body": body})
        if link:
            c1.link_button("Open in my email app", link)
        else:
            c1.caption("Too long for a mail link - copy the text.")
        c2.download_button("Download as .txt", f"To: {to}\nSubject: {subj}\n\n{body}", "reply.txt", key=f"rdl:{ns}:{tag}")
        c3.caption("Nothing is sent automatically - you review and send it.")


def photo_panel(doc):
    scans = doc.meta.get("scans") or []
    if not scans:
        return
    with st.expander(f"📷 How the photo was processed · {Path(doc.path).name}"):
        for r in scans:
            q = r.quality
            c1, c2 = st.columns(2)
            c1.image(scan.overlay_quad(r), caption="Your photo (green line = the page the scanner found)", **STRETCH)
            c2.image(r.variants["A"], caption="Flattened, straightened and lighting-corrected (what was read)", **STRETCH)
            for level, msg in q.issues:
                (st.error if level == "retake" else st.warning)(msg)
            for note in q.notes:
                st.caption("• " + note)


def render_result(ns: str, res: dict, docs: list, raw: dict, email_rec: dict | None = None):
    status_banner(res)
    if res.get("fields"):
        st.markdown("**Field comparison** (the SI is the reference)")
        show_cmp(res["fields"])
    fix_panel(ns, res)
    entries = {}
    for d in docs:
        if d.kind in ("SI", "BL") and not d.error:
            entries[d.kind.lower()] = (d.path, raw.get(d.path), d.lines, d.meta.get("scans"))
    evidence_view(res.get("fields") or [], entries, ns)
    reply_box(email_rec, res, ns)
    for d in docs:
        photo_panel(d)


@st.cache_resource(show_spinner=False, max_entries=8)
def _read(key: str, _files):
    return read_uploads(_files)


def _vocab():
    return B.get_vocab() if DATASET else None


# ------------------------------------------------------------------ Quick check
with t_qc:
    st.subheader("Quick check")
    st.write("Drop a shipping instruction and a draft bill of lading (PDF, Word, Excel, text, or a photo), "
             "or a whole email (.eml / .msg). Files are read in memory and are **not saved**.")
    up = st.file_uploader("Files", accept_multiple_files=True, key="qc_up", label_visibility="collapsed",
                          type=sorted({*DOC_EXTS, *EMAIL_EXTS}))
    picks = {"qc_s1": ("Mismatch (text files)", "1_upload_mismatch"), "qc_s2": ("All match (PDFs)", "2_upload_ok"),
             "qc_s3": ("An email (.eml)", "3_email")}
    if SAMPLES.is_dir():
        st.caption("No files handy? Try an example:")
        s1, s2, s3, s4 = st.columns(4)
        for col, (k, (label, _)) in zip((s1, s2, s3), picks.items()):
            if col.button(label, key=k, **STRETCH):
                st.session_state["qc_sample"] = k
        if s4.button("Clear", key="qc_clear", **STRETCH):
            st.session_state.pop("qc_sample", None)
    files = [(u.name, u.getvalue()) for u in up]
    if not files and st.session_state.get("qc_sample"):
        folder = SAMPLES / picks[st.session_state["qc_sample"]][1]
        files = [(p.name, p.read_bytes()) for p in sorted(folder.iterdir()) if p.is_file()]
        st.info(f"Using the example: {picks[st.session_state['qc_sample']][0]}")
    if not files:
        st.info("Nothing loaded yet.")
    else:
        info = None
        try:
            key = hashlib.sha1("|".join(f"{n}:{sha(d)}" for n, d in files).encode()).hexdigest()
            with st.spinner("Reading the documents…"):
                info = _read(key, files)
        except ValueError as e:
            st.error(str(e))
        if info:
            ns = f"qc:{key[:10]}"
            docs, rec = info["docs"], info["email"]
            if rec:
                with st.expander(f"📧 Email · {rec['subject'] or '(no subject)'}", expanded=info["category"] != BL_COMPARISON):
                    st.write(f"**From:** {rec['from']}  \n**Category:** {CAT.get(info['category'], info['category'])} "
                             f"({round(info['confidence'] * 100)}%) — {info['reason']}")
                    st.text(rec["body"])
            if info["category"] != BL_COMPARISON:
                st.info(f"This looks like **{CAT.get(info['category'], info['category'])}** — there is nothing to compare. "
                        "Attach an SI and a draft BL to run a check.")
            else:
                roles = {}
                with st.expander("Files and their roles", expanded=any(d.kind not in ("SI", "BL") for d in docs)):
                    for d in docs:
                        pick = st.selectbox(f"{Path(d.path).name}  —  detected: {d.kind}", ["Auto", "SI", "BL"],
                                            key=f"role:{key}:{d.path}")
                        if pick != "Auto":
                            roles[d.path] = pick
                        if d.error:
                            st.caption(f"⚠️ {d.error}")
                res = check_docs(docs, st.session_state.get(f"corr:{ns}"), _vocab() if any(d.ocr for d in docs) else None, roles)
                render_result(ns, res, docs, info["raw"], rec)

# ------------------------------------------------------------------ Scan with phone
with t_scan:
    st.subheader("Scan with your phone")
    st.markdown("**1.** Lay each page flat on a plain, darker surface.  \n"
                "**2.** Get the whole page in the frame, in good light.  \n"
                "**3.** Tap **Upload → Take photo** (your normal camera app opens; this works over plain http).")
    st.caption("Tilted, shadowed or sideways pages are straightened for you. A photo that is too blurry, dark, glary or "
               "far away is rejected with a reason instead of being misread.")

    @st.cache_resource(show_spinner=False, max_entries=10)
    def _scan(key: str, force: bool, _data):
        return scan.process_photo(_data, ocr=True, force=force)

    sample_pairs = {"match": ("match_SI_photo.jpg", "match_BL_photo.jpg"),
                    "mismatch": ("mismatch_SI_photo.jpg", "mismatch_BL_photo_sideways.jpg")}
    ph = SAMPLES / "4_phone_photos"
    if ph.is_dir():
        st.caption("No paper handy? Try example photos (simulated phone shots — tilted, shadowed, one sideways):")
        e1, e2, e3, _ = st.columns([1.4, 1.4, 1, 3])
        if e1.button("Photos that match", key="sc_match", **STRETCH):
            st.session_state["scan_sample"] = "match"
        if e2.button("Photos with a mismatch", key="sc_mism", **STRETCH):
            st.session_state["scan_sample"] = "mismatch"
        if e3.button("Clear", key="sc_clear", **STRETCH):
            st.session_state.pop("scan_sample", None)
    c_si, c_bl = st.columns(2)
    usable, photos_by_role = {}, {}
    for col, role, label in ((c_si, "SI", "1 · Shipping instruction (SI)"), (c_bl, "BL", "2 · Draft bill of lading (BL)")):
        with col:
            st.markdown(f"**{label}**")
            ups = st.file_uploader("Take a photo or choose", type=list(scan.IMAGE_EXTS), accept_multiple_files=True,
                                   key=f"scan_up:{role}", label_visibility="collapsed")
            with st.expander("Use the live camera instead (needs https)"):
                cam = st.camera_input("Take a picture", key=f"cam:{role}", label_visibility="collapsed")
            photos = [(u.name, u.getvalue()) for u in ups] + ([("camera.jpg", cam.getvalue())] if cam else [])
            if not photos and st.session_state.get("scan_sample"):
                pair = sample_pairs[st.session_state["scan_sample"]]
                fn = pair[0 if role == "SI" else 1]
                photos = [(fn, (ph / fn).read_bytes())]
                st.caption(f"Example photo: {fn}")
            photos_by_role[role] = photos
            good = []
            for name, data in photos:
                k = sha(data)
                try:
                    with st.spinner(f"Reading {name}…"):
                        r = _scan(k, False, data)
                        if r.quality.retake and st.session_state.get(f"force:{k}"):
                            r = _scan(k, True, data)
                except Exception as e:
                    st.error(f"Could not process {name}: {e}")
                    continue
                q = r.quality
                a, b = st.columns([1, 2])
                a.image(r.variants["A"], **STRETCH)
                with b:
                    st.caption(name)
                    if not q.issues:
                        st.success("Good photo")
                    for level, msg in q.issues:
                        (st.error if level == "retake" else st.warning)(msg)
                    if q.retake:
                        st.checkbox("Use this photo anyway", key=f"force:{k}")
                if not q.retake or st.session_state.get(f"force:{k}"):
                    good.append((name, data, r))
            usable[role] = good

    if usable.get("SI") and usable.get("BL"):
        si_doc = scan.photos_to_doc("SI_photo", [r for _, _, r in usable["SI"]])
        bl_doc = scan.photos_to_doc("BL_photo", [r for _, _, r in usable["BL"]])
        docs = [si_doc, bl_doc]
        for d, role in ((si_doc, "SI"), (bl_doc, "BL")):
            d.meta["scans"] = [r for _, _, r in usable[role]]
        raw = {si_doc.path: usable["SI"][0][1], bl_doc.path: usable["BL"][0][1]}
        ns = "scan:" + sha((sha(raw[si_doc.path]) + sha(raw[bl_doc.path])).encode())[:10]
        st.divider()
        from pipeline.readers import detect_kind
        for d, role, other in ((si_doc, "SI", "BL"), (bl_doc, "BL", "SI")):
            if not d.error and detect_kind(d) == other:
                st.warning(f"The document in the {role} box looks like a **{other}** — check you did not swap them.")
        res = check_docs(docs, st.session_state.get(f"corr:{ns}"), _vocab(), {si_doc.path: "SI", bl_doc.path: "BL"})
        render_result(ns, res, docs, raw, None)
    elif any(photos_by_role.values()):
        st.info("Add a photo for both documents to run the check.")

# ------------------------------------------------------------------ inbox: helpers that need the dataset
NO_DATA = ("No inbox dataset is connected, so these tabs are empty. " + (B.connect_error or "") +
           "  Quick check and Scan with phone work without it.")


def show_sources(eid: str, atts: list[str], docs: list[dict] | None, key: str):
    for i, path in enumerate(atts):
        d = docs[i] if docs and i < len(docs) else {}
        tags = " · ".join(x for x in (d.get("kind"), "scanned · OCR" if d.get("ocr") else None) if x)
        with st.expander(f"{Path(path).name}" + (f"  —  {tags}" if tags else "")):
            if d.get("error"):
                st.error(d["error"])
            if not st.toggle("Show document", key=f"{key}:{eid}:{i}"):
                continue
            doc = B.doc(eid, i)
            if doc["page_image"]:
                png = B.page_png(eid, i, 1, 130)
                if png:
                    st.image(png, caption=f"{doc['name']} — page 1", **STRETCH)
                else:
                    st.caption("(page image unavailable — poppler needed)")
            if doc["lines"]:
                st.caption("OCR text" if doc["ocr"] else "Extracted text")
                st.code("\n".join(doc["lines"]), language=None)


def show_email(eid: str):
    d = B.email(eid)
    r, e = d["result"], d["email"]
    st.subheader(f"{eid} — {e['subject']}")
    st.markdown(f"**{CAT.get(r['category'], r['category'])}**  ·  {outcome(r)}"
                + (f"  ·  resolved by **{r['resolved_by']}**" if r.get("resolved_by") else ""))
    if r["category"] == BL_COMPARISON:
        status_banner(r)
        st.markdown("**Field comparison** (SI is the reference)")
        show_cmp(r["fields"])
        entries = {}
        docs_info = r.get("documents") or []
        for i, path in enumerate(e.get("attachments", [])):
            kind = docs_info[i].get("kind") if i < len(docs_info) else None
            if kind in ("SI", "BL"):
                try:
                    entries[kind.lower()] = (path, B.inbox.read_bytes(path), B.doc(eid, i)["lines"], None)
                except Exception:
                    pass
        evidence_view(r["fields"], entries, f"mail:{eid}")
        reply_box(e, r, f"mail:{eid}")
    with st.expander("How it was classified", expanded=r["category"] != BL_COMPARISON):
        st.write(f"**Category:** {CAT.get(r['category'], r['category'])}  \n"
                 f"**Confidence:** {round((r.get('confidence') or 0) * 100)}%  \n**Reason:** {r['reason']}")
        if r.get("processing") == "FAILED":
            st.error(r.get("error"))
        if r.get("swapped_attachment_order"):
            st.info("Attachment filenames were swapped; roles were taken from document content.")
        if r.get("manual_entry"):
            st.info("Compared from values typed by a reviewer.")
    with st.expander("Email body"):
        st.text(e["body"])
    if e.get("attachments"):
        st.markdown("**Attachments**")
        show_sources(eid, e["attachments"], r.get("documents"), "src")
    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("Re-run this email", key=f"retry:{eid}"):
        B.reprocess(eid)
        st.rerun()
    if d["resolution"] and c2.button("Undo human decision", key=f"undo:{eid}"):
        B.unresolve(eid)
        st.rerun()


# ------------------------------------------------------------------ Inbox overview
with t_over:
    if not counts:
        st.info(NO_DATA if not DATASET else "No results yet. Press **Run pipeline** in the sidebar.")
    else:
        s = counts["status"]
        cols = st.columns(6)
        cols[0].metric("Emails processed", counts["emails"])
        cols[1].metric("Comparison requests", counts["comparisons"])
        cols[2].metric("No mismatch detected", s.get("OK", 0))
        cols[3].metric("Mismatch found", s.get("MISMATCH", 0))
        cols[4].metric("Waiting for a person", len(queue))
        cols[5].metric("Resolved by a person", counts["resolved"])
        if counts["failed"]:
            st.warning(f"{counts['failed']} email(s) failed to process. They are in the review queue with the error.")

        def chart(title: str, data: dict, label):
            st.markdown(f"**{title}**")
            if not data:
                st.caption("Nothing to show.")
                return
            df = pd.DataFrame({"": [label(k) for k in data], "Count": list(data.values())}).sort_values("Count", ascending=False)
            st.dataframe(df, hide_index=True, height=38 + 35 * len(df), **STRETCH,
                         column_config={"Count": st.column_config.ProgressColumn(
                             "Count", min_value=0, max_value=int(df["Count"].max()), format="%d")})

        a, b = st.columns(2)
        with a:
            chart("What the inbox contained", counts["categories"], lambda k: CAT.get(k, k))
            chart("Which fields differ", counts["defect_fields"], lambda k: FIELD.get(k, k))
        with b:
            chart("Comparison outcomes", s, lambda k: OUTCOME.get(k, k))
            chart("Why cases went to a person", counts["review_reasons"], lambda k: REASON.get(k, k))

# ------------------------------------------------------------------ Emails
with t_mail:
    if not counts:
        st.info(NO_DATA if not DATASET else "No results yet. Run the pipeline first.")
    else:
        rows = pd.DataFrame(B.rows())
        c1, c2, c3 = st.columns([3, 2, 2])
        q = c1.text_input("Search id or subject", "")
        cat = c2.selectbox("Category", ["All"] + sorted(counts["categories"]), format_func=lambda k: CAT.get(k, k))
        stat = c3.selectbox("Outcome", ["All", "OK", "MISMATCH", "NEEDS_REVIEW"], format_func=lambda k: OUTCOME.get(k, k))
        view = rows
        if q:
            view = view[view["email_id"].str.contains(q, case=False) | view["subject"].str.contains(q, case=False, na=False)]
        if cat != "All":
            view = view[view["category"] == cat]
        if stat != "All":
            view = view[view["status"] == stat]
        view = view.reset_index(drop=True)
        shown = pd.DataFrame({
            "Email": view["email_id"], "Subject": view["subject"], "Category": view["category"].map(lambda k: CAT.get(k, k)),
            "Outcome": [outcome(r) for r in view.to_dict("records")],
            "What needs attention": [", ".join(FIELD[f] for f in r["defect_fields"]) or REASON.get(r["review_reason"], "")
                                     for r in view.to_dict("records")]})
        st.caption(f"{len(view)} of {len(rows)} emails. Click a row to open it.")
        ev_ = st.dataframe(shown, hide_index=True, **STRETCH, height=340, on_select="rerun", selection_mode="single-row",
                           key="email_table",
                           column_config={"Email": st.column_config.TextColumn(width="small"),
                                          "Subject": st.column_config.TextColumn(width="medium"),
                                          "Category": st.column_config.TextColumn(width="medium"),
                                          "Outcome": st.column_config.TextColumn(width="medium"),
                                          "What needs attention": st.column_config.TextColumn(width="medium")})
        picked = ev_.selection.rows if ev_ is not None else []
        st.divider()
        if picked:
            show_email(view.loc[picked[0], "email_id"])
        else:
            st.info("Select an email above to see its SI-vs-BL comparison, where each value was read, and a draft reply.")

# ------------------------------------------------------------------ Review queue
with t_review:
    if not counts:
        st.info(NO_DATA if not DATASET else "No results yet. Run the pipeline first.")
    else:
        st.subheader(f"Waiting for a person ({len(queue)})")
        st.caption("The system did not guess. Each case shows why it stopped, what it read, and the source.")
        if not queue:
            st.success("Queue is empty. Every comparison has an outcome.")
        for it in queue:
            eid, r = it["email_id"], it["result"]
            by = {f["field"]: f for f in r.get("fields", [])}
            flagged = {f["field"] for f in it.get("fields_needing_attention", [])}
            saved = (it.get("resolution") or {}).get("corrections") or {}
            pre = r.get("prefill") or {}

            def val(f: str, side: str) -> str:
                if saved.get(side, {}).get(f):
                    return saved[side][f]
                v = by[f][side] if f in by else pre.get(side, {}).get(f)
                return "" if v is None or is_blank(v) else str(v)

            with st.expander(f"**{eid}** · {REASON.get(it['reason'], it['reason'])} · {it['subject'][:80]}"):
                st.warning(f"**Why:** {it['detail']}")
                st.caption(REASON_HELP.get(it["reason"], ""))
                if r.get("fields"):
                    st.markdown("**What was read (SI vs BL)**")
                    show_cmp(r["fields"])
                show_sources(eid, it["attachments"], r.get("documents"), "rsrc")
                reply_box(B.emails.get(eid), r, f"rq:{eid}")

                act = st.radio("Action", ["Enter correct values", "Confirm: no mismatch", "Confirm: mismatch"],
                               key=f"act:{eid}", horizontal=True)
                corr = {"si": {}, "bl": {}}
                if act == "Enter correct values":
                    if not r.get("fields") and (pre.get("si") or pre.get("bl")):
                        st.success("Values already read from the readable document(s) are pre-filled. Check them, then complete the rest.")
                    h = st.columns([1.3, 2, 2])
                    h[1].caption("SI VALUE")
                    h[2].caption("BL VALUE")
                    for f, name in FIELD.items():
                        c = st.columns([1.3, 2, 2])
                        c[0].markdown(f"**{name} ●**" if f in flagged else name)
                        for side, col in (("si", c[1]), ("bl", c[2])):
                            corr[side][f] = col.text_input(f"{name} {side}", value=val(f, side), key=f"v:{eid}:{side}:{f}",
                                                           label_visibility="collapsed",
                                                           placeholder="needs a value" if f in flagged else "")
                    st.caption("● = the field that triggered the review. Whatever you enter is compared with the same code "
                               "as the automatic path. For a missing or wrong document, fill all seven fields on both sides.")
                defect = []
                if act == "Confirm: mismatch":
                    defect = st.multiselect("Which fields genuinely differ?", list(FIELD), format_func=lambda k: FIELD[k],
                                            key=f"mf:{eid}")
                n1, n2, n3 = st.columns([1, 3, 1])
                reviewer = n1.text_input("Your name", key=f"rv:{eid}", value=st.session_state.get("reviewer", ""))
                note = n2.text_input("Note (what you checked)", key=f"nt:{eid}")
                n3.write("")
                if n3.button("Save decision", type="primary", key=f"save:{eid}", **STRETCH):
                    st.session_state["reviewer"] = reviewer
                    payload = {"email_id": eid, "reviewer": reviewer, "note": note}
                    if act == "Enter correct values":
                        payload["corrections"] = corr
                    else:
                        payload["decision"] = "OK" if act.endswith("no mismatch") else "MISMATCH"
                        payload["defect_fields"] = defect
                    try:
                        res = B.resolve(payload)
                    except ValueError as e:
                        st.error(str(e))
                    else:
                        if res["status"] == "NEEDS_REVIEW":
                            st.warning("Saved, but it still needs attention: " + (res.get("review_detail") or ""))
                        else:
                            st.session_state["flash"] = f"{eid}: " + ("no mismatch detected" if res["status"] == "OK" else
                                                                     "mismatch — " + ", ".join(FIELD[f] for f in res["defect_fields"]))
                            st.rerun()

        if resolved:
            st.subheader(f"Resolved by a person ({len(resolved)})")
            for x in resolved:
                c = st.columns([1.2, 2.2, 2, 3, 1])
                c[0].markdown(f"`{x['email_id']}`")
                c[1].write(outcome(x["result"]) + (" — " + ", ".join(FIELD[f] for f in x["result"]["defect_fields"])
                                                 if x["result"]["status"] == "MISMATCH" else ""))
                c[2].caption(f"{x['resolution']['reviewer']} · {x['resolution'].get('at', '')}")
                c[3].caption(x["resolution"].get("note", ""))
                if c[4].button("Undo", key=f"undo_r:{x['email_id']}"):
                    B.unresolve(x["email_id"])
                    st.rerun()

if st.session_state.get("flash"):
    st.toast(st.session_state.pop("flash"))

# ------------------------------------------------------------------ Mismatch report
with t_mm:
    if not counts:
        st.info(NO_DATA if not DATASET else "No results yet. Run the pipeline first.")
    else:
        mm = B.mismatches()
        ids = {m["email_id"] for m in mm}
        st.subheader(f"{len(ids)} emails with a mismatch · {len(mm)} fields")
        if mm:
            st.dataframe(pd.DataFrame([{"Email": m["email_id"], "Field": FIELD[m["field"]], "SI": m["si"], "BL": m["bl"]}
                                       for m in mm]), hide_index=True, **STRETCH, height=460)
            st.download_button("Download CSV", B.mismatch_csv().encode("utf-8-sig"), "mismatches.csv", "text/csv")
        with st.expander(f"{len(B.matches())} emails: No mismatch detected"):
            st.write(", ".join(B.matches()))

# ------------------------------------------------------------------ exports (sidebar, only once results exist)
if counts:
    with st.sidebar:
        st.divider()
        st.caption("Export")
        for name, mime in (("report.md", "text/markdown"), ("submission.json", "application/json"),
                           ("review_queue.json", "application/json"), ("resolutions.json", "application/json")):
            p = B.out / name
            if p.exists():
                st.download_button(name, p.read_bytes(), name, mime, **STRETCH, key=f"dl:{name}")
        st.download_button("mismatches.csv", B.mismatch_csv().encode("utf-8-sig"), "mismatches.csv", "text/csv",
                           **STRETCH, key="dl:csv")
        if B.source.startswith("http") and st.button("Submit to scoreboard", **STRETCH):
            try:
                from pipeline.run import to_submission
                st.json(B.inbox.submit({r["email_id"]: to_submission(r) for r in B.ordered()}))
            except Exception as e:
                st.error(str(e))
