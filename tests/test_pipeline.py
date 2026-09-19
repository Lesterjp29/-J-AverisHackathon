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


def test_submission_shape_matches_sample():
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
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
