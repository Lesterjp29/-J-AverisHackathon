# Shipping document checker - everything (Python, Tesseract OCR, poppler) is inside; nothing to install on your machine.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_THREAD_LIMIT=1 \
    SDOC_DOCKER=1 \
    SDOC_DATA=/data \
    SDOC_OUT=/out

# poppler-utils = PDF text + page images, tesseract = OCR for scans and phone photos (osd = detects sideways pages)
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-streamlit.txt requirements-extras.txt ./
RUN pip install -r requirements-streamlit.txt \
    && (pip install -r requirements-extras.txt || echo "optional extras (HEIC photos, .msg email) not installed")

COPY . .
RUN mkdir -p /data /out

EXPOSE 8501
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
