from fastapi import FastAPI
import subprocess
import json
from google.cloud import storage
app = FastAPI()
BUCKET_NAME = 'averis-results-project-bdbb1403'


@app.post('/run')
def run_pipeline():
    subprocess.run(['python', '-m', 'pipeline.run',
                   '.', '--out', 'out', '--submit'])
    client = storage.Client()
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob('submission.json')
    blob.upload_from_filename('out/submission.json')
    return {'status': 'done'}


@app.get('/results')
def get_results():
    client = storage.Client()
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob('submission.json')
    data = blob.download_as_text()
    return json.loads(data)
