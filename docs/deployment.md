# Deployment Notes

## Local Dashboard

From the repository root, install `requirements.txt` and the system tools in `packages.txt`, then run:

```bash
python -m streamlit run app.py
```

`app.py` stays at the root so existing Streamlit entry-point settings remain valid. `.streamlit/config.toml` is the shared theme; private Streamlit secrets remain ignored by Git.

## Optional Cloud Storage API

`pipeline/api.py` exposes:

| Endpoint | Behavior |
| --- | --- |
| `POST /run` | Runs `data/sample`, writes local reports, then uploads `submission.json` to the configured bucket |
| `GET /results` | Downloads and returns that bucket's `submission.json` |

Install the optional dependencies:

```bash
python -m pip install -r requirements-api.txt
```

Set `RESULTS_BUCKET` to a bucket you control. Provide Google Cloud Application Default Credentials through your local environment or hosting identity, with permission to read/write the intended objects. The API reads `RESULTS_BUCKET` from the process environment; copying `.env.example` alone does not configure this variable for Uvicorn.

```bash
python -m uvicorn pipeline.api:app --host 127.0.0.1 --port 8080
```

The API does not provide the `/emails`, attachment-download, or `/submit` endpoints expected by the organizer's HTTP dataset adapter. It is not a scoring server.

## Container

```bash
docker build -t docuverify-api .
docker run --rm -p 127.0.0.1:8080:8080 --env RESULTS_BUCKET=your-bucket docuverify-api
```

This starts the API; cloud operations additionally require credentials available inside the container. Supply those through the deployment environment rather than baking them into the image. `.dockerignore` excludes local secrets, virtual environments, reviewer decisions, and generated outputs.

## Current Limits

No live deployment has been verified. The service processes a fixed bundled dataset synchronously, writes to a shared output directory and object name, and has no authentication or job queue. Keep it local/private until access control, separate job storage, concurrency management, and operational monitoring are implemented. A successful image build or local API start is not a production-readiness claim.
