"""Field-by-field comparison with FOUR outcomes (not two):

  match      values agree after normalisation
  mismatch   values genuinely differ            -> defect
  missing    a value is absent / placeholder    -> needs a person (missing_value)
  uncertain  we cannot trust a reading (OCR passes disagree, near-miss text,
             document internally inconsistent) -> needs a person

The comparison itself is deterministic code. Nothing here asks a model
"do these match?" - models (or OCR) only *read*; code *decides*.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .extract import FIELDS, Field, party_lines
from .normalize import canon_name, canon_port, parse_container_count, parse_weight_kg, squash

WEIGHT_TOL_KG = 0.5
OCR_NEAR_MISS = 80          # OCR'd names this similar are "probably noise" -> uncertain, not mismatch


@dataclass
class FieldResult:
    field: str
    verdict: str                     # match | mismatch | missing | uncertain
    si: str | None = None
    bl: str | None = None
    reason: str = ""
    si_evidence: str = ""
    bl_evidence: str = ""

    def as_dict(self):
        return self.__dict__.copy()


# ---------------------------------------------------------------- vocabulary snapping (OCR only)
class Vocab:
    """Entities seen in born-digital documents. An OCR'd value is 'snapped' to the closest known
    entity when it is clearly closest (score >= 88 and 4+ points ahead of the runner-up). Genuinely
    different parties stay different, because they snap to a different entity."""

    def __init__(self, names=(), places=()):
        self.names = sorted({n for n in names if n})
        self.places = sorted({p for p in places if p}, key=len, reverse=True)

    def snap_name(self, c: str | None):
        if not c or not self.names:
            return c, False
        sq = squash(c)
        scored = sorted(((fuzz.ratio(sq, squash(n)), n) for n in self.names), reverse=True)
        best = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0
        if best[0] >= 88 and best[0] - second >= 4:
            return best[1], True
        return c, False

    def snap_place(self, c: str | None):
        if not c or not self.places:
            return c, False
        sq = squash(c)
        for p in self.places:                      # OCR often drops the comma: 'NHAVA SHEVA INDIA'
            if sq == squash(p) or sq.startswith(squash(p)) and len(squash(p)) >= 5:
                return p, True
        best = max(((fuzz.ratio(sq, squash(p)), p) for p in self.places), default=(0, None))
        return (best[1], True) if best[0] >= 85 else (c, False)


# ---------------------------------------------------------------- value normalisation
def norm_value(fn: str, f: Field, snap: "Vocab | None" = None):
    if fn in ("shipper", "consignee", "notify_party"):
        name, behalf = party_lines(f)
        n = canon_name(name)
        if snap:
            n, _ = snap.snap_name(n)
        return (n, canon_name(behalf) if behalf else None)
    if fn in ("port_of_loading", "port_of_discharge"):
        place, country = canon_port(f.raw)
        if snap:
            place, ok = snap.snap_place(place)
            if ok:
                country = None                    # OCR'd country text is unreliable once the place is known
        if country and place and squash(country) == squash(place):
            country = None                    # "SINGAPORE, SINGAPORE"
        return (place, country)
    if fn == "container_count":
        return parse_container_count(f.raw)
    return parse_weight_kg(f.raw)


def _consensus(fn: str, per_pass: list[Field | None], snap=None):
    """OCR: each pass must give the same normalised value, else the reading is untrustworthy."""
    # A pass that did not find the label at all is a missed read, not a disagreement: judge the passes
    # that DID read the field. (A pass that read it as blank/placeholder still counts, and will disagree.)
    seen = [f for f in per_pass if f is not None]
    if not seen:
        return None, True
    vals = [(norm_value(fn, f, snap) if not f.blank else None) for f in seen]
    first = next((f for f in seen if not f.blank), seen[0])
    return first, len({repr(v) for v in vals}) == 1


def _show(fn: str, f: Field | None) -> str | None:
    if f is None:
        return None
    if fn in ("shipper", "consignee", "notify_party"):
        return party_lines(f)[0] or f.raw
    return f.raw


# ---------------------------------------------------------------- single field
def compare_field(fn: str, si: Field | None, bl: Field | None, *, si_ocr=False, bl_ocr=False,
                  si_stable=True, bl_stable=True, vocab: "Vocab | None" = None) -> FieldResult:
    r = FieldResult(fn, "match", _show(fn, si), _show(fn, bl),
                    si_evidence=si.evidence if si else "", bl_evidence=bl.evidence if bl else "")
    for side, f in (("SI", si), ("BL", bl)):
        if f is None:
            r.verdict, r.reason = "missing", f"{side} has no '{fn}' field"
            return r
        if f.blank:
            r.verdict, r.reason = "missing", f"{side} value is blank/placeholder ({f.raw!r})"
            return r
    if not (si_stable and bl_stable):
        who = "SI" if not si_stable else "BL"
        r.verdict, r.reason = "uncertain", f"OCR passes disagree on the {who} value"
        return r

    a = norm_value(fn, si, vocab if si_ocr else None)
    b = norm_value(fn, bl, vocab if bl_ocr else None)
    ocr = si_ocr or bl_ocr

    if fn in ("shipper", "consignee", "notify_party"):
        (an, ab), (bn, bb) = a, b
        if an is None or bn is None:
            r.verdict, r.reason = "missing", "party name empty"
            return r
        if an == bn or squash(an) == squash(bn):
            if ab and bb and squash(ab) != squash(bb):
                r.verdict, r.reason = "mismatch", f"'on behalf of' party differs ({ab} vs {bb})"
            return r
        score = fuzz.ratio(squash(an), squash(bn))
        if ocr and score >= OCR_NEAR_MISS:
            r.verdict, r.reason = "uncertain", f"names differ slightly (similarity {score:.0f}) - could be OCR noise"
        else:
            r.verdict, r.reason = "mismatch", f"different party (similarity {score:.0f})"
        return r

    if fn in ("port_of_loading", "port_of_discharge"):
        (ap, ac), (bp, bc) = a, b
        if squash(ap or "") == squash(bp or ""):
            if ac and bc and squash(ac) != squash(bc):
                r.verdict, r.reason = "mismatch", f"same place name, different country ({ac} vs {bc})"
            return r
        score = fuzz.ratio(squash(ap or ""), squash(bp or ""))
        if ocr and score >= OCR_NEAR_MISS:
            r.verdict, r.reason = "uncertain", f"port names differ slightly (similarity {score:.0f}) - could be OCR noise"
        else:
            r.verdict, r.reason = "mismatch", "different port"
        return r

    if a is None or b is None:
        r.verdict, r.reason = "missing", "could not parse a number"
        return r
    same = (a == b) if fn == "container_count" else abs(a - b) <= WEIGHT_TOL_KG
    if not same:
        r.verdict, r.reason = "mismatch", f"{a:g} vs {b:g}"
    return r


# ---------------------------------------------------------------- documents
def compare_docs(si_fields: dict, bl_fields: dict, *, si_passes=None, bl_passes=None,
                 si_ocr=False, bl_ocr=False, vocab: "Vocab | None" = None) -> list[FieldResult]:
    out = []
    for fn in FIELDS:
        s, b = si_fields[fn], bl_fields[fn]
        s_ok = b_ok = True
        if si_passes:
            s, s_ok = _consensus(fn, [p[fn] for p in si_passes], vocab)
        if bl_passes:
            b, b_ok = _consensus(fn, [p[fn] for p in bl_passes], vocab)
        out.append(compare_field(fn, s, b, si_ocr=si_ocr, bl_ocr=bl_ocr, si_stable=s_ok, bl_stable=b_ok, vocab=vocab))
    return out


def table_consistency(lines: list[str], fields: dict) -> str | None:
    """PDF container tables: number of container rows must equal the stated count."""
    rows = [l for l in lines if re.match(r"\s*[A-Z]{4}\d{7}\b", l)]
    if not rows or not fields.get("container_count") or fields["container_count"].blank:
        return None
    stated = parse_container_count(fields["container_count"].raw)
    if stated is not None and stated != len(rows):
        return f"document lists {len(rows)} container rows but states {stated}"
    return None
