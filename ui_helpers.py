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


