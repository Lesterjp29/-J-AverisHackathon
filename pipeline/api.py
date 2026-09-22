"""Optional Google Cloud Storage API. Run with uvicorn pipeline.api:app."""
from fastapi import FastAPI, HTTPException
import subprocess
import json
import os
import sys
from google.cloud import storage
from .paths import ROOT, SAMPLE_DATA, DEFAULT_OUTPUT

app = FastAPI()


def results_bucket():
    name = os.environ.get("RESULTS_BUCKET")
    if not name:
        raise HTTPException(status_code=503, detail="Set RESULTS_BUCKET to your Google Cloud Storage bucket.")
    return storage.Client().bucket(name)


@app.post('/run')
def run_pipeline():
    bucket = results_bucket()
    subprocess.run([sys.executable, '-m', 'pipeline.run',
                   str(SAMPLE_DATA), '--out', str(DEFAULT_OUTPUT)], cwd=ROOT, check=True)
    blob = bucket.blob('submission.json')
    blob.upload_from_filename(str(DEFAULT_OUTPUT / 'submission.json'))
    return {'status': 'done'}


@app.get('/results')
def get_results():
    bucket = results_bucket()
    blob = bucket.blob('submission.json')
    data = blob.download_as_text()
    return json.loads(data)
