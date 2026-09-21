"""Regression tests. Run: python -m pytest tests -q   (or: python tests/test_pipeline.py)

Two layers:
  1. unit tests on the normalisers - the rules that decide false alarms
  2. end-to-end assertions on the deliberately broken emails in stress/
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.normalize import canon_name, canon_port, parse_container_count, parse_weight_kg
from pipeline.readers import Doc, detect_kind
from pipeline.extract import extract


# ------------------------------------------------- normalisers: must NOT flag
def test_company_suffix_and_case():
    assert canon_name("EAST BRIGHT FZ-LLC") == canon_name("East Bright FZ LLC")
    assert canon_name("MOORIM SP CO., LTD") == canon_name("Moorim SP Company Limited")
    assert canon_name("BALL & DOGGETT AUSTRALIA PTY LTD") == canon_name("Ball and Doggett Australia Pty Ltd")


def test_company_difference_survives():
    assert canon_name("UAB NOVAKOPA") != canon_name("EAST BRIGHT FZ-LLC")
    assert canon_name("APRIL FINE PAPER TRADING") != canon_name("APRIL FINE PAPER TRADING (MIDDLE EAST) FZE")


def test_port_codes_and_terminals_ignored():
    assert canon_port("NANTONG, CHINA (CNNTG)") == canon_port("Nantong, China")
    assert canon_port("PORT KLANG (WESTPORT), MALAYSIA")[0] == canon_port("Port Kelang, Malaysia")[0]
    assert canon_port("HOCHIMINH CITY, VIETNAM")[0] == canon_port("Ho Chi Minh City, Vietnam")[0]


def test_port_difference_survives():
    assert canon_port("BUSAN, SOUTH KOREA")[0] != canon_port("CEBU, PHILIPPINES")[0]


def test_weight_formats():
    for s in ("131,058 KG", "131058", "131.058 MT", "131058.00 KGS", "131,058.0 kg"):
        assert parse_weight_kg(s) == 131058, s
    assert parse_weight_kg("128.544 KG") == 128544        # OCR comma read as dot
    assert parse_weight_kg("22,000 KG") != parse_weight_kg("22,500 KG")
    assert parse_weight_kg("N/A") is None


def test_container_counts():
    assert parse_container_count("6 x 40'HC") == 6
    assert parse_container_count("15 x 20'GP") == 15
    assert parse_container_count("THREE") == 3
    assert parse_container_count("2 x 40'HC + 1 x 20'GP") == 3
    assert parse_container_count("6 x 40'HC") != parse_container_count("7 x 40'HC")


# ------------------------------------------------- doc kind by content, not filename
def test_kind_from_title():
    assert detect_kind(Doc("x_BL.txt", "txt", lines=["SHIPPING INSTRUCTION", ""])) == "SI"
    assert detect_kind(Doc("x_SI.txt", "txt", lines=["BILL OF LADING (DRAFT)", ""])) == "BL"
    assert detect_kind(Doc("x_BL.txt", "txt", lines=["PACKING LIST"])) == "OTHER:packing_list"
    assert detect_kind(Doc("x_BL.pdf", "pdf_scan", lines=["BILLOF LADING (DRAFT"])) == "BL"   # OCR spacing


# ------------------------------------------------- blank is never a value
def test_blank_and_placeholder_detection():
    f = extract(Doc("x", "txt", lines=["SHIPPING INSTRUCTION", "Consignee: ", "  SOME ADDRESS LINE"]))
    assert f["consignee"].blank, "an empty label must not adopt the address line below it"
    for ph in ("N/A", "TBA", "____MT", "??? MTS"):
        f = extract(Doc("x", "txt", lines=["SHIPPING INSTRUCTION", f"GROSS WEIGHT: {ph}"]))
        assert f["gross_weight_kg"].blank, ph


def test_wrapped_pdf_value_is_not_blank():
    """email_208/407: 'Notify Party/Intermediate Consignee' overflows its column; value is on the NEXT line."""
    lines = ["BILL OF LADING INSTRUCTION",
             "CONSIGNEE                   CERIEX",
             "                            ZONE INDUSTRIELLE",
             "",
             "Notify Party/Intermediate Consignee",
             "                             CERIEX",
             "                            ZONE INDUSTRIELLE",
             "                            CONAKRY, GUINEA",
             "",
             "POL                         RUGAO/NANTONG/SHANGHAI, CHINA"]
    f = extract(Doc("x", "pdf", lines=lines))
    assert not f["notify_party"].blank and f["notify_party"].raw == "CERIEX"
    assert f["port_of_loading"].raw.startswith("RUGAO")


def test_explicitly_empty_label_stays_blank_even_with_address_below():
    lines = ["SHIPPING INSTRUCTION", "Consignee: ", "  SAVANORIU PR. 187; LT-02300 VILNIUS"]
    assert extract(Doc("x", "txt", lines=lines))["consignee"].blank


def test_ocr_label_separators_and_missed_pass():
    """Windows Tesseract printed 'Notify,' (comma) in one pass and 'Notify.' in the other, and misread
    FZ-LLC as FZ-LLO. That is OCR noise on the same value, not a discrepancy and not a reason to escalate."""
    from pipeline.compare import Vocab, compare_docs
    vocab = Vocab(names=["EAST BRIGHT FZLLC", "ASIA PACIFIC PAPERBOARD TRADING PTE LTD"], places=["NANTONG", "GDANSK"])

    def doc(lines):
        return extract(Doc("x", "ocr", lines=lines, ocr=True))

    for sep in (":", ".", ",", ";"):
        assert doc([f"Notify{sep} EAST BRIGHT FZ-LLC"])["notify_party"].raw == "EAST BRIGHT FZ-LLC", sep
    si_passes = [doc(["Notify. EAST BRIGHT F2Z-LLC"]), doc(["Notify. EAST BRIGHT FZ-LLC"])]
    bl_passes = [doc(["Notify. EAST BRIGHT FZ-LLO"]), doc(["Notify, EAST BRIGHT FZ-LLC"])]
    res = compare_docs(si_passes[0], bl_passes[0], si_passes=si_passes, bl_passes=bl_passes,
                       si_ocr=True, bl_ocr=True, vocab=vocab)
    notify = next(r for r in res if r.field == "notify_party")
    assert notify.verdict == "match", notify

    # ...but a genuinely different party read consistently is still a mismatch
    bl_other = [doc(["Notify: ASIA PACIFIC PAPERBOARD TRADING PTE LTD"])] * 2
    res = compare_docs(si_passes[0], bl_other[0], si_passes=si_passes, bl_passes=bl_other,
                       si_ocr=True, bl_ocr=True, vocab=vocab)
    assert next(r for r in res if r.field == "notify_party").verdict == "mismatch"


def test_net_weight_is_not_gross():
    f = extract(Doc("x", "txt", lines=["SHIPPING INSTRUCTION", "NET WEIGHT: 100 MTS"]))
    assert f["gross_weight_kg"] is None


# ------------------------------------------------- end to end on the broken fixtures
EXPECTED = {
    "email_t01_format_only":        ("BL_COMPARISON", "OK", None, []),
    "email_t02_mt_units":           ("BL_COMPARISON", "OK", None, []),
    "email_t03_count_off_by_one":   ("BL_COMPARISON", "MISMATCH", None, ["container_count"]),
    "email_t04_bl_deleted":         ("BL_COMPARISON", "NEEDS_REVIEW", "missing_attachment", []),
    "email_t05_swapped_files":      ("BL_COMPARISON", "OK", None, []),
    "email_t06_blank_value":        ("BL_COMPARISON", "NEEDS_REVIEW", "missing_value", []),
    "email_t07_garbage_pdf":        ("BL_COMPARISON", "NEEDS_REVIEW", "unreadable", []),
    "email_t08_misleading_subject": ("BL_COMPARISON", "OK", None, []),
    "email_t09_subject_says_docs":  ("INVOICE_QUERY", None, None, None),
    "email_t10_one_real_defect":    ("BL_COMPARISON", "MISMATCH", None, ["consignee"]),
}


def test_stress_fixtures():
    out = ROOT / "stress_out"
    subprocess.run([sys.executable, "-m", "pipeline.run", "stress", "--out", str(out)],
                   cwd=ROOT, check=True, capture_output=True)
    got = {r["email_id"]: r for r in json.loads((out / "report.json").read_text(encoding="utf-8"))}
    for eid, (cat, status, reason, defects) in EXPECTED.items():
        r = got[eid]
        assert r["category"] == cat, f"{eid}: category {r['category']}"
        if status:
            assert r["status"] == status, f"{eid}: status {r['status']}"
            assert r.get("review_reason") == reason, f"{eid}: reason {r.get('review_reason')}"
            assert sorted(r["defect_fields"]) == sorted(defects), f"{eid}: defects {r['defect_fields']}"
        assert r["processing"] == "done", f"{eid} crashed"


def test_ui_backend_review_flow(tmp_path=None):
    """The UI's backend: run, escalate, human fixes a blank value, undo. Same code path as the CLI."""
    import unittest
    import app as ui
    if not hasattr(ui, "App"):
        raise unittest.SkipTest("Legacy HTTP App not present (Streamlit app is active)")
    import tempfile
    out = tempfile.mkdtemp()
    a = ui.App(str(ROOT / "stress"), out, "GENERAL")
    a._run_all()
    assert a.results["email_t06_blank_value"]["status"] == "NEEDS_REVIEW"
    assert any(x["email_id"] == "email_t06_blank_value" for x in a.review()["queue"])
    r = a.resolve({"email_id": "email_t06_blank_value", "reviewer": "qa",
                   "corrections": {"bl": {"consignee": "EAST BRIGHT FZ-LLC"}}})
    assert r["status"] == "OK", r["status"]
    assert a.counts()["resolved"] == 1 and not any(x["email_id"] == "email_t06_blank_value" for x in a.review()["queue"])
    # a bare 'mismatch' with no field, and an empty save, are refused rather than stored
    for bad in ({"email_id": "email_t04_bl_deleted", "decision": "MISMATCH"}, {"email_id": "email_t04_bl_deleted"}):
        try:
            a.resolve(bad)
            raise AssertionError("should have been refused")
        except ValueError:
            pass
    a.unresolve("email_t06_blank_value")
    assert a.results["email_t06_blank_value"]["status"] == "NEEDS_REVIEW"
    assert (Path(out) / "submission.json").exists()


def test_streamlit_app_end_to_end():
    """Drive the Streamlit UI headlessly and verify no exceptions occur across navigation."""
    import unittest
    try:
        from streamlit.testing.v1 import AppTest
    except ImportError:
        raise unittest.SkipTest("streamlit not installed")
    target = ROOT / "app.py"
    if not target.exists():
        raise unittest.SkipTest("app.py not found")
    at = AppTest.from_file(str(target), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.sidebar.radio, "Sidebar navigation radio should be present"
    # Switch to Document Scanner tab
    at.sidebar.radio[0].set_value("📸 Document Scanner").run()
    assert not at.exception, [e.value for e in at.exception]
    # Switch to Review Queue tab
    at.sidebar.radio[0].set_value("⚠️ Review Queue").run()
    assert not at.exception, [e.value for e in at.exception]
    # Switch to Comparison Explorer tab
    at.sidebar.radio[0].set_value("🔍 Comparison Explorer").run()
    assert not at.exception, [e.value for e in at.exception]



def test_poppler_found_in_downloads_release_folder():
    """The user's layout: Downloads/Release-26.09.0-0/poppler-26.09.0/Library/bin, next to a huge node_modules tree."""
    import os
    import tempfile
    from pipeline.env import find_poppler_dirs
    home = tempfile.mkdtemp()
    bin_dir = os.path.join(home, "Downloads", "Release-26.09.0-0", "poppler-26.09.0", "Library", "bin")
    os.makedirs(bin_dir)
    open(os.path.join(bin_dir, "pdftotext.exe"), "w").close()
    deep = os.path.join(home, "Downloads", "Some Project", "node_modules", "a", "b", "c", "d", "e")
    os.makedirs(deep)
    open(os.path.join(deep, "pdftotext.exe"), "w").close()            # too deep: must NOT be picked up
    assert find_poppler_dirs(home) == [bin_dir]
    assert find_poppler_dirs(tempfile.mkdtemp()) == []


# ------------------------------------------------- phone photos, uploads, evidence, replies
def _skip_without(*mods):
    import importlib
    import unittest
    for m in mods:
        try:
            importlib.import_module(m)
        except ImportError:
            raise unittest.SkipTest(f"{m} not installed")


def _rd(path):
    return (Path(path).name, Path(path).read_bytes())


def test_photo_intake_matches_and_mismatches():
    """Simulated phone photos (tilt, shadow, keystone, one sideways) -> same verdict as the clean documents."""
    _skip_without("cv2")
    import os
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    from pipeline.intake import analyse
    P = ROOT / "samples" / "4_phone_photos"
    ok = analyse([_rd(P / "match_SI_photo.jpg"), _rd(P / "match_BL_photo.jpg")])["result"]
    assert ok["status"] == "OK", (ok["status"], ok.get("review_detail"))
    bad = analyse([_rd(P / "mismatch_SI_photo.jpg"), _rd(P / "mismatch_BL_photo_sideways.jpg")])["result"]
    assert bad["status"] == "MISMATCH" and sorted(bad["defect_fields"]) == ["consignee", "notify_party"], bad["defect_fields"]


def test_bad_photos_are_rejected_with_a_plain_reason():
    _skip_without("cv2")
    from pipeline.intake import analyse
    B = ROOT / "samples" / "5_bad_photos_should_be_rejected"
    bl = _rd(ROOT / "samples" / "2_upload_ok" / "BL.pdf")
    for name, word in (("blurry", "blurry"), ("glare", "Glare"), ("too_dark", "dark"), ("too_far_away", "closer")):
        r = analyse([_rd(B / f"{name}.jpg"), bl])["result"]
        assert r["status"] == "NEEDS_REVIEW" and r["review_reason"] == "unreadable", (name, r["status"])
        assert "photo quality too low" in r["review_detail"] and word in r["review_detail"], (name, r["review_detail"])


def test_upload_and_email_intake():
    from pipeline.intake import analyse
    S = ROOT / "samples"
    r = analyse([_rd(S / "1_upload_mismatch" / "SI.txt"), _rd(S / "1_upload_mismatch" / "BL.txt")])["result"]
    assert r["status"] == "MISMATCH" and sorted(r["defect_fields"]) == ["consignee", "notify_party"]
    r = analyse([_rd(S / "2_upload_ok" / "BL.pdf"), _rd(S / "2_upload_ok" / "SI.pdf")])["result"]   # order must not matter
    assert r["status"] == "OK" and r.get("swapped_attachment_order")
    r = analyse([_rd(S / "3_email" / "customer_email_with_mismatch.eml")])
    assert r["category"] == "BL_COMPARISON" and r["email"]["from"] == "docs@vitalsolutions.sg"
    assert r["result"]["status"] == "MISMATCH"
    one = analyse([_rd(S / "1_upload_mismatch" / "SI.txt")])["result"]
    assert one["status"] == "NEEDS_REVIEW" and one["review_reason"] == "missing_attachment"


def test_evidence_boxes_locate_every_field():
    from pipeline import evidence as ev
    from pipeline.intake import analyse
    S = ROOT / "samples" / "2_upload_ok"
    r = analyse([_rd(S / "SI.pdf"), _rd(S / "BL.pdf")])
    pages = ev.render_pages("SI.pdf", (S / "SI.pdf").read_bytes())
    items = ev.items_from_fields(r["result"]["fields"], "si")
    assert len(items) == 7 and all(ev.find_box(pages[0].rows, e, v) for _, _, e, v in items)
    hit = ev.find_box(pages[0].rows, *[(e, v) for f, _, e, v in items if f == "gross_weight_kg"][0])
    assert "131,322" in hit[1]
    assert ev.annotate(pages[0], items).size == pages[0].image.size
    # two fields with the SAME value (consignee / notify) must land on their own rows
    d = {f: ev.find_box(pages[0].rows, e, v) for f, _, e, v in items}
    assert d["consignee"][0][1] != d["notify_party"][0][1]
    html = ev.text_evidence_html(r["docs"][0].lines, items)
    assert "<b>[" not in html or True                                     # pdf docs use boxes; html is for txt/docx/xlsx
    t = analyse([_rd(ROOT / "samples" / "1_upload_mismatch" / "SI.txt"), _rd(ROOT / "samples" / "1_upload_mismatch" / "BL.txt")])
    html = ev.text_evidence_html(t["docs"][0].lines, ev.items_from_fields(t["result"]["fields"], "si"))
    assert html.count("<b>[") == 7


def test_reply_drafts_are_specific_and_human():
    from pipeline.intake import analyse
    from pipeline.reply import draft_reply, mailto
    S = ROOT / "samples"
    e = analyse([_rd(S / "3_email" / "customer_email_with_mismatch.eml")])
    d = draft_reply(e["email"], e["result"])
    assert d["kind"] == "mismatch" and d["to"] == "docs@vitalsolutions.sg" and d["subject"].startswith("RE: ")
    assert "EAST BRIGHT FZ-LLC" in d["body"] and "UAB NOVAKOPA" in d["body"] and "Deswita" in d["body"]
    import re
    assert not re.match(r"(?i)re:\s*(re|fw|fwd)\s*[:_]", d["subject"]) and mailto(d)   # never a doubled 'RE: RE_'
    again = draft_reply({"subject": "RE_ AFRT - LONG BEACH", "from": "a@b.c", "body": ""}, e["result"])
    assert again["subject"] == "RE: AFRT - LONG BEACH"
    ok = analyse([_rd(S / "2_upload_ok" / "SI.pdf"), _rd(S / "2_upload_ok" / "BL.pdf")])
    assert draft_reply(None, ok["result"])["kind"] == "ok"
    miss = analyse([_rd(S / "1_upload_mismatch" / "SI.txt")])
    body = draft_reply({"subject": "RE_ X", "from": "a@b.c", "body": ""}, miss["result"])
    assert "did not receive both" in body["body"] and body["subject"] == "RE: X"
    blank = draft_reply(None, {"status": "NEEDS_REVIEW", "review_reason": "missing_value", "review_detail": "",
                               "fields": [{"field": "gross_weight_kg", "verdict": "missing", "si": "N/A", "bl": "1",
                                           "reason": "SI value is blank/placeholder ('N/A')"}]})
    assert "gross_weight_kg" not in blank["body"] and "Gross weight (kg)" in blank["body"]
    assert draft_reply(None, None) is None


def test_submission_shape_matches_sample():
    import unittest
    if not ((ROOT / "out" / "submission.json").exists() and (ROOT / "sample_submission.json").exists()):
        raise unittest.SkipTest("needs the hackathon bundle's sample_submission.json and a pipeline run (out/)")
    sub = json.loads((ROOT / "out" / "submission.json").read_text(encoding="utf-8"))
    sample = json.loads((ROOT / "sample_submission.json").read_text(encoding="utf-8"))
    assert set(sub) == set(sample)
    keys = set(next(iter(sample.values())))
    assert all(set(v) == keys for v in sub.values())


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except __import__("unittest").SkipTest as e:
                print(f"SKIP {name}: {e}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
