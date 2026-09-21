import os
import json
import shutil
from pathlib import Path
import streamlit as st
from ui_helpers import comparison_table, summary_table, report_status, text as html_text

# Ensure environment setup is run so poppler and tesseract are discovered on Windows
try:
    from pipeline.env import setup
    setup()
except Exception:
    pass

# --- Page Config ---
st.set_page_config(
    page_title="DocuVerify — Shipping Document Verification",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Match custom surfaces to the native Streamlit theme in .streamlit/config.toml.
st.markdown(f"<style>{Path(__file__).with_name('ui.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)
st.markdown('<a class="skip-link" href="#main-content">Skip to main content</a>', unsafe_allow_html=True)

OUT_DIR = Path("out")
RESOLUTIONS_FILE = Path("resolutions.json")


@st.cache_data
def load_data():
    report_path = OUT_DIR / "report.json"
    review_path = OUT_DIR / "review_queue.json"
    sub_path = OUT_DIR / "submission.json"

    raw_report = json.loads(report_path.read_text(
        encoding="utf-8")) if report_path.exists() else {}
    reviews = json.loads(review_path.read_text(
        encoding="utf-8")) if review_path.exists() else []
    subs = json.loads(sub_path.read_text(encoding="utf-8")
                      ) if sub_path.exists() else {}

    report_list = []
    if isinstance(raw_report, dict):
        for k, v in raw_report.items():
            entry = {"email_id": k, **
                     (v if isinstance(v, dict) else {"details": v})}
            report_list.append(entry)
    elif isinstance(raw_report, list):
        report_list = raw_report

    return report_list, reviews, subs


report_data, review_queue, submissions = load_data()

# Calculate stats
intents = {}
outcomes = {"OK": 0, "MISMATCH": 0, "NEEDS_REVIEW": 0}

for item in report_data:
    intent = str(item.get("category") or item.get("intent", "UNKNOWN")).upper()
    intents[intent] = intents.get(intent, 0) + 1

    status = report_status(item)
    if status in outcomes:
        outcomes[status] += 1

# Check AI status
gemini_key_present = bool(os.environ.get("GEMINI_API_KEY"))

# --- Sidebar Navigation ---
pages = {
    "overview": "📊 Dashboard Overview",
    "scanner": "📸 Document Scanner",
    "review": "⚠️ Review Queue",
    "explorer": "🔍 Comparison Explorer",
    "distribution": "📈 Intent Distribution",
}


def remember_page():
    st.query_params["page"] = next(slug for slug, label in pages.items() if label == st.session_state["navigation"])


def remember_review_draft(email_id):
    # Widget state is removed by Streamlit when an item leaves the current page.
    st.session_state.setdefault("review_drafts", {})[email_id] = {
        "decision": st.session_state.get(f"d_{email_id}", "Select resolution…"),
        "note": st.session_state.get(f"n_{email_id}", ""),
    }


with st.sidebar:
    st.markdown("**🚢 DocuVerify**")
    st.caption("AI Shipping Document Automation")
    st.write("")

    nav_selection = st.radio(
        "Navigation",
        list(pages.values()),
        index=list(pages).index(st.query_params.get("page")) if st.query_params.get("page") in pages else 0,
        key="navigation",
        on_change=remember_page,
        label_visibility="visible"
    )
    st.write("---")

    # Engine status indicators
    st.markdown("**System Health**")
    st.caption("OCR Engine: **Available**" if shutil.which("tesseract") else "OCR Engine: **Not configured**")
    if gemini_key_present:
        st.caption("AI Model: **Active** 🤖")
    else:
        st.caption("AI Model: **Rule-Only** ⚡")

    st.caption(f"Batch Dataset: `{len(report_data)} emails indexed`")

st.markdown('<div id="main-content" tabindex="-1"></div>', unsafe_allow_html=True)

# ==============================================================================
# VIEW 1: DASHBOARD OVERVIEW
# ==============================================================================
if nav_selection == "📊 Dashboard Overview":
    st.markdown("""
    <div class="dashboard-header">
        <h1>Shipping Document Overview</h1>
        <p>Review the latest batch results, compare documents, and resolve flagged shipments.</p>
    </div>
    """, unsafe_allow_html=True)

    metrics = [
        ("Emails Indexed", len(report_data), "neutral", f"{len(intents)} categories"),
        ("Matching Documents", outcomes["OK"], "ok", "Fields match"),
        ("Document Mismatches", outcomes["MISMATCH"], "mismatch", "Discrepancies found"),
        ("Awaiting Review", len(review_queue), "review", "Human review"),
    ]
    cards = "".join(
        f'<div class="metric-card"><div class="metric-title">{title}</div>'
        f'<div class="metric-value">{count:,}</div><span class="badge badge-{color}">{label}</span></div>'
        for title, count, color, label in metrics
    )
    st.markdown(f'<div class="metric-grid">{cards}</div>', unsafe_allow_html=True)
    if not report_data:
        st.info("No batch results yet. Open Document Scanner to upload and verify your documents.")

    col_left, col_right = st.columns([5, 3])

    with col_left:
        with st.container(border=True):
            st.markdown("## 📋 Urgent Items Needing Attention")
            st.caption("Documents flagged for unreadable OCR, missing attachments, or discrepancies.")

            preview_items = review_queue[:5] if review_queue else []
            if not preview_items:
                st.success("No urgent items pending review!")
            for item in preview_items:
                eid = item.get("email_id") or item.get("email") or "Unknown"
                reason = item.get("reason", "Needs validation")
                detail = item.get("detail", "")
                st.markdown(f"""
                <div class="action-item">
                    <div>
                        <strong>{html_text(eid)}</strong> — {html_text(reason)}
                        <div class="action-detail">{html_text(detail)}</div>
                    </div>
                    <span class="badge badge-review">Pending</span>
                </div>
                """, unsafe_allow_html=True)

    with col_right:
        with st.container(border=True):
            st.markdown("## 📊 Email Category Breakdown")
            if intents:
                st.bar_chart(intents)
                st.markdown(summary_table(intents), unsafe_allow_html=True)
            else:
                st.info("No email categories to display yet.")

# ==============================================================================
# VIEW 2: 📸 DOCUMENT SCANNER (EPHEMERAL UPLOAD & VERIFY)
# ==============================================================================
elif nav_selection == "📸 Document Scanner":
    st.markdown("""
    <div class="scanner-hero">
        <h1>Document Scanner</h1>
        <p>Verify Shipping Instructions (SI) against draft Bills of Lading (BL). Works with phone photos, flatbed scans, PDFs, and digital files.</p>
        <div>
            <span class="pill-tag">📄 SI &amp; BL Comparison</span>
            <span class="pill-tag">⚡ Quick Verification</span>
            <span class="pill-tag">📷 Photo Quality Inspector</span>
            <span class="pill-tag">🤖 Advisory AI Assistance</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Quick test options
    st.markdown("## Upload Documents to Verify")
    st.caption("Upload a Shipping Instruction and a draft Bill of Lading, or try a sample below.")
    c_sample1, c_sample2, c_clear = st.columns(3)
    with c_sample1:
        load_sample_ok = st.button("Try Matching Sample", use_container_width=True, help="Load clean matching SI & BL")
    with c_sample2:
        load_sample_mismatch = st.button("Try Mismatch Sample", use_container_width=True, help="Load mismatching SI & BL")
    with c_clear:
        if st.button("🔄 Reset", use_container_width=True):
            st.session_state["scanner_result"] = None
            st.session_state["demo_files"] = None
            st.session_state["upload_version"] = st.session_state.get("upload_version", 0) + 1
            st.rerun()

    # Handle sample load buttons
    if load_sample_ok or load_sample_mismatch:
        sample_id = "001" if load_sample_ok else "004"
        p1 = Path(f"attachments/email_{sample_id}_SI.txt")
        p2 = Path(f"attachments/email_{sample_id}_BL.txt")
        st.session_state["scanner_result"] = None
        try:
            with st.spinner("Processing sample documents…"):
                from pipeline.intake import analyse
                files = [(p1.name, p1.read_bytes()), (p2.name, p2.read_bytes())]
                st.session_state["scanner_result"] = analyse(files)
                st.session_state["demo_files"] = files
                st.session_state["upload_version"] = st.session_state.get("upload_version", 0) + 1
        except Exception as error:
            st.error(f"Could not open this sample. Upload your own SI and BL files to continue. Details: {error}")

    uploaded_files = st.file_uploader(
        "Shipping Instruction and Bill of Lading files",
        key=f"documents_{st.session_state.get('upload_version', 0)}",
        on_change=lambda: st.session_state.update(scanner_result=None, demo_files=None),
        type=["png", "jpg", "jpeg", "webp", "pdf", "docx", "xlsx", "txt"],
        accept_multiple_files=True,
        help="Upload at least two documents: one Shipping Instruction (SI) and one draft Bill of Lading (BL). Both text documents and photos are supported.",
        label_visibility="visible"
    )

    if uploaded_files:
        st.markdown(f"**{len(uploaded_files)} file(s) selected:**")
        cols = st.columns(min(len(uploaded_files), 4))
        for i, uf in enumerate(uploaded_files):
            ext = uf.name.rsplit('.', 1)[-1].lower()
            icon = {"pdf": "📄", "txt": "📝", "docx": "📃", "xlsx": "📊", "png": "🖼️", "jpg": "📷", "jpeg": "📷"}.get(ext, "📁")
            cols[i % len(cols)].caption(f"{icon} **{uf.name}** ({uf.size / 1024:.1f} KB)")

        if st.button("🚀 Verify Attached Documents", type="primary", use_container_width=True):
            with st.spinner("Reading documents, judging photo quality, and performing comparison…"):
                try:
                    from pipeline.intake import analyse
                    files = [(f.name, f.getvalue()) for f in uploaded_files]
                    st.session_state["scanner_result"] = analyse(files)
                    st.session_state["demo_files"] = None
                except Exception as e:
                    st.error(f"Could not verify these documents. Check that the files open correctly, then try again. Details: {e}")
                    st.session_state["scanner_result"] = None

    # --- Render Scan & Compare Results ---
    scan_result = st.session_state.get("scanner_result")
    if scan_result:
        st.markdown("---")
        if st.session_state.get("demo_files"):
            st.info("Showing sample results. Upload your own documents to start a new comparison.")
        category = scan_result.get("category", "UNKNOWN")
        confidence = scan_result.get("confidence", 0)
        reason = scan_result.get("reason", "")

        cat_labels = {
            "BL_COMPARISON": "Document Verification (SI vs BL)",
            "SI_REQUEST": "Shipping Instruction Request",
            "INVOICE_QUERY": "Commercial Invoice Query",
            "GENERAL": "General Correspondence",
            "SPAM": "Non-Operational / Spam"
        }

        comparison = scan_result.get("result")
        if not comparison:
            st.info(f"No document comparison was produced. Identified as {cat_labels.get(category, category)}. Upload both an SI and a draft BL to compare their fields.")
        if comparison:
            status = comparison.get("status", "UNKNOWN")
            fields = comparison.get("fields", [])
            defect_fields = comparison.get("defect_fields", [])
            review_reason = comparison.get("review_reason", "")
            review_detail = comparison.get("review_detail", "")

            # 1. Big Verdict Card
            if status == "OK":
                st.markdown("""
                <div role="status" aria-live="polite" class="verdict-card verdict-ok">
                    <div class="verdict-icon" aria-hidden="true">✅</div>
                    <div class="verdict-text">
                        <h2 style="color: #166534;">All Compared Fields Match</h2>
                        <p>All extracted fields between the Shipping Instruction (SI) and draft Bill of Lading (BL) match successfully after normalisation.</p>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            elif status == "MISMATCH":
                st.markdown(f"""
                <div role="status" aria-live="polite" class="verdict-card verdict-mismatch">
                    <div class="verdict-icon" aria-hidden="true">❌</div>
                    <div class="verdict-text">
                        <h2 style="color: #BE123C;">Discrepancy Detected in {len(defect_fields)} Field(s)</h2>
                        <p>Discrepancies found in: <strong>{html_text(', '.join(defect_fields))}</strong>. See field-by-field breakdown below.</p>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            elif status == "NEEDS_REVIEW":
                st.markdown(f"""
                <div role="status" aria-live="polite" class="verdict-card verdict-review">
                    <div class="verdict-icon" aria-hidden="true">⚠️</div>
                    <div class="verdict-text">
                        <h2 style="color: #92400E;">Needs Human Attention ({html_text(review_reason)})</h2>
                        <p>{html_text(review_detail or 'One or more fields requires human verification.')}</p>
                    </div>
                </div>
                """, unsafe_allow_html=True)

            # 2. Side-by-side Field Comparison Table
            if fields:
                st.markdown("## 📊 Field-by-Field Verification")
                st.markdown(comparison_table(fields), unsafe_allow_html=True)

            # 3. Document identification cards
            documents = comparison.get("documents", [])
            if documents:
                st.markdown("## 📁 Document Role Identification")
                d_cols = st.columns(min(len(documents), 3))
                for idx, doc in enumerate(documents):
                    doc_path = Path(doc.get("path", "unknown")).name
                    doc_fmt = doc.get("format", "unknown")
                    doc_kind = doc.get("kind", "unknown")
                    doc_ocr = doc.get("ocr", False)
                    doc_error = doc.get("error")

                    kind_badge = "badge-ok" if doc_kind == "SI" else ("badge-neutral" if doc_kind == "BL" else "badge-review")
                    with d_cols[idx % len(d_cols)]:
                        with st.container(border=True):
                            st.markdown(f"**{doc_path}**")
                            st.caption(f"Format: `{doc_fmt}` | OCR: `{'Yes' if doc_ocr else 'No'}`")
                            st.markdown(f'<span class="badge {kind_badge}">{html_text(doc_kind)}</span>', unsafe_allow_html=True)
                            if doc_error:
                                st.caption(f":red[{doc_error}]")

        # 4. Photo Quality Inspector (if images were provided)
        docs_info = scan_result.get("docs", [])
        photo_docs = [d for d in docs_info if hasattr(d, 'meta') and d.meta.get("scans")]
        if photo_docs:
            st.markdown("## 📷 Photo Quality & Retake Inspector")
            for d in photo_docs:
                for scan_res in d.meta.get("scans", []):
                    q = scan_res.quality
                    with st.container(border=True):
                        st.markdown(f"**Quality Diagnostics for `{Path(d.path).name}`**")
                        q_cols = st.columns(4)
                        q_cols[0].metric("Sharpness", f"{q.sharpness:.2f}")
                        q_cols[1].metric("Page Dimensions", f"{q.page_long_px} px" if q.page_long_px else f"{q.width}x{q.height}")
                        q_cols[2].metric("Glare Share", f"{q.glare_pct:.1%}")
                        q_cols[3].metric("Deskew Applied", f"{q.skew_deg}°")

                        chips = ""
                        for level, msg in q.issues:
                            cls = "qc-retake" if level == "retake" else "qc-warn"
                            icon = "🔴 Retake: " if level == "retake" else "🟡 Notice: "
                            chips += f'<span class="quality-chip {cls}">{icon}{html_text(msg)}</span> '
                        if not q.issues:
                            chips = '<span class="quality-chip qc-ok">🟢 High Quality Photo — Clean for OCR</span>'
                        st.markdown(chips, unsafe_allow_html=True)

        # 5. Raw Output Expander (for technical / audit view)
        with st.expander("🔧 Inspection Payload (Raw JSON)"):
            display_result = {k: v for k, v in scan_result.items() if k not in ("docs", "raw", "email")}
            st.json(display_result)

# ==============================================================================
# VIEW 3: ⚠️ REVIEW QUEUE
# ==============================================================================
elif nav_selection == "⚠️ Review Queue":
    st.markdown("""
    <div class="dashboard-header">
        <h1>⚠️ Human Review Queue</h1>
        <p>Inspect, verify, and resolve shipments flagged for missing values, OCR ambiguity, or wrong attachment types.</p>
    </div>
    """, unsafe_allow_html=True)

    n_with_ai = sum(
        1 for rev in review_queue
        if any(f.get("llm_suggested_verdict") for f in (rev.get("all_fields") or []))
    )
    if n_with_ai:
        st.info(f"🤖 **{n_with_ai} of {len(review_queue)}** items have an advisory AI read on at least one field.")

    if not review_queue:
        st.success("No documents need review. You’re all caught up.")
    review_page = st.number_input("Review page", min_value=1, max_value=max(1, (len(review_queue) + 9) // 10), step=1, key="review_page")
    review_start = (review_page - 1) * 10
    st.caption(f"Showing {min(review_start + 1, len(review_queue))}–{min(review_start + 10, len(review_queue))} of {len(review_queue)} items")
    for idx, rev in enumerate(review_queue[review_start:review_start + 10], start=review_start):
        eid = rev.get("email_id") or f"Item {idx+1}"
        reason = rev.get("reason", "Flagged")
        detail = rev.get("detail", "")
        all_fields = rev.get("all_fields") or []
        llm_fields = [f for f in all_fields if f.get("llm_suggested_verdict")]
        fields_to_show = rev.get("fields_needing_attention") or all_fields

        title = f"**{eid}** — {reason}"
        if llm_fields:
            title += f"  🤖 ({len(llm_fields)} AI suggestions)"

        with st.expander(title, expanded=(idx == review_start)):
            col_a, col_b = st.columns([3, 2])

            with col_a:
                st.markdown(f"**Issue Detail:** `{detail}`")
                st.write("")
                for f in fields_to_show:
                    fname = f.get("field", "field")
                    verdict = f.get("verdict", "")
                    st.markdown(f"**`{fname}`** — *{verdict}*")

                    fc1, fc2 = st.columns(2)
                    fc1.markdown(f'<div class="value-card"><span class="value-label">Shipping Instruction (SI)</span>{html_text(f.get("si"))}</div>', unsafe_allow_html=True)
                    fc2.markdown(f'<div class="value-card"><span class="value-label">Bill of Lading (BL)</span>{html_text(f.get("bl"))}</div>', unsafe_allow_html=True)

                    if f.get("reason"):
                        st.caption(f"Discrepancy: {f['reason']}")

                    sv = f.get("llm_suggested_verdict")
                    if sv:
                        icon = {"same": "🟢", "different": "🔴", "cannot_tell": "🟡"}.get(sv, "⚪")
                        st.markdown(
                            f"""<div class="llm-box">
                                <span style="font-weight:700; color:#6D28D9;">{icon} AI read: {html_text(sv.upper())}</span><br>
                                <span style="font-size:14px; color:#475569;">{html_text(f.get('llm_reasoning', ''))}</span><br>
                                <span style="font-size:14px; color:#475569;">{html_text(f.get('llm_note', 'Advisory only — confirm before resolving'))}</span>
                            </div>""",
                            unsafe_allow_html=True,
                        )
                    st.divider()

            with col_b:
                with st.container(border=True):
                    st.markdown("**Resolution Action**")
                    draft = st.session_state.get("review_drafts", {}).get(eid, {})
                    decisions = ["Select resolution…", "OK (Approve as Match)", "MISMATCH (Flag Defect)"]
                    decision = st.selectbox(
                        "Decision",
                        decisions,
                        index=decisions.index(draft.get("decision", decisions[0])),
                        key=f"d_{eid}",
                        on_change=remember_review_draft,
                        args=(eid,),
                    )
                    notes = st.text_input(
                        "Reviewer Note",
                        placeholder="e.g. Confirmed the weight with the customer…",
                        autocomplete="off",
                        value=draft.get("note", ""),
                        key=f"n_{eid}",
                        on_change=remember_review_draft,
                        args=(eid,),
                    )
                    st.caption("Draft notes stay available while you switch pages in this session. Confirm to save your resolution.")
                    if st.button("💾 Confirm Resolution", key=f"btn_{eid}", type="primary"):
                        if decision.startswith("OK") or decision.startswith("MISMATCH"):
                            # Save to resolutions.json
                            res_map = {}
                            if RESOLUTIONS_FILE.exists():
                                try:
                                    res_map = json.loads(RESOLUTIONS_FILE.read_text(encoding="utf-8"))
                                except Exception:
                                    res_map = {}
                            dec_val = "OK" if decision.startswith("OK") else "MISMATCH"
                            res_map[eid] = {"decision": dec_val, "reviewer": "human_reviewer", "note": notes}
                            RESOLUTIONS_FILE.write_text(json.dumps(res_map, indent=2), encoding="utf-8")
                            st.success(f"Resolution saved for {eid} ({dec_val})!")
                        else:
                            st.warning("Please choose a valid resolution action.")

# ==============================================================================
# VIEW 4: 🔍 COMPARISON EXPLORER
# ==============================================================================
elif nav_selection == "🔍 Comparison Explorer":
    st.markdown("""
    <div class="dashboard-header">
        <h1>🔍 Comparison Explorer</h1>
        <p>Browse, filter, and inspect verified shipping documents and discrepancy reasons.</p>
    </div>
    """, unsafe_allow_html=True)

    c_search, c_filter = st.columns([3, 1])
    with c_search:
        search_query = st.text_input("Search shipments", placeholder="e.g. email_004 or shipment keywords…", autocomplete="off", label_visibility="visible")
    with c_filter:
        fltr = st.selectbox("Status Filter", ["ALL", "MISMATCH", "OK", "NEEDS_REVIEW", "NOT_COMPARED"], format_func=lambda value: {"ALL": "All statuses", "OK": "Match", "MISMATCH": "Mismatch", "NEEDS_REVIEW": "Needs review", "NOT_COMPARED": "Not compared"}[value], label_visibility="visible")

    filtered_items = []
    for item in report_data:
        norm_status = report_status(item)
        if fltr != "ALL" and norm_status != fltr:
            continue

        eid = item.get("email_id") or ""
        subj = item.get("subject") or ""
        if search_query:
            q = search_query.lower()
            if q not in eid.lower() and q not in subj.lower():
                continue

        filtered_items.append((item, norm_status))

    st.caption(f"Showing **{len(filtered_items)}** shipments matching criteria")

    if not filtered_items:
        st.info("No shipments match your search. Try another email ID or choose All statuses.")
    filter_signature = (search_query, fltr)
    if st.session_state.get("explorer_filter") != filter_signature:
        st.session_state["explorer_page"] = 1
        st.session_state["explorer_filter"] = filter_signature
    page = st.number_input("Results page", min_value=1, max_value=max(1, (len(filtered_items) + 19) // 20), step=1, key="explorer_page")
    start = (page - 1) * 20
    st.caption(f"Results {min(start + 1, len(filtered_items))}–{min(start + 20, len(filtered_items))} of {len(filtered_items)}")
    for item, norm_status in filtered_items[start:start + 20]:
        eid = item.get("email_id") or "Unknown"
        subj = item.get("subject") or "No Subject"
        defect_fields = item.get("defect_fields") or []
        fields = item.get("fields") or []

        badge_cls = "badge-ok" if norm_status == "OK" else ("badge-mismatch" if norm_status == "MISMATCH" else "badge-review" if norm_status == "NEEDS_REVIEW" else "badge-neutral")

        with st.expander(f"**{eid}** — {subj[:70]} ({norm_status})"):
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"**Subject:** `{subj}`")
            c2.markdown(f'<span class="badge {badge_cls}">{norm_status}</span>', unsafe_allow_html=True)

            if defect_fields:
                st.error(f"Discrepancies found in: **{', '.join(defect_fields)}**")

            # Show fields table
            if fields:
                st.markdown(comparison_table(fields), unsafe_allow_html=True)

            with st.expander("Raw record JSON"):
                st.json(item)

# ==============================================================================
# VIEW 5: 📈 INTENT DISTRIBUTION
# ==============================================================================
elif nav_selection == "📈 Intent Distribution":
    st.markdown("""
    <div class="dashboard-header">
        <h1>📈 Ingested Email Intents</h1>
        <p>Operational classification breakdown across incoming shipping correspondence.</p>
    </div>
    """, unsafe_allow_html=True)

    c_chart, c_table = st.columns([3, 2])

    with c_chart:
        with st.container(border=True):
            st.markdown("## 📊 Distribution Chart")
            if intents:
                st.bar_chart(intents)
            else:
                st.info("No email categories to display yet.")

    with c_table:
        with st.container(border=True):
            st.markdown("## 📋 Breakdown Summary")
            if intents:
                st.markdown(summary_table(intents), unsafe_allow_html=True)
            else:
                st.info("No email categories to display yet.")
