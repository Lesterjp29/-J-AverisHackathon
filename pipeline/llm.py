"""Gemini-backed assistance for the three places the deterministic pipeline already
defers to a person: (1) classification when regex confidence is low, (2) field
extraction when a document's field comes back missing after label-regex + fuzzy
matching, (3) "uncertain" compare verdicts (OCR-pass disagreement / near-miss text).

Every function here is advisory only. Nothing in this file can turn a clean
match/mismatch/missing verdict into something else - compare.py's decisions for those
stay untouched, per the pipeline's own design ("only code compares"). If GEMINI_API_KEY
is unset, the google-genai package isn't installed, or any API call fails, every
function degrades to returning None - the rest of the pipeline then runs exactly as it
did with zero LLM involvement. Nothing here ever raises.

Uses the current `google-genai` SDK (`pip install google-genai`), not the deprecated
`google-generativeai` package - the old one talks to a legacy endpoint that doesn't serve
newer model generations.
"""
from __future__ import annotations

import json
import os
import re

_API_KEY = os.environ.get("GEMINI_API_KEY")
_MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
_ENABLED = bool(_API_KEY)

_client = None
if _ENABLED:
    try:
        from google import genai
        _client = genai.Client(api_key=_API_KEY)
    except Exception:
        _client = None
        _ENABLED = False


def _ask(prompt: str) -> str | None:
    """Low-level call. Returns raw text, or None on any failure - never raises."""
    if not _ENABLED or _client is None:
        return None
    try:
        resp = _client.models.generate_content(
            model=_MODEL_NAME, contents=prompt)
        return (resp.text or "").strip()
    except Exception:
        return None


def _parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "",
                     text.strip(), flags=re.M).strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return None


# ---------------------------------------------------------------- (1) classification fallback
CLASSIFY_CONF_THRESHOLD = 0.75
_CATEGORIES = ("BL_COMPARISON", "SI_REQUEST",
               "INVOICE_QUERY", "GENERAL", "SPAM")

_CLASSIFY_PROMPT = """You are triaging one email from a shipping operations inbox into exactly
one category: BL_COMPARISON, SI_REQUEST, INVOICE_QUERY, GENERAL, or SPAM.

BL_COMPARISON: asks to compare/check a Shipping Instruction (SI) against a draft Bill of
Lading (BL), or has SI/BL documents attached for checking.
SI_REQUEST: submits a brand-new shipping instruction (shipper/POL/etc given in the body).
INVOICE_QUERY: about invoices, billing, THC, detention/demurrage, or similar charges.
GENERAL: any other operational message, including "please send me the draft BL" (a request
to receive a document, not to compare one).
SPAM: phishing / scam / unsolicited marketing.

Email subject: {subject}
Email body:
{body}
Number of attachments: {n_att}

A rule-based classifier already guessed "{rule_category}" with confidence {rule_conf} because:
{rule_reason}

Return ONLY this JSON, nothing else:
{{"category": "<one of the five>", "confidence": <0-1 float>, "reason": "<one short sentence>"}}
"""


def llm_classify_fallback(email: dict, rule_result: dict) -> dict | None:
    """Call only when the rule-based classifier's own confidence is below threshold.
    Returns a dict shaped like classify()'s output (category/confidence/reason), or None
    if the LLM is unavailable, fails, or the caller's rule-based confidence was already
    high enough - in which case the caller should keep the rule-based result untouched."""
    if rule_result.get("confidence", 1.0) >= CLASSIFY_CONF_THRESHOLD:
        return None
    prompt = _CLASSIFY_PROMPT.format(
        subject=email.get("subject", ""), body=email.get("body", "")[:4000],
        n_att=len(email.get("attachments", [])),
        rule_category=rule_result.get("category"), rule_conf=rule_result.get("confidence"),
        rule_reason=rule_result.get("reason"))
    parsed = _parse_json(_ask(prompt))
    if not parsed or parsed.get("category") not in _CATEGORIES:
        return None
    try:
        conf = float(parsed.get("confidence", 0.6))
    except (TypeError, ValueError):
        conf = 0.6
    return {"category": parsed["category"], "confidence": conf,
            "reason": f"LLM fallback: {parsed.get('reason', '')}",
            "rule_based_guess": rule_result.get("category")}


# ---------------------------------------------------------------- (2) extraction fallback
_EXTRACT_PROMPT = """Read the shipping document text below and find the value for exactly one
field: "{field_label}".

Documents label this field inconsistently (e.g. "Port of Loading" / "Load Port" / "POL";
"No. of Containers" / "Container Count"). Find it by MEANING, not exact header text. Do not
confuse "Net Weight" with "Gross Weight". If the field is genuinely absent, or blank/a
placeholder (N/A, TBA, blank line, underscores), say so.

Document text:
{text}

Return ONLY this JSON, nothing else:
{{"value": "<the field's value as written, or null if absent/blank>", "confidence": <0-1 float>}}
"""

_FIELD_LABELS = {
    "shipper": "Shipper", "consignee": "Consignee", "notify_party": "Notify Party",
    "port_of_loading": "Port of Loading", "port_of_discharge": "Port of Discharge",
    "container_count": "Number of Containers", "gross_weight_kg": "Gross Weight (kg)",
}


def llm_extract_fallback(field_name: str, lines: list[str]) -> dict | None:
    """Call only for a field extract.py's label-regex + fuzzy matcher came back with None
    for. Returns {"value": str, "confidence": float} or None. This never overrides a field
    extract.py already found - regex label-matching is exact and stays preferred whenever
    it succeeds; the LLM only fills gaps it left behind."""
    label = _FIELD_LABELS.get(field_name)
    if not label:
        return None
    prompt = _EXTRACT_PROMPT.format(
        field_label=label, text="\n".join(lines)[:6000])
    parsed = _parse_json(_ask(prompt))
    if not parsed or not parsed.get("value"):
        return None
    try:
        conf = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    return {"value": str(parsed["value"]).strip(), "confidence": conf,
            "source": "llm_extract_fallback"}


# ---------------------------------------------------------------- (3) "uncertain" compare fallback
_UNCERTAIN_PROMPT = """Two OCR passes (or a near-miss text comparison) disagree on the value of
one shipping-document field: "{field_label}".

SI evidence line: {si_evidence}
SI reading: {si_val}

BL evidence line: {bl_evidence}
BL reading: {bl_val}

Reason the system could not decide on its own: {reason}

Based only on the evidence given, do you think the SI and BL values represent the SAME
underlying value (this is OCR noise, not a real difference) or a GENUINELY DIFFERENT value?
Be conservative: say "different" whenever there is real doubt.

Return ONLY this JSON, nothing else:
{{"verdict": "same" | "different" | "cannot_tell", "reasoning": "<one short sentence>"}}
"""


def llm_resolve_uncertain(field_result, si_evidence: str, bl_evidence: str) -> dict | None:
    """Call only for a FieldResult with verdict == "uncertain" - never for match/mismatch/
    missing, which stay entirely governed by compare.py. This is advisory: it returns a
    suggestion to attach to the review queue for a human reviewer. It never changes
    field_result.verdict itself, and a person still makes the final call."""
    if field_result.verdict != "uncertain":
        return None
    label = _FIELD_LABELS.get(field_result.field, field_result.field)
    prompt = _UNCERTAIN_PROMPT.format(
        field_label=label, si_evidence=si_evidence or "(none)", si_val=field_result.si,
        bl_evidence=bl_evidence or "(none)", bl_val=field_result.bl, reason=field_result.reason)
    parsed = _parse_json(_ask(prompt))
    if not parsed or parsed.get("verdict") not in ("same", "different", "cannot_tell"):
        return None
    return {"llm_suggested_verdict": parsed["verdict"],
            "llm_reasoning": parsed.get("reasoning", ""),
            "llm_note": "advisory only - a person still confirms this field"}
