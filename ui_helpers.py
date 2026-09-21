"""UI helper utilities for HTML rendering, sanitization, and status reporting."""
import html


def text(val) -> str:
    """Format a value safely for HTML display, with '—' for None/empty."""
    if val is None or val == "":
        return "—"
    return html.escape(str(val))


def report_status(item: dict) -> str:
    """Normalize status for reports and tables."""
    cat = item.get("category", "")
    st = item.get("status")
    if not st and isinstance(item.get("result"), dict):
        st = item["result"].get("status")
    if not st:
        return "NOT_COMPARED" if cat != "BL_COMPARISON" else "UNKNOWN"
    st = str(st).upper()
    if "OK" in st or ("MATCH" in st and "MISMATCH" not in st):
        return "OK"
    if "MISMATCH" in st:
        return "MISMATCH"
    if "REVIEW" in st:
        return "NEEDS_REVIEW"
    return st


def comparison_table(fields: list[dict]) -> str:
    """Render the 7-field comparison table safely with HTML escaping."""
    field_labels = {
        "shipper": "Shipper",
        "consignee": "Consignee",
        "notify_party": "Notify Party",
        "port_of_loading": "Port of Loading (POL)",
        "port_of_discharge": "Port of Discharge (POD)",
        "container_count": "Container Count",
        "gross_weight_kg": "Gross Weight (kg)",
    }
    verdict_badges = {
        "match": '<span class="badge badge-ok">Match</span>',
        "mismatch": '<span class="badge badge-mismatch">Mismatch</span>',
        "missing": '<span class="badge badge-review">Missing</span>',
        "uncertain": '<span class="badge badge-neutral">Uncertain</span>',
    }

    rows = []
    for f in fields:
        raw_fn = f.get("field", "")
        fname = html.escape(field_labels.get(raw_fn, raw_fn.replace("_", " ").title()))
        verdict = f.get("verdict", "")
        badge = verdict_badges.get(verdict, html.escape(str(verdict)))

        # Check for AI advisory verdict or LLM fallback
        ai_sugg = f.get("llm_suggested_verdict")
        ai_reason = f.get("llm_reasoning")
        if ai_sugg:
            badge += f'<div style="margin-top:4px;"><span class="badge badge-neutral" title="{html.escape(str(ai_reason or ""))}"><small>🤖 AI: {html.escape(str(ai_sugg))}</small></span></div>'

        si_val = text(f.get("si"))
        bl_val = text(f.get("bl"))
        reason = html.escape(str(f.get("reason", "")))
        reason_cell = f'<span style="color:#64748B;font-size:12px">{reason}</span>' if reason else ""

        rows.append(f"<tr><td>{fname}</td><td>{si_val}</td><td>{bl_val}</td><td>{badge}</td><td>{reason_cell}</td></tr>")

    return (
        '<table class="field-table">\n'
        '<thead><tr><th>Field Name</th><th>Shipping Instruction (SI)</th><th>Draft Bill of Lading (BL)</th><th>Status</th><th>Evidence / Note</th></tr></thead>\n'
        f"<tbody>\n{''.join(rows)}\n</tbody>\n"
        '</table>'
    )


def summary_table(items) -> str:
    """Render a category summary breakdown table."""
    if isinstance(items, dict):
        total = sum(items.values()) or 1
        items = [(k, v, v / total * 100) for k, v in sorted(items.items(), key=lambda x: x[1], reverse=True)]
    rows = []
    for entry in items:
        if len(entry) == 3:
            cat, count, pct = entry
        elif len(entry) == 2:
            cat, count = entry
            pct = 0.0
        else:
            continue
        rows.append(f"<tr><td><strong>{html.escape(str(cat))}</strong></td><td>{count}</td><td>{pct:.1f}%</td></tr>")
    return (
        '<table class="field-table">\n'
        '<thead><tr><th>Category</th><th>Count</th><th>Share</th></tr></thead>\n'
        f"<tbody>\n{''.join(rows)}\n</tbody>\n"
        '</table>'
    )


def format_review_issue(reason: str, detail: str) -> tuple[str, str]:
    """Translate technical pipeline reasons into clear, human-understandable explanations."""
    import re

    reason = str(reason or "").strip()
    detail = str(detail or "").strip()

    doc_labels = {
        "commercial_invoice": "Commercial Invoice",
        "packing_list": "Packing List",
        "certificate_of_origin": "Certificate of Origin",
        "booking_confirmation": "Booking Confirmation",
        "customs_declaration": "Customs Declaration",
        "insurance_certificate": "Insurance Certificate",
    }

    if reason == "wrong_doc_type":
        found = []
        for k, v in doc_labels.items():
            if k in detail.lower():
                found.append(v)
        other_name = ", ".join(found) if found else "Non-BL Document"
        friendly_reason = f"Wrong Document Attached ({other_name})"
        friendly_detail = f"Carrier draft Bill of Lading (BL) is missing. The email attached a {other_name} instead of a draft BL."
        return friendly_reason, friendly_detail

    if reason == "missing_attachment":
        friendly_reason = "Missing Attachment"
        if "only 0" in detail.lower():
            friendly_detail = "No documents attached. Verification requires both a Shipping Instruction (SI) and draft Bill of Lading (BL)."
        elif "only 1" in detail.lower():
            friendly_detail = "Only one document attached. Verification requires both a Shipping Instruction (SI) and draft Bill of Lading (BL)."
        else:
            friendly_detail = "Incomplete document set attached for verification."
        return friendly_reason, friendly_detail

    if reason == "unreadable":
        friendly_reason = "Unreadable Document / Scan"
        if "could not be parsed" in detail.lower() or "corrupt" in detail.lower():
            fn = detail.split(":")[0] if ":" in detail else "Attachment"
            friendly_detail = f"{fn}: Document could not be opened or is corrupted. Please re-request a clean file."
        elif "ocr passes disagree" in detail.lower():
            fields_affected = [m.group(1).replace("_", " ").title() for m in re.finditer(r"([a-z_]+):\s*ocr passes disagree", detail, re.I)]
            f_str = ", ".join(fields_affected) if fields_affected else "values"
            friendly_detail = f"Scan ambiguity: Multiple OCR passes disagree on {f_str} due to image blur or noise."
        else:
            friendly_detail = detail
        return friendly_reason, friendly_detail

    if reason == "missing_value":
        friendly_reason = "Missing Field Information"
        parts = []
        for segment in detail.split(";"):
            segment = segment.strip()
            if ":" in segment:
                f, desc = segment.split(":", 1)
                f_clean = f.replace("_", " ").strip().title()
                desc_clean = desc.replace("blank/placeholder", "marked as blank/TBA").strip()
                parts.append(f"{f_clean} ({desc_clean})")
            else:
                parts.append(segment)
        friendly_detail = "; ".join(parts) if parts else detail
        return friendly_reason, friendly_detail

    return reason.replace("_", " ").title(), detail


