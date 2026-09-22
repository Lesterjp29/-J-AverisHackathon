"""Human-in-the-loop resolution.

resolutions.json (keyed by email_id) accepts two shapes:

  {"email_511": {"decision": "OK", "reviewer": "hari", "note": "opened original in Acrobat"}}
  {"email_517": {"decision": "MISMATCH", "defect_fields": ["port_of_loading"]}}
  {"email_516": {"corrections": {"si": {"gross_weight_kg": "235,550 KG"}}, "note": "confirmed with customer"}}

Corrections are re-compared by the same code path, so the report updates itself.
    python -m pipeline.run data/sample --out outputs/sample --resolutions outputs/resolutions.json
"""
from __future__ import annotations

from .extract import Field


def apply_corrections(si_fields: dict, bl_fields: dict, corrections: dict) -> None:
    for side, target in (("si", si_fields), ("bl", bl_fields)):
        for fn, val in (corrections.get(side) or {}).items():
            target[fn] = Field(raw=str(val), block=[str(val)], blank=False, evidence=f"human-entered: {val}")
