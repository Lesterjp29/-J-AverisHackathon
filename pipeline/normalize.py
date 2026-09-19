"""Canonical forms. Pure functions, no I/O. Everything here exists to separate
FORMATTING noise (case, punctuation, suffix style, units, thousands separators,
'PORT KLANG (WESTPORT)' vs 'PORT KLANG') from REAL differences."""
from __future__ import annotations

import re

# ---------------------------------------------------------------- names
_SUFFIX = [
    (r"\bLIMITED\b", "LTD"), (r"\bCOMPANY\b", "CO"), (r"\bCORPORATION\b", "CORP"),
    (r"\bINCORPORATED\b", "INC"), (r"\bSDN\.? ?BHD\.?\b", "SDN BHD"), (r"\bPRIVATE\b", "PTE"),
    (r"\bFZ[- ]?LLC\b", "FZLLC"), (r"\bL\.?L\.?C\.?\b", "LLC"),
]


def canon_name(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.upper().replace("&", " AND ")
    s = re.sub(r"[.,;:'\"`’]", " ", s)
    s = re.sub(r"[-_/]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for pat, rep in _SUFFIX:
        s = re.sub(pat, rep, s)
    return re.sub(r"\s+", " ", s).strip()


def squash(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


# ---------------------------------------------------------------- ports
PORT_ALIASES = {
    "PORT KELANG": "PORT KLANG", "PELABUHAN KLANG": "PORT KLANG", "PKG": "PORT KLANG",
    "HO CHI MINH": "HOCHIMINH CITY", "HO CHI MINH CITY": "HOCHIMINH CITY", "HOCHIMINH": "HOCHIMINH CITY",
    "HCMC": "HOCHIMINH CITY", "PYONGTAEK": "PYEONGTAEK", "PUSAN": "BUSAN", "TUTICORIN": "TUTICORIN",
    "THOOTHUKUDI": "TUTICORIN", "JAWAHARLAL NEHRU": "NHAVA SHEVA", "NHAVASHEVA": "NHAVA SHEVA",
}
COUNTRY_ALIASES = {"US": "USA", "UNITED STATES": "USA", "U S A": "USA", "UAE": "UAE",
                   "UNITED ARAB EMIRATES": "UAE", "KOREA": "SOUTH KOREA", "REPUBLIC OF KOREA": "SOUTH KOREA"}


def canon_port(s: str | None) -> tuple[str | None, str | None]:
    """-> (place, country). Drops UN/LOCODE '(CNNTG)', terminal '(WESTPORT)', spacing, case."""
    if s is None:
        return None, None
    s = s.upper()
    s = re.sub(r"\([^)]*\)", " ", s)                 # (CNNTG) (WESTPORT)
    s = re.sub(r"\s+", " ", s).strip(" ,")
    if "," in s:
        place, country = [x.strip() for x in s.rsplit(",", 1)]
    else:
        place, country = s, None
    place = PORT_ALIASES.get(place, place)
    if country:
        country = COUNTRY_ALIASES.get(country, country)
    return place, country


# ---------------------------------------------------------------- numbers
_WORDS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8,
          "NINE": 9, "TEN": 10, "ELEVEN": 11, "TWELVE": 12, "FIFTEEN": 15, "TWENTY": 20}


def parse_container_count(s: str | None) -> int | None:
    """'6 x 40'HC' -> 6 ; '3 x 20'FCL' -> 3 ; 'THREE' -> 3.  Multi-type strings are summed
    ('2 x 40HC + 1 x 20GP' -> 3)."""
    if not s:
        return None
    u = s.upper()
    pairs = re.findall(r"(\d+)\s*[X×]", u)
    if pairs:
        return sum(int(p) for p in pairs)
    m = re.match(r"\s*(\d+)\b", u)
    if m:
        return int(m.group(1))
    for w, n in _WORDS.items():
        if re.match(rf"\s*{w}\b", u):
            return n
    return None


def parse_weight_kg(s: str | None) -> float | None:
    """'131,058 KG' / '131058' / '128.544 KG' (thousands dot, incl. OCR comma->dot) / '22.5 MT' -> kg."""
    if not s:
        return None
    u = s.upper().strip()
    m = re.search(r"(\d[\d.,\s]*)\s*(KGS?|MTS?|TONS?|TONNES?|T|LBS?)?\b", u)
    if not m:
        return None
    num, unit = m.group(1).strip().replace(" ", ""), (m.group(2) or "KG")
    num = num.rstrip(".,")
    if "," in num and "." in num:
        dec = "," if num.rfind(",") > num.rfind(".") else "."
        thou = "." if dec == "," else ","
        num = num.replace(thou, "").replace(dec, ".")
    elif "," in num:
        num = num.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", num) else num.replace(",", ".")
    elif "." in num:
        # '128.544' / '22.825' => thousands separator (three digits after the dot, KG-scale weights)
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", num) and unit in ("KG", "KGS"):
            num = num.replace(".", "")
    try:
        v = float(num)
    except ValueError:
        return None
    if unit.startswith(("MT", "TON", "TONNE")) or unit == "T":
        v *= 1000
    elif unit.startswith("LB"):
        v *= 0.45359237
    return round(v, 3)
