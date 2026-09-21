"""Doc -> the seven comparison fields, aligned by MEANING not by header text.

Each field returns a Field(raw, lines, blank, evidence). Nothing is guessed:
if a label is absent the field is None; if it is present but empty/placeholder
(N/A, TBA, ____MT ...) it is blank=True. Compare/review decide what to do.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .readers import Doc, strip_cjk

FIELDS = ["shipper", "consignee", "notify_party", "port_of_loading",
          "port_of_discharge", "container_count", "gross_weight_kg"]

# label regexes per canonical field. Applied to the START of a line, case-insensitive.
# Order inside a group: longest first. `\s*` tolerates OCR dropping spaces ("Portof Loading").
_S = r"\s*"
LABELS: dict[str, str] = {
    "shipper": rf"shipper(?:{_S}\(principal{_S}or{_S}seller\))?(?:/exporter)?",
    "consignee": rf"(?:consignee(?:{_S}\(non-negotiable\))?|to{_S}the{_S}order{_S}of)",
    "notify_party": rf"notify(?:{_S}party)?(?:/intermediate{_S}consignee)?",
    "port_of_loading": rf"(?:port{_S}of{_S}loading(?:{_S}\(pol\))?|load{_S}port|pol\b)",
    "port_of_discharge": rf"(?:port{_S}of{_S}discharge(?:{_S}\(pod\))?|discharge{_S}port|pod\b)",
    "container_count": rf"(?:(?:total{_S})?no\.?{_S}of{_S}containers(?:{_S}or{_S}packages)?|total{_S}containers|container{_S}count|containers)",
    "gross_weight_kg": rf"(?:total{_S})?gross{_S}(?:weight|wt)(?:{_S}\(kgs?\))?",
}
_COMPILED = {k: re.compile(rf"^\s*{v}\s*(?:[:：]|\s{{2,}}|\s|$)\s*(.*)$", re.I) for k, v in LABELS.items()}
# explicitly NOT gross weight -> never confuse with the gross field
_NET = re.compile(r"^\s*net\s*(weight|wt)", re.I)

BLANK = re.compile(r"^\s*(?:n/?a|tba|tbc|tbd|nil|none|-+|_+.*|\?+.*|\.+|x+|unknown)\s*$|^_+|_{3,}|\?{3,}", re.I)


@dataclass
class Field:
    raw: str                              # first line as written
    block: list[str] = field(default_factory=list)   # all lines (name + address...)
    blank: bool = False
    evidence: str = ""                    # source line, for the review queue
    wrapped: bool = False                 # label line had no value AND no colon: PDF column overflow,
                                          # the value sits on the next line (vs "Label: " = truly empty)


def _indented(line: str) -> bool:
    return bool(line) and line[0] in " \t" and line.strip() != ""


_CANON_LABELS = {
    "shipper": ["shipper"], "consignee": ["consignee", "to the order of"], "notify_party": ["notify", "notify party"],
    "port_of_loading": ["port of loading", "load port", "pol"], "port_of_discharge": ["port of discharge", "discharge port", "pod"],
    "container_count": ["containers", "no of containers", "container count", "total containers"],
    "gross_weight_kg": ["gross weight", "gross wt"],
}


def _fuzzy_label(line: str):
    """OCR turns 'Shipper:' into 'Shipper.', 'Port of Loading' into 'Portof Leading' or 'FPortof Loading'.
    Split at the first ':' or '.' near the start and fuzzy-match the label text."""
    from rapidfuzz import fuzz
    # OCR renders the colon after a label as ':' '.' ',' ';' or '|' depending on engine version
    m = re.match(r"^\W?([A-Za-z][A-Za-z ]{1,28}?)\s*[:.,;|]\s*(.*)$", line)
    if not m:
        return None
    if re.match(r"\s*container\s*(no|num|#)", m.group(1), re.I):      # table column header, not the 'Containers' field
        return None
    lab = re.sub(r"[^a-z]", "", m.group(1).lower())
    best = (0, None)
    for name, alts in _CANON_LABELS.items():
        for a in alts:
            sc = fuzz.ratio(lab, re.sub(r"[^a-z]", "", a))
            if sc > best[0]:
                best = (sc, name)
    if best[0] >= 85:
        return best[1], m.group(2).strip()
    return None


def parse_fields(lines: list[str], ocr: bool = False) -> dict[str, Field | None]:
    out: dict[str, Field | None] = {k: None for k in FIELDS}
    cur: str | None = None
    for raw_line in lines:
        line = strip_cjk(raw_line).replace("\u00a0", " ")
        if not line.strip():
            cur = None
            continue
        if _NET.match(line):
            cur = None
            continue
        hit = None
        # a non-indented line may start a new field
        if not _indented(raw_line) or cur is None:
            for name, rx in _COMPILED.items():
                m = rx.match(line)
                if m and out[name] is None:     # first occurrence wins (header block, not table rows)
                    hit = (name, m.group(1).strip())
                    break
        if not hit and ocr and cur is None or (not hit and ocr and not _indented(raw_line)):
            fz = _fuzzy_label(line)
            if fz and out[fz[0]] is None:
                hit = fz
        if hit:
            name, val = hit
            wrapped = (val == "" and ":" not in line and "：" not in line)
            f = Field(raw=val, block=[val] if val else [], blank=(val == "" or bool(BLANK.search(val))),
                      evidence=raw_line.strip(), wrapped=wrapped)
            out[name] = f
            cur = name
            continue
        # continuation: indented line under a party field (address / "ON BEHALF OF")
        if cur in ("shipper", "consignee", "notify_party") and _indented(raw_line):
            f = out[cur]
            if f.wrapped and not f.raw:          # PDF: long label, value wrapped onto the next line
                f.raw = line.strip()
                f.block = [f.raw]
                f.blank = bool(BLANK.search(f.raw))
                f.evidence += " / " + f.raw
            elif f.raw and not f.blank:          # address / "ON BEHALF OF" lines under a real value
                f.block.append(line.strip())
            # else: "Label: " was explicitly empty -> stays blank; never adopt the address below it as the value
        elif cur in ("shipper", "consignee", "notify_party") and out[cur] and not out[cur].raw:
            pass
        else:
            cur = cur if _indented(raw_line) else None
    return out


def party_lines(f: Field) -> tuple[str, str]:
    """(name, on_behalf_of). Name = first line only; an 'ON BEHALF OF ...' line is kept apart
    because one side may omit it (SI: 'APRIL FINE PAPER TRADING' vs BL adds the principal)."""
    name = f.block[0] if f.block else f.raw
    behalf = ""
    for l in f.block[1:]:
        m = re.match(r"\s*on\s+behalf\s+of\s+(.*)", l, re.I)
        if m:
            behalf = m.group(1)
    # xlsx/txt flattened "NAME ON BEHALF OF X"
    m = re.match(r"(.*?)\s+on\s+behalf\s+of\s+(.*)", name, re.I)
    if m:
        name, behalf = m.group(1), m.group(2)
    return name.strip(), behalf.strip()


def extract(doc: Doc) -> dict[str, Field | None]:
    return parse_fields(doc.lines, ocr=doc.ocr or doc.fmt == "ocr")
