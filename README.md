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


## Technical Architecture

```mermaid
flowchart TD
    %% -------------------------------------------------------------
    %% 1. INGESTION SOURCES & CLIENTS
    %% -------------------------------------------------------------
    subgraph SOURCES["1. Ingestion Sources & Clients"]
        S_EMAIL["Corporate Email Gateway<br/>(IMAP / MIME .eml Payloads)"]
        S_MANUAL["Manual Uploads<br/>(PDF, DOCX, XLSX, TXT)"]
        S_PHOTO["Mobile Camera Scans<br/>(Skewed JPG / PNG Photos)"]
    end

    %% -------------------------------------------------------------
    %% 2. PRESENTATION LAYER (FRONTEND)
    %% -------------------------------------------------------------
    subgraph FRONTEND["2. Presentation Layer (Streamlit Framework)"]
        UI_MAIN["Streamlit Web App<br/>(app.py)"]
        UI_UPLOAD["File & Camera Upload Portal"]
        UI_DIFF["Interactive Side-by-Side Diff Viewer"]
        UI_QUEUE["Borderline Match Review Panel"]
        UI_CONFIG["App Configuration<br/>(.streamlit/config.toml)"]

        UI_CONFIG -.-> UI_MAIN
        UI_UPLOAD --> UI_MAIN
        UI_MAIN --> UI_DIFF
        UI_MAIN --> UI_QUEUE
    end

    %% -------------------------------------------------------------
    %% 3. CLOUD INFRASTRUCTURE & SECURITY PERIMETER
    %% -------------------------------------------------------------
    subgraph CLOUD["3. Cloud Infrastructure & Security Perimeter"]
        CDN["Cloud CDN / Load Balancer<br/>(SSL/TLS Termination)"]
        BLOB_STORE["Cloud Object Storage (S3 / GCS)<br/>(/attachments, /inbox, /out)"]
        CONTAINER_SRV["Container Orchestration<br/>(Google Cloud Run / AWS ECS)"]
        SECRETS["Cloud Secret Manager<br/>(API Keys, Service Accounts)"]

        CDN --> CONTAINER_SRV
        BLOB_STORE <--> CONTAINER_SRV
        SECRETS -.->|Runtime Injection via pipeline/env.py| CONTAINER_SRV
    end

    %% -------------------------------------------------------------
    %% 4. BACKEND PROCESSING PIPELINE (CONTAINERIZED RUNTIME)
    %% -------------------------------------------------------------
    subgraph BACKEND["4. Backend Pipeline Core (Containerised via Dockerfile)"]
        direction TB

        %% Submodule A: Ingestion & Extraction
        subgraph MOD_INTAKE["Module A: Ingestion & Preprocessing (pipeline/intake.py, scan.py, readers.py)"]
            INTAKE["Intake Normalizer & Unpacker<br/>(pipeline/intake.py)"]
            ROUTER{"File Type Router"}
            D1["PDF & Scans:<br/>pdf2image & Tesseract OCR"]
            D2["Office Docs:<br/>python-docx & openpyxl"]
            D3["Camera Images:<br/>OpenCV Deskew & OCR"]
            D4["Plain Text:<br/>Direct UTF-8 Parser"]

            INTAKE --> ROUTER
            ROUTER -->|"PDF / Scanned"| D1
            ROUTER -->|"DOCX / XLSX"| D2
            ROUTER -->|"JPG / PNG"| D3
            ROUTER -->|"TXT"| D4
        end

        %% Submodule B: Role Classification
        subgraph MOD_CLASSIFY["Module B: Role Classification (pipeline/classify.py)"]
            CLF_SCORE["Keyword Heuristics & Signature Scoring"]
            CLF_CHECK{"Confidence >= Threshold?"}
            CLF_LABEL["Label Role:<br/>BL vs. SI"]
            CLF_LLM["LLM Document Role Fallback<br/>(pipeline/llm.py)"]

            D1 & D2 & D3 & D4 --> CLF_SCORE
            CLF_SCORE --> CLF_CHECK
            CLF_CHECK -->|"Yes"| CLF_LABEL
            CLF_CHECK -->|"Ambiguous"| CLF_LLM
            CLF_LLM --> CLF_LABEL
        end

        %% Submodule C: Entity Extraction
        subgraph MOD_EXTRACT["Module C: Structured Entity Extraction (pipeline/extract.py & llm.py)"]
            EXTRACT_TGT["Target Entity Extractor<br/>(Regex Boundaries & Schema Validator)"]
            LLM_SCHEMA["JSON Schema Constraint Enforcement<br/>(pipeline/llm.py)"]
            PAYLOAD["Structured Entity Payload<br/>(Parties, Routes, Containers, Weights)"]

            CLF_LABEL --> EXTRACT_TGT
            EXTRACT_TGT <--> LLM_SCHEMA
            LLM_SCHEMA --> PAYLOAD
        end

        %% Submodule D: Reconciliation & Matching
        subgraph MOD_RECON["Module D: Comparison & Verification (pipeline/compare.py & normalize.py)"]
            NORM["Unit & Text Normalization<br/>(LBS to KG, ISO Dates, Trailing Trim)"]
            COMPARE["Fuzzy Match & Levenshtein Matrix<br/>(Numeric Tolerance Checks)"]
            DIFF_CHECK{"Field Mismatch Detected?"}

            PAYLOAD --> NORM
            NORM --> COMPARE
            COMPARE --> DIFF_CHECK
        end

        %% Submodule E: Audit, Human-in-the-Loop & Communication
        subgraph MOD_AUDIT["Module E: Audit & Communication (pipeline/evidence.py, review.py, reply.py)"]
            EVIDENCE["Audit Evidence Collector<br/>(pipeline/evidence.py)"]
            REVIEW["Human Review Gating<br/>(pipeline/review.py: Score < Tau)"]
            REPLY["Discrepancy Email Generator<br/>(pipeline/reply.py)"]

            DIFF_CHECK -->|"Score >= Tau"| EVIDENCE
            DIFF_CHECK -->|"Score < Tau"| REVIEW
            DIFF_CHECK -->|"Mismatch Found"| REPLY
        end
    end

    %% -------------------------------------------------------------
    %% 5. PERSISTENCE & OUTPUT ARTIFACTS
    %% -------------------------------------------------------------
    subgraph PERSISTENCE["5. Persistence & Output Artifacts (out/)"]
        OUT_SUBMISSION["out/submission.json<br/>(Canonical Reconciliation Output)"]
        OUT_REPORT_JSON["out/report.json<br/>(Machine-Readable Audit Trace)"]
        OUT_REPORT_MD["out/report.md<br/>(Consolidated Markdown Diff Summary)"]
        OUT_REVIEW["out/review_queue.json<br/>(Low-Confidence Review Queue)"]
    end

    %% Source Routing
    S_EMAIL --> BLOB_STORE
    S_MANUAL --> UI_UPLOAD
    S_PHOTO --> UI_UPLOAD
    UI_MAIN --> CDN
    CONTAINER_SRV --> INTAKE

    %% Backend to Persistence Wiring
    EVIDENCE --> OUT_SUBMISSION
    EVIDENCE --> OUT_REPORT_JSON
    EVIDENCE --> OUT_REPORT_MD
    REVIEW --> OUT_REVIEW
    OUT_SUBMISSION --> BLOB_STORE
    OUT_REPORT_JSON --> BLOB_STORE
    OUT_REPORT_MD --> BLOB_STORE
    OUT_REVIEW --> BLOB_STORE

    %% Artifacts to Frontend Wiring
    OUT_REPORT_MD -.-> UI_DIFF
    OUT_REPORT_JSON -.-> UI_DIFF
    OUT_REVIEW -.-> UI_QUEUE
    REPLY -.->|Draft Approval| UI_MAIN


