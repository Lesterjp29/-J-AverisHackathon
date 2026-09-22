# Cloud Architecture Proposal

These diagrams preserve the architecture contribution merged from GitHub (`fdff8fe`). They are a design proposal, not a verified deployment or an exact map of current implementation.

The following distinctions apply:

- IMAP ingestion, S3 support, Secret Manager integration, and Cloud Run/ECS deployment have not been implemented or verified in this repository.
- The Streamlit app currently invokes the Python pipeline directly; it does not send uploads to the optional Docker API.
- The existing API uploads submission results to Google Cloud Storage. A shared input object-storage workflow is proposed.
- `/out` and `out/...` in the diagrams refer to historical paths; generated reports now belong under `outputs/sample/`.
- Current PDF readers use Poppler command-line tools and Tesseract; the diagrams' `pdf2image` label is illustrative.
- Document-role detection, email classification, extraction, and deterministic comparison are separate stages. The depicted universal confidence cutoff of 0.82, ISO-date normalization, and JSON-schema enforcement do not describe the current comparison implementation.

See [the README](../README.md#how-it-works) for the implemented workflow and [deployment notes](deployment.md) for the API's current behavior and limitations.

## Technical Architecture

### 1. System Topology & Infrastructure
```mermaid
graph LR
    subgraph Clients["Clients & Ingestion"]
        A1["Email (IMAP)"]
        A2["Web Uploads"]
        A3["Mobile Photos"]
        UI["Streamlit Portal<br/>(app.py)"]
    end

    subgraph Cloud["Cloud Infrastructure (Docker)"]
        GCS[("Object Storage<br/>S3 / GCS")]
        DOCKER["Docker Microservice<br/>(Cloud Run / ECS)<br/><i>Tesseract & Poppler</i>"]
        SECRETS["Secret Manager"]
    end

    subgraph Outputs["Persistence (/out)"]
        O1[("submission.json")]
        O2[("report.json / .md")]
        O3[("review_queue.json")]
    end

    A1 --> GCS
    A2 & A3 --> UI --> DOCKER
    GCS <--> DOCKER
    SECRETS -.-> DOCKER
    DOCKER --> O1 & O2 & O3
    O2 & O3 -.-> UI
```

### 2. Document Processing & Audit Pipeline
```mermaid
flowchart TD
    IN["Document Input (pipeline/intake.py)"] --> ROUTE{"File Type?"}
    
    ROUTE -->|"PDF / Scans"| R1["pdf2image & Tesseract OCR"]
    ROUTE -->|"Office Docs"| R2["python-docx & openpyxl"]
    ROUTE -->|"JPG / PNG"| R3["OpenCV Deskew & OCR"]
    ROUTE -->|"Plain Text"| R4["Direct UTF-8 Reader"]
    
    R1 & R2 & R3 & R4 --> CLF["Classifier (pipeline/classify.py)<br/>BL vs. SI"]
    CLF --> EXT["Entity Extraction (pipeline/extract.py)<br/>LLM + JSON Schema Enforcement"]
    EXT --> NORM["Normalization & Diff (pipeline/compare.py)<br/>LBS to KG, ISO Dates, Levenshtein"]
    
    NORM --> CHECK{"Confidence Score?"}
    CHECK -->|">= 0.82"| OK["Audit Trail (out/report.json & submission.json)"]
    CHECK -->|"< 0.82"| REVIEW["Human Queue (out/review_queue.json)"]
    CHECK -->|"Mismatch"| REPLY["Client Reply Generator (pipeline/reply.py)"]
```


