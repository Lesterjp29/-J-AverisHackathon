# Shipping document verification — solution notes

## Document Dashboard

Run `python -m streamlit run app.py` from this project directory. The dashboard includes document uploads, sample comparisons, a review queue, and searchable batch results.

The layout adapts to the available content width. Wide comparison tables scroll inside a labeled, keyboard-focusable region; use the arrow keys when the table has focus. Review drafts survive page changes within the current session. Use **Confirm Resolution** to save them. Direct page links use `?page=overview`, `scanner`, `review`, `explorer`, or `distribution`.

UI regression checks: `python -m unittest discover -s tests -p test_ui.py`.

    python -m pipeline.run <data-dir | http://localhost:8080> --out out [--submit]
    python -m pipeline.run . --out out --resolutions resolutions.json     # apply human decisions
    python -m pipeline.run . --out out --retry email_511                  # re-run one email
    python -m pipeline.run . --out out_alt --ask-send-as BL_COMPARISON    # alternate labelling (see below)

Outputs: `report.md` (readable), `report.json` (evidence), `review_queue.json`, `submission.json` (scoreboard shape).
Install: `pip install -r requirements.txt`, plus system `poppler` and `tesseract` (on Windows see below).
Check tools any time with `python -m pipeline.env`. The pipeline refuses to run if poppler is missing, rather than
silently sending every PDF to review.

### Windows / VS Code
1. Put `inbox/`, `attachments/`, `sample_submission.json` from the bundle into this folder (`loader.py` is already here).
   Or leave them where they are and pass that folder: `python -m pipeline.run C:\path\to\bundle --out out`.
2. Install Tesseract (UB-Mannheim build, tick "add to PATH") and poppler (oschwartz10612/poppler-windows, add `Library\bin` to PATH,
   or set `POPPLER_PATH`). Restart VS Code. Verify: `python -m pipeline.env`.
3. `python -m pipeline.run . --out out` then `python tests/test_pipeline.py`.
Tests: `python tests/test_pipeline.py` — 13 tests (normaliser units + the 10 broken fixtures end to end).

## Design: code decides, readers only read
| Stage | How |
|---|---|
| Classify | Rules on **body + attachment content**. Subject is only a weak tiebreaker (subjects are reused across intents in the data). |
| Read | txt / text-PDF (`pdftotext -layout`) / docx (paragraphs **and** table cells) / xlsx / scanned PDF (OCR at 300 & 400 dpi). SI vs BL identified by **document title**, never filename. |
| Extract | Label regexes align "Load Port" / "POL" / "Port of Loading (POL)"; CJK glyph labels stripped; "Net Weight" never mistaken for gross. |
| Normalise | Names: case/punctuation/suffix/spacing. Ports: drop `(CNNTG)` codes and `(WESTPORT)` terminals. Weight: units + `131,058` / `131058` / `128.544`. Containers: `6 x 40'HC` -> 6. |
| Compare | Four outcomes: match / mismatch / missing / uncertain. Only *code* compares. |
| OCR safety | Two OCR passes must agree; OCR'd names/ports are snapped to entities seen in born-digital docs, so noise ("ALGUAG") resolves but a genuinely different party does not. |
| Human review | Missing/wrong/unreadable/blank/uncertain -> `review_queue.json` with reason, per-field SI/BL values and source lines. Reviewer supplies a decision or corrected values; the same compare code re-runs. |
| Failures | Exceptions are recorded on the email (`processing: FAILED`), routed to review, and retryable with `--retry`. |

## Judgement calls (record of reasons)
1. **"Please assist to send the draft BL for X for checking asap" (91 emails, no attachments)** -> `GENERAL` by default: nothing is being compared; it asks someone to *send* a document. This is the biggest ambiguity in the data. Score both ways (`--ask-send-as BL_COMPARISON` turns them into NEEDS_REVIEW/missing_attachment) and keep the better Stage-1 F1.
2. Confirmed difference + a blank elsewhere -> `MISMATCH` (blank fields are listed as still needing a person). Blank only -> `NEEDS_REVIEW/missing_value`. A blank is never treated as a difference.
3. Party comparison is on the **name** (first line). Addresses are ignored; the SI often omits them. `ON BEHALF OF` is compared only when both sides have it.
4. Born-digital text is compared exactly after normalisation (no fuzzy band): every real difference found was a whole-entity swap or a +/-1 container / +/-500-1000 kg change, and no formatting-only difference survived normalisation. Fuzzy tolerance applies to OCR only.
5. `unreadable` is also used for OCR-disagreement, since the allowed reason set has nothing closer.

## Observed on the sample bundle (self-checked, not scored)
520 emails: GENERAL 151, BL_COMPARISON 129, SI_REQUEST 125, INVOICE_QUERY 75, SPAM 40.
Comparisons: OK 66, MISMATCH 46 (72 differing fields), NEEDS_REVIEW 17 (5 wrong doc, 5 missing attachment, 2 unreadable, 5 blank values) — exactly the designed edge cases 501-520 minus the three scans that read cleanly.
Scanned pairs 512-514 were checked by eye against the page images: all three match.
**False-negative audit.** Of the 66 OK verdicts, only 8 fields had SI text differing from BL text at all: 2 thousands-separator formats (`243588` vs `243,588`) and 6 OCR artefacts on the three scanned emails, all verified against the page images. No normalisation rule is masking a real difference in born-digital documents.

`stress/` holds 10 deliberately broken emails (format-only differences, MT units, off-by-one container, deleted BL, swapped files, blank value, corrupt PDF, misleading subjects, one real defect among noise) — all classified as intended.
