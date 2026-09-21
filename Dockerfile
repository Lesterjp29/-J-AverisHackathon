FROM python:3.11-slim
RUN apt-get update && apt-get install -y poppler-utils tesseract-ocr && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
ENV PYTHONPATH=/app:/app/pipeline
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080"]

